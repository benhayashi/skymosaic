import math
from typing import Any, Dict, List, Optional, Tuple, Union
import cv2
import numpy as np

from backend.astrometry.camera_model import (
    build_camera_rotation_matrix,
    get_focal_length_px,
)
from backend.config import (
    CameraConfig,
    LensModelType,
    StitchTargetProjection,
    StitchingConfig,
)


def normalize_polygon_list(poly_input: Any) -> List[List[List[float]]]:
    """
    Normalizes either a single polygon [[x,y],...] or multiple polygons [[[x,y],...],...]
    into a standardized list of polygons [[[x,y],...], ...].
    """
    if not poly_input:
        return []
    if isinstance(poly_input, (list, tuple)) and len(poly_input) > 0:
        first = poly_input[0]
        if isinstance(first, (list, tuple)) and len(first) > 0:
            if isinstance(first[0], (int, float)):
                # Single polygon [[x, y], [x, y], ...]
                return [poly_input]
            elif isinstance(first[0], (list, tuple)):
                # List of polygons [[[x, y], ...], ...]
                return poly_input
    return []


def point_in_polygon(px: float, py: float, poly: Any, img_w: int, img_h: int) -> bool:
    """Checks if pixel (px, py) is inside normalized polygon vertices."""
    polys = normalize_polygon_list(poly)
    for p in polys:
        if len(p) >= 3:
            poly_pts = np.array([[pt[0] * img_w, pt[1] * img_h] for pt in p], dtype=np.int32)
            if cv2.pointPolygonTest(poly_pts, (px, py), False) >= 0:
                return True
    return False


def generate_camera_polygon_mask(poly: Any, img_w: int, img_h: int) -> np.ndarray:
    """
    Creates a binary mask (uint8) where 255 = valid sky, 0 = masked obstacle (ground, tree, pole, roof).
    Supports either a single polygon [[x, y], ...] or multiple polygons [[[x, y], ...], ...].
    """
    mask = np.full((img_h, img_w), 255, dtype=np.uint8)
    polys = normalize_polygon_list(poly)
    for p in polys:
        if len(p) >= 3:
            pts = np.array([[int(round(pt[0] * img_w)), int(round(pt[1] * img_h))] for pt in p], dtype=np.int32)
            # Fill masked obstacle region with 0
            cv2.fillPoly(mask, [pts], 0)
    return mask


def generate_target_world_grid(
    stitching_cfg: StitchingConfig
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Computes topocentric unit vector grid (X=East, Y=North, Z=Up) and valid dome mask
    for the target stitched canvas.
    
    Returns:
        (grid_vx, grid_vy, grid_vz, valid_canvas_mask)
        All shapes: (canvas_h, canvas_w)
    """
    size = stitching_cfg.canvas_size

    if stitching_cfg.projection == StitchTargetProjection.ZENITHAL_POLAR:
        w = size
        h = size
        u_coords = np.arange(w, dtype=np.float32)
        v_coords = np.arange(h, dtype=np.float32)
        uu, vv = np.meshgrid(u_coords, v_coords)

        u0 = (w - 1) / 2.0
        v0 = (h - 1) / 2.0
        r_max = (min(w, h) / 2.0)

        du = uu - u0
        dv = vv - v0
        dist = np.hypot(du, dv)

        # Valid dome mask
        max_valid_r = r_max * stitching_cfg.horizon_mask_radius
        valid_mask = dist <= max_valid_r

        # Zenith angle theta: 0 at center, pi/2 (90 deg) at r_max
        theta = (dist / r_max) * (np.pi / 2.0)
        # Altitude: pi/2 at center, 0 at horizon
        alt = (np.pi / 2.0) - theta
        # Azimuth: North (Az=0) is top (dv < 0), East (Az=90 deg) is right (du > 0)
        az = np.arctan2(du, -dv) % (2.0 * np.pi)

        cos_alt = np.cos(alt)
        sin_alt = np.sin(alt)
        vx = cos_alt * np.sin(az)  # East
        vy = cos_alt * np.cos(az)  # North
        vz = sin_alt               # Up

        # Zero out invalid regions
        vx[~valid_mask] = 0.0
        vy[~valid_mask] = 0.0
        vz[~valid_mask] = 0.0

        return vx, vy, vz, valid_mask

    else:
        # Equirectangular: 360 deg horizontal x 90 deg vertical
        w = size
        h = max(300, size // 2)
        u_coords = np.arange(w, dtype=np.float32)
        v_coords = np.arange(h, dtype=np.float32)
        uu, vv = np.meshgrid(u_coords, v_coords)

        # Azimuth: 0 to 360 deg
        az = (uu / w) * (2.0 * np.pi)
        # Altitude: 90 deg at top (v=0) to 0 deg at bottom (v=h-1)
        alt = (1.0 - (vv / (h - 1))) * (np.pi / 2.0)

        cos_alt = np.cos(alt)
        sin_alt = np.sin(alt)
        vx = cos_alt * np.sin(az)
        vy = cos_alt * np.cos(az)
        vz = sin_alt
        valid_mask = np.ones((h, w), dtype=bool)

        return vx, vy, vz, valid_mask


def compute_camera_remap_lut(
    camera_cfg: CameraConfig,
    img_w: int,
    img_h: int,
    grid_vx: np.ndarray,
    grid_vy: np.ndarray,
    grid_vz: np.ndarray,
    valid_canvas_mask: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Computes map_x, map_y (float32) for cv2.remap and a binary coverage mask for a single camera.
    Returns:
        (map_x, map_y, cam_valid_mask)
    """
    out_h, out_w = grid_vx.shape

    # Pre-allocate remap arrays with -1.0
    map_x = np.full((out_h, out_w), -1.0, dtype=np.float32)
    map_y = np.full((out_h, out_w), -1.0, dtype=np.float32)
    cam_valid = np.zeros((out_h, out_w), dtype=np.uint8)

    if not camera_cfg.enabled:
        return map_x, map_y, cam_valid

    R_w2c = build_camera_rotation_matrix(camera_cfg.extrinsics)
    intrinsics = camera_cfg.intrinsics

    f = get_focal_length_px(intrinsics, img_w, img_h)
    cx = (img_w / 2.0) + intrinsics.center_offset_x * img_w
    cy = (img_h / 2.0) + intrinsics.center_offset_y * img_h

    # Only process pixels on the valid canvas
    valid_indices = np.where(valid_canvas_mask)
    if len(valid_indices[0]) == 0:
        return map_x, map_y, cam_valid

    vx_val = grid_vx[valid_indices]
    vy_val = grid_vy[valid_indices]
    vz_val = grid_vz[valid_indices]

    # Rotate topocentric world vectors to camera frame: p_cam = R_w2c @ [vx, vy, vz]
    # R_w2c shape (3, 3)
    # world vectors shape (3, N)
    v_world_stacked = np.vstack([vx_val, vy_val, vz_val])
    p_cam = R_w2c @ v_world_stacked  # shape (3, N)

    xc = p_cam[0, :]
    yc = p_cam[1, :]
    zc = p_cam[2, :]

    norm_xy = np.hypot(xc, yc)
    theta = np.arctan2(norm_xy, zc)
    phi = np.arctan2(yc, xc)

    model = intrinsics.lens_model

    if model == LensModelType.RECTILINEAR:
        # Behind camera or beyond FOV
        in_front = (zc > 0) & (theta < np.radians(89.0))
        r_u = np.tan(theta)
        r2 = r_u * r_u
        r4 = r2 * r2
        radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * r4
        x_norm = (xc / np.maximum(zc, 1e-6)) * radial
        y_norm = (yc / np.maximum(zc, 1e-6)) * radial
        px = cx + f * x_norm
        py = cy + f * y_norm
        valid_pts = in_front

    elif model == LensModelType.FISHEYE_EQUIDISTANT:
        r_d = theta
        px = cx + f * r_d * np.cos(phi)
        py = cy + f * r_d * np.sin(phi)
        valid_pts = (theta <= np.radians(intrinsics.fov_deg / 2.0 + 5.0))

    elif model == LensModelType.FISHEYE_EQUISOLID:
        r_d = 2.0 * np.sin(theta / 2.0)
        px = cx + f * r_d * np.cos(phi)
        py = cy + f * r_d * np.sin(phi)
        valid_pts = (theta <= np.radians(intrinsics.fov_deg / 2.0 + 5.0))

    elif model == LensModelType.FISHEYE_STEREOGRAPHIC:
        valid_pts = (theta < np.radians(170.0)) & (theta <= np.radians(intrinsics.fov_deg / 2.0 + 5.0))
        r_d = 2.0 * np.tan(theta / 2.0)
        px = cx + f * r_d * np.cos(phi)
        py = cy + f * r_d * np.sin(phi)

    elif model == LensModelType.FISHEYE_ORTHOGRAPHIC:
        valid_pts = (theta <= np.radians(min(89.5, intrinsics.fov_deg / 2.0)))
        r_d = np.sin(theta)
        px = cx + f * r_d * np.cos(phi)
        py = cy + f * r_d * np.sin(phi)

    elif model == LensModelType.PANORAMIC_CYLINDRICAL:
        # Full 180°+ panoramic horizontal breadth (Reolink Duo series)
        az_c = np.arctan2(xc, zc)
        d_xz = np.hypot(xc, zc)
        alt_c = np.arctan2(yc, np.maximum(d_xz, 1e-6))
        valid_pts = (zc > -0.2) & (np.abs(az_c) <= np.radians(intrinsics.fov_deg / 2.0 + 8.0))
        r2 = az_c * az_c + alt_c * alt_c
        radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * (r2 * r2)
        px = cx + f * (az_c * radial)
        py = cy + f * (np.tan(alt_c) * radial)

    else:
        r_d = theta
        px = cx + f * r_d * np.cos(phi)
        py = cy + f * r_d * np.sin(phi)
        valid_pts = theta <= np.radians(intrinsics.fov_deg / 2.0)

    # Check image boundary bounds and finite values
    is_finite = np.isfinite(px) & np.isfinite(py)
    in_bounds = valid_pts & is_finite & (px >= 0.0) & (px <= (img_w - 1.0)) & (py >= 0.0) & (py <= (img_h - 1.0))

    # Mask out points below configured minimum elevation (ground/horizon cutoff)
    min_elev = getattr(camera_cfg, "min_elevation_deg", 0.0)
    if min_elev is not None:
        # vz_val is sin(altitude) in world topocentric coordinates
        min_sin_alt = np.sin(np.radians(min_elev))
        above_horizon = vz_val >= min_sin_alt
        in_bounds = in_bounds & above_horizon

    # Apply polygon obstacle mask if configured and cutout mode is active (not tracking-only)
    mask_tracking_only = getattr(camera_cfg, "mask_tracking_only", True)
    all_masks = getattr(camera_cfg, "get_all_polygon_masks", lambda: [])()
    if not all_masks and len(camera_cfg.polygon_mask) >= 3:
        all_masks = [camera_cfg.polygon_mask]

    if not mask_tracking_only and len(all_masks) > 0 and in_bounds.any():
        poly_mask = generate_camera_polygon_mask(all_masks, img_w, img_h)
        # Sample polygon mask at rounded pixel locations only for in_bounds points
        px_int = np.clip(np.round(px[in_bounds]).astype(np.int32), 0, img_w - 1)
        py_int = np.clip(np.round(py[in_bounds]).astype(np.int32), 0, img_h - 1)
        unmasked = np.zeros_like(in_bounds, dtype=bool)
        unmasked[in_bounds] = poly_mask[py_int, px_int] > 0
        in_bounds = unmasked

    # Write into output remap tables
    selected_u = valid_indices[1][in_bounds]
    selected_v = valid_indices[0][in_bounds]
    selected_px = px[in_bounds].astype(np.float32)
    selected_py = py[in_bounds].astype(np.float32)

    map_x[selected_v, selected_u] = selected_px
    map_y[selected_v, selected_u] = selected_py
    cam_valid[selected_v, selected_u] = 255

    return map_x, map_y, cam_valid


def build_normalized_weight_maps(
    valid_masks: List[np.ndarray],
    feather_pixels: int = 25
) -> List[np.ndarray]:
    """
    Computes distance-transform blended weight maps for multi-camera overlap seams.
    Sum of weights at every covered canvas pixel is 1.0.
    """
    if not valid_masks:
        return []

    weights = []
    for mask in valid_masks:
        if np.count_nonzero(mask) == 0:
            weights.append(np.zeros(mask.shape, dtype=np.float32))
            continue

        # Distance transform from invalid boundary into valid region
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        # Cap distance at feather_pixels to produce a smooth linear ramp
        feather_dist = max(1.0, float(feather_pixels))
        w = np.clip(dist / feather_dist, 0.0, 1.0).astype(np.float32)
        weights.append(w)

    # Stack and normalize
    stacked = np.stack(weights, axis=0)  # (N, H, W)
    sum_weights = np.sum(stacked, axis=0)  # (H, W)
    covered = sum_weights > 1e-6

    normalized_weights = []
    for i in range(len(weights)):
        norm_w = np.zeros_like(weights[i])
        norm_w[covered] = stacked[i][covered] / sum_weights[covered]
        normalized_weights.append(norm_w)

    return normalized_weights
