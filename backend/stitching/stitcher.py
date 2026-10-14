import math
import time
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

from backend.config import CameraConfig, SkyViewConfig, StitchTargetProjection
from backend.stitching.warp_lut import (
    build_normalized_weight_maps,
    compute_camera_remap_lut,
    generate_target_world_grid,
)
from backend.tracking.ntp_client import NTPClient


class CameraLUTData:
    def __init__(self, camera_id: str, map_x: np.ndarray, map_y: np.ndarray, weight_map: np.ndarray):
        self.camera_id = camera_id
        self.map_x = map_x
        self.map_y = map_y
        self.weight_map = weight_map[:, :, np.newaxis]  # shape (H, W, 1) for fast broadcasting


class MultiCameraStitcher:
    """
    High-performance multi-camera stitching engine for SkyView.
    Uses precomputed cv2.remap Coordinate Lookup Tables (LUTs) and distance-transform
    feathering weights for real-time blending.
    """

    def __init__(self, config: SkyViewConfig):
        self.config = config
        self._lut_cache: Dict[str, CameraLUTData] = {}
        self._cached_canvas_size: int = 0
        self._cached_projection: str = ""
        self._valid_canvas_mask: Optional[np.ndarray] = None
        self._camera_dims: Dict[str, Tuple[int, int]] = {}
        self.ntp_client = NTPClient(server=config.stitching.ntp_server)
        if config.stitching.ntp_enabled:
            try:
                self.ntp_client.sync()
            except Exception:
                pass

    def invalidate_lut_cache(self) -> None:
        """Clears precomputed lookup tables so they will be recomputed with new settings."""
        self._lut_cache.clear()
        self._valid_canvas_mask = None

    def update_config(self, config: SkyViewConfig) -> None:
        self.config = config
        self.invalidate_lut_cache()

    def ensure_luts(self, frame_dimensions: Dict[str, Tuple[int, int]]) -> None:
        """
        Builds LUTs for all enabled cameras if not cached or if dimensions/config changed.
        frame_dimensions: dict mapping camera_id -> (width, height)
        """
        stitching_cfg = self.config.stitching
        need_rebuild = (
            len(self._lut_cache) == 0
            or self._cached_canvas_size != stitching_cfg.canvas_size
            or self._cached_projection != stitching_cfg.projection.value
        )

        for cam_id, dims in frame_dimensions.items():
            if self._camera_dims.get(cam_id) != dims:
                need_rebuild = True
                break

        if not need_rebuild:
            return

        self._camera_dims = frame_dimensions.copy()
        self._cached_canvas_size = stitching_cfg.canvas_size
        self._cached_projection = stitching_cfg.projection.value

        # 1. Compute target canvas topocentric world coordinate grid
        grid_vx, grid_vy, grid_vz, valid_mask = generate_target_world_grid(stitching_cfg)
        self._valid_canvas_mask = valid_mask

        # 2. Compute remap coordinates and coverage mask for each enabled camera
        active_cams = [c for c in self.config.cameras if c.enabled]
        raw_maps = []
        valid_masks = []

        for cam in active_cams:
            dims = frame_dimensions.get(cam.id, (1920, 1080))
            map_x, map_y, c_mask = compute_camera_remap_lut(
                cam, dims[0], dims[1], grid_vx, grid_vy, grid_vz, valid_mask
            )
            raw_maps.append((cam.id, map_x, map_y))
            valid_masks.append(c_mask)

        # 3. Compute normalized blended weight maps
        normalized_weights = build_normalized_weight_maps(
            valid_masks, feather_pixels=stitching_cfg.feather_pixels
        )

        # 4. Store in cache
        self._lut_cache.clear()
        for (cam_id, map_x, map_y), weight in zip(raw_maps, normalized_weights):
            self._lut_cache[cam_id] = CameraLUTData(cam_id, map_x, map_y, weight)

    def stitch_frames(
        self,
        camera_frames: Dict[str, np.ndarray],
        overlay_annotations: bool = False
    ) -> np.ndarray:
        """
        Stitches a dictionary of camera frames {cam_id: bgr_image} into a unified allsky canvas.
        """
        if not camera_frames:
            size = self.config.stitching.canvas_size
            return np.zeros((size, size, 3), dtype=np.uint8)

        # Extract dimensions
        dims = {cam_id: (frame.shape[1], frame.shape[0]) for cam_id, frame in camera_frames.items()}
        self.ensure_luts(dims)

        out_size = self.config.stitching.canvas_size
        out_h = out_size if self.config.stitching.projection == StitchTargetProjection.ZENITHAL_POLAR else max(300, out_size // 2)
        out_w = out_size

        # Accumulator canvas (float32 for precise blending)
        accumulator = np.zeros((out_h, out_w, 3), dtype=np.float32)

        cam_config_map = {c.id: c for c in self.config.cameras}

        for cam_id, frame in camera_frames.items():
            if cam_id not in self._lut_cache:
                continue

            lut = self._lut_cache[cam_id]
            cam_cfg = cam_config_map.get(cam_id)
            gain = cam_cfg.gain_scale if cam_cfg else 1.0

            # Fast remap
            warped = cv2.remap(
                frame,
                lut.map_x,
                lut.map_y,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0)
            )

            # Apply gain and accumulate with weight
            if gain != 1.0:
                warped_float = warped.astype(np.float32) * gain
            else:
                warped_float = warped.astype(np.float32)

            accumulator += warped_float * lut.weight_map

        # Clip and convert to uint8
        composite = np.clip(accumulator, 0.0, 255.0).astype(np.uint8)

        # Apply circular dome clipping if polar projection
        if (
            self.config.stitching.projection == StitchTargetProjection.ZENITHAL_POLAR
            and self._valid_canvas_mask is not None
        ):
            composite[~self._valid_canvas_mask] = 0

        if overlay_annotations:
            composite = self.render_dome_annotations(composite)

        return composite

    def render_dome_annotations(self, image: np.ndarray) -> np.ndarray:
        """Draws cardinal direction labels, altitude rings, and zenith crosshair onto polar dome."""
        if self.config.stitching.projection != StitchTargetProjection.ZENITHAL_POLAR:
            return image

        annotated = image.copy()
        h, w = annotated.shape[:2]
        cx, cy = int(w / 2), int(h / 2)
        r_max = int(min(w, h) / 2 * self.config.stitching.horizon_mask_radius)

        # Altitude grid circles (30 deg, 60 deg, Horizon 0 deg)
        cv2.circle(annotated, (cx, cy), r_max, (40, 100, 40), 1, cv2.LINE_AA)  # Horizon
        cv2.circle(annotated, (cx, cy), int(r_max * 2.0 / 3.0), (30, 70, 30), 1, cv2.LINE_AA)  # 30 deg alt
        cv2.circle(annotated, (cx, cy), int(r_max * 1.0 / 3.0), (20, 50, 20), 1, cv2.LINE_AA)  # 60 deg alt

        # Zenith center crosshair
        cv2.drawMarker(annotated, (cx, cy), (60, 150, 60), cv2.MARKER_CROSS, 16, 1, cv2.LINE_AA)

        # Compass cardinal points
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.55
        color = (100, 220, 100)
        thickness = 1

        # North (top)
        cv2.putText(annotated, "N", (cx - 7, cy - r_max + 22), font, scale, color, thickness, cv2.LINE_AA)
        # East (right)
        cv2.putText(annotated, "E", (cx + r_max - 25, cy + 6), font, scale, color, thickness, cv2.LINE_AA)
        # South (bottom)
        cv2.putText(annotated, "S", (cx - 6, cy + r_max - 12), font, scale, color, thickness, cv2.LINE_AA)
        # West (left)
        cv2.putText(annotated, "W", (cx - r_max + 12, cy + 6), font, scale, color, thickness, cv2.LINE_AA)

        # Render NTP-synchronized timestamp overlay if enabled
        if getattr(self.config.stitching, "overlay_timestamp", True):
            time_fmt = getattr(self.config.stitching, "time_format", "zulu")
            tz_str = getattr(self.config.observer, "timezone_str", "UTC")
            ts_str = self.ntp_client.format_timestamp(time_fmt, tz_str)

            # Draw semi-transparent pill / badge at bottom-left
            t_scale = 0.50
            t_thick = 1
            t_font = cv2.FONT_HERSHEY_SIMPLEX
            (text_w, text_h), baseline = cv2.getTextSize(ts_str, t_font, t_scale, t_thick)
            
            # Bottom-left margin
            px = 16
            py = h - 16
            
            # Background badge
            cv2.rectangle(
                annotated,
                (px - 6, py - text_h - 6),
                (px + text_w + 6, py + baseline + 4),
                (10, 15, 25),
                cv2.FILLED
            )
            cv2.rectangle(
                annotated,
                (px - 6, py - text_h - 6),
                (px + text_w + 6, py + baseline + 4),
                (45, 60, 80),
                1,
                cv2.LINE_AA
            )
            # Text
            cv2.putText(annotated, ts_str, (px, py), t_font, t_scale, (200, 240, 255), t_thick, cv2.LINE_AA)

        return annotated
