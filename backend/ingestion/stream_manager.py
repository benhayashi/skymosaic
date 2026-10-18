import datetime
import math
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
import cv2
import numpy as np

from backend.astrometry.camera_model import (
    build_camera_rotation_matrix,
    project_world_point_to_camera,
)
from backend.astrometry.celestial import (
    BRIGHT_STARS,
    get_lst_deg,
    radec_to_altaz,
    altaz_to_unit_vector,
)
from backend.config import CameraConfig, CameraSourceType, SkyViewConfig


class SyntheticSkyCamera:
    """
    Generates realistic synthetic night sky frames for testing and demonstration.
    Renders catalog stars projected according to observer lat/lon, capture time,
    camera orientation (Az/Alt/Roll), and lens distortion.
    """

    def __init__(self, camera_cfg: CameraConfig, observer_lat: float, observer_lon: float):
        self.camera_cfg = camera_cfg
        self.lat = observer_lat
        self.lon = observer_lon
        self.width = 1280
        self.height = 720
        self._rng = np.random.default_rng(seed=hash(camera_cfg.id) % 2**32)

        # Pre-generate some random faint background stars
        self.faint_stars_ra = self._rng.uniform(0.0, 360.0, 300)
        self.faint_stars_dec = self._rng.uniform(-20.0, 85.0, 300)
        self.faint_stars_mag = self._rng.uniform(3.5, 6.0, 300)

    def render_frame(self, dt: Optional[datetime.datetime] = None) -> np.ndarray:
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)

        w, h = self.width, self.height
        frame = np.zeros((h, w, 3), dtype=np.uint8)

        # 1. Subtle deep-night sky background gradient (dark navy blue at zenith to dark slate at horizon)
        # Background noise
        noise = self._rng.normal(6.0, 2.5, (h, w, 3)).clip(0, 255).astype(np.uint8)
        frame = cv2.add(frame, noise)

        # 2. Celestial star projections
        R_w2c = build_camera_rotation_matrix(self.camera_cfg.extrinsics)
        intr = self.camera_cfg.intrinsics
        lst_deg = get_lst_deg(dt, self.lon)

        # Render catalog bright stars
        for star in BRIGHT_STARS:
            alt, az = radec_to_altaz(star["ra"], star["dec"], self.lat, lst_deg)
            if alt < -1.0:
                continue

            v_world = altaz_to_unit_vector(alt, az)
            proj = project_world_point_to_camera(v_world, R_w2c, intr, w, h)
            if proj is None:
                continue

            px, py = proj
            if 0 <= px < w and 0 <= py < h:
                # Apparent brightness and radius based on magnitude
                mag = star["mag"]
                intensity = int(max(40, min(255, 255 - (mag + 1.5) * 45)))
                radius = 1 if mag > 2.0 else (2 if mag > 0.5 else 3)

                color = (intensity, intensity, min(255, intensity + 15))
                cv2.circle(frame, (int(round(px)), int(round(py))), radius, color, -1, cv2.LINE_AA)
                if mag < 1.0:  # Mild star diffraction glow for primary stars
                    cv2.circle(frame, (int(round(px)), int(round(py))), radius + 2, (intensity // 4, intensity // 4, intensity // 4), 1, cv2.LINE_AA)

        # Render faint background stars
        for ra, dec, mag in zip(self.faint_stars_ra, self.faint_stars_dec, self.faint_stars_mag):
            alt, az = radec_to_altaz(ra, dec, self.lat, lst_deg)
            if alt < 0.0:
                continue
            v_world = altaz_to_unit_vector(alt, az)
            proj = project_world_point_to_camera(v_world, R_w2c, intr, w, h)
            if proj is not None:
                px, py = proj
                if 0 <= px < w and 0 <= py < h:
                    intensity = int(max(20, min(120, 180 - mag * 25)))
                    frame[int(py), int(px)] = (intensity, intensity, intensity)

        # 3. Simulated horizon silhouette (ground / roofline at bottom of frame)
        # Horizon contour based on camera altitude
        cam_alt = self.camera_cfg.extrinsics.altitude_deg
        # If camera points low, ground is prominent; if high, ground is minimal
        horizon_y = int(h * (0.5 + math.tan(math.radians(max(-30, min(60, cam_alt)))) * 0.45))
        if horizon_y < h:
            # Draw ground silhouette
            ground_pts = np.array([
                [0, h],
                [0, max(0, horizon_y)],
                [int(w * 0.3), max(0, horizon_y - 20)],  # simulated tree/roof peak
                [int(w * 0.7), max(0, horizon_y + 10)],
                [w, max(0, horizon_y - 10)],
                [w, h]
            ], dtype=np.int32)
            cv2.fillPoly(frame, [ground_pts], (4, 4, 4))
            # Ground ambient sodium lighting glow on edge
            cv2.polylines(frame, [ground_pts[1:-1]], False, (10, 15, 25), 2, cv2.LINE_AA)

        return frame


class CameraStreamHandler:
    """Manages acquisition of frames from a single camera source."""

    def __init__(self, camera_cfg: CameraConfig, observer_lat: float, observer_lon: float):
        self.camera_cfg = camera_cfg
        self.lat = observer_lat
        self.lon = observer_lon
        self.latest_frame: Optional[np.ndarray] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._synthetic_sim: Optional[SyntheticSkyCamera] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        if camera_cfg.source_type == CameraSourceType.SYNTHETIC:
            self._synthetic_sim = SyntheticSkyCamera(camera_cfg, observer_lat, observer_lon)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _open_stream(self) -> bool:
        if self.camera_cfg.source_type == CameraSourceType.RTSP:
            url = self.camera_cfg.url_or_path
            if not url:
                return False
            # Prevent multithreaded ffmpeg pthread_frame race condition on high-framerate RTSP
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|threads;1|fflags;nobuffer|flags;low_delay"
            self._cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            return self._cap.isOpened()
        elif self.camera_cfg.source_type == CameraSourceType.USB:
            dev_path = self.camera_cfg.url_or_path or "0"
            dev_idx = int(dev_path) if dev_path.isdigit() else dev_path
            self._cap = cv2.VideoCapture(dev_idx)
            return self._cap.isOpened()
        return True

    def _apply_flips(self, frame: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if frame is None:
            return None
        flip_h = getattr(self.camera_cfg, "flip_horizontal", False)
        flip_v = getattr(self.camera_cfg, "flip_vertical", False)
        if flip_h and flip_v:
            return cv2.flip(frame, -1)
        elif flip_h:
            return cv2.flip(frame, 1)
        elif flip_v:
            return cv2.flip(frame, 0)
        return frame

    def _capture_loop(self) -> None:
        try:
            while self._running:
                try:
                    if self.camera_cfg.source_type == CameraSourceType.SYNTHETIC:
                        sim_frame = self._synthetic_sim.render_frame()
                        with self._lock:
                            self.latest_frame = sim_frame
                        time.sleep(0.1)  # ~10 FPS for simulation

                    elif self.camera_cfg.source_type == CameraSourceType.DIRECTORY:
                        folder = Path(self.camera_cfg.url_or_path)
                        if folder.is_dir():
                            exts = ("*.jpg", "*.jpeg", "*.png", "*.tiff", "*.fits")
                            files = []
                            for ext in exts:
                                files.extend(folder.glob(ext))
                            if files:
                                latest_file = max(files, key=lambda f: f.stat().st_mtime)
                                img = cv2.imread(str(latest_file))
                                if img is not None:
                                    with self._lock:
                                        self.latest_frame = img
                        time.sleep(1.0)

                    elif self.camera_cfg.source_type in (CameraSourceType.RTSP, CameraSourceType.USB):
                        if self._cap is None or not self._cap.isOpened():
                            if not self._open_stream():
                                time.sleep(3.0)
                                continue

                        ret, frame = self._cap.read()
                        if ret and frame is not None:
                            with self._lock:
                                self.latest_frame = frame
                        else:
                            time.sleep(1.0)
                            if self._cap:
                                self._cap.release()
                                self._cap = None

                except Exception as e:
                    print(f"Error capturing from camera {self.camera_cfg.id}: {e}")
                    time.sleep(2.0)
        finally:
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

    def get_frame(self, apply_flips: bool = False) -> Optional[np.ndarray]:
        with self._lock:
            frame = None
            if self.latest_frame is not None:
                frame = self.latest_frame.copy()
            elif self._synthetic_sim is not None:
                frame = self._synthetic_sim.render_frame()
                self.latest_frame = frame
                frame = frame.copy()
            if frame is not None and apply_flips:
                frame = self._apply_flips(frame)
            return frame


class StreamManager:
    """Manages all camera sources and provides synchronized frame grabs."""

    def __init__(self, config: SkyViewConfig):
        self.config = config
        self.handlers: Dict[str, CameraStreamHandler] = {}
        self._init_handlers()

    def _init_handlers(self) -> None:
        self.stop_all()
        self.handlers.clear()
        for cam in self.config.cameras:
            handler = CameraStreamHandler(cam, self.config.observer.latitude, self.config.observer.longitude)
            self.handlers[cam.id] = handler
            if cam.enabled:
                handler.start()

    def update_config(self, config: SkyViewConfig) -> None:
        self.config = config
        current_cam_ids = {c.id: c for c in config.cameras}

        # Stop and remove handlers for cameras no longer present
        for cid in list(self.handlers.keys()):
            if cid not in current_cam_ids:
                self.handlers[cid].stop()
                del self.handlers[cid]

        # Update existing or add new handlers
        for cam in config.cameras:
            existing = self.handlers.get(cam.id)
            if existing and existing.camera_cfg.source_type == cam.source_type and existing.camera_cfg.url_or_path == cam.url_or_path:
                # In-place parameter update without restarting RTSP/hardware stream
                existing.camera_cfg = cam
                existing.lat = config.observer.latitude
                existing.lon = config.observer.longitude
                if existing._synthetic_sim is not None:
                    existing._synthetic_sim.camera_cfg = cam
                    existing._synthetic_sim.lat = config.observer.latitude
                    existing._synthetic_sim.lon = config.observer.longitude
                if cam.enabled and not existing._running:
                    existing.start()
                elif not cam.enabled and existing._running:
                    existing.stop()
            else:
                if existing:
                    existing.stop()
                handler = CameraStreamHandler(cam, config.observer.latitude, config.observer.longitude)
                self.handlers[cam.id] = handler
                if cam.enabled:
                    handler.start()

    def stop_all(self) -> None:
        for handler in self.handlers.values():
            handler.stop()

    def get_camera_frame(self, camera_id: str, apply_flips: bool = False) -> Optional[np.ndarray]:
        handler = self.handlers.get(camera_id)
        if handler:
            return handler.get_frame(apply_flips=apply_flips)
        return None

    def get_all_active_frames(self) -> Dict[str, np.ndarray]:
        frames = {}
        for cam in self.config.cameras:
            if not cam.enabled:
                continue
            handler = self.handlers.get(cam.id)
            if handler:
                frame = handler.get_frame()
                if frame is not None:
                    frames[cam.id] = frame
        return frames

    def save_test_shot(self, camera_id: str, output_dir: str = "data/test_shots") -> Optional[str]:
        """Captures a test shot from the given camera and saves it to disk."""
        frame = self.get_camera_frame(camera_id)
        if frame is None:
            return None

        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{camera_id}_{timestamp_str}.jpg"
        full_path = out_path / filename

        cv2.imwrite(str(full_path), frame)
        return str(full_path)
