import math
from typing import Optional, Tuple
import numpy as np
from scipy.spatial.transform import Rotation

from backend.config import CameraExtrinsics, CameraIntrinsics, LensModelType


def build_camera_rotation_matrix(extrinsics: CameraExtrinsics) -> np.ndarray:
    """
    Builds the 3x3 rotation matrix R_w2c that transforms a topocentric world vector
    (X=East, Y=North, Z=Up) into the camera optical frame (X=Right, Y=Down, Z=Forward).
    
    extrinsics.azimuth_deg: 0=North, 90=East, 180=South, 270=West
    extrinsics.altitude_deg: 0=Horizon, 90=Zenith
    extrinsics.roll_deg: clockwise rotation around optical boresight in degrees
    """
    az_rad = math.radians(extrinsics.azimuth_deg)
    alt_rad = math.radians(extrinsics.altitude_deg)
    roll_rad = math.radians(extrinsics.roll_deg)

    # 1. Optical boresight unit vector in topocentric frame
    # X=East, Y=North, Z=Up
    cos_alt = math.cos(alt_rad)
    sin_alt = math.sin(alt_rad)
    fwd_w = np.array([
        cos_alt * math.sin(az_rad),
        cos_alt * math.cos(az_rad),
        sin_alt
    ], dtype=np.float64)
    fwd_w /= np.linalg.norm(fwd_w)

    # 2. Camera "Right" and "Down" axes with zero roll
    # Zenith vector is [0, 0, 1]
    up_zenith = np.array([0.0, 0.0, 1.0], dtype=np.float64)

    # If pointing almost straight up or down, define reference right as East [1, 0, 0]
    if abs(sin_alt) > 0.99999:
        if sin_alt > 0:  # Pointing at Zenith
            # Facing zenith: forward is +Z. Camera 'up' points North [0, 1, 0], so right is East [1, 0, 0]
            right_w = np.array([math.cos(az_rad), -math.sin(az_rad), 0.0], dtype=np.float64)
        else:  # Pointing at Nadir
            right_w = np.array([math.cos(az_rad), math.sin(az_rad), 0.0], dtype=np.float64)
        right_w /= np.linalg.norm(right_w)
        down_w = np.cross(fwd_w, right_w)
    else:
        # Normal pointing: right is horizontal (perpendicular to boresight and vertical axis)
        # In camera frame, +Y is Down (so -Y is Up).
        # boresight x zenith gives camera East/Right:
        right_w = np.cross(fwd_w, up_zenith)
        right_w /= np.linalg.norm(right_w)
        # down = fwd x right
        down_w = np.cross(fwd_w, right_w)
        down_w /= np.linalg.norm(down_w)

    # 3. Apply roll rotation around fwd_w (clockwise around camera optical axis)
    if abs(roll_rad) > 1e-6:
        rot_roll = Rotation.from_rotvec(roll_rad * fwd_w)
        right_w = rot_roll.apply(right_w)
        down_w = rot_roll.apply(down_w)

    # R_c2w has columns [right_w, down_w, fwd_w]
    R_c2w = np.column_stack([right_w, down_w, fwd_w])
    # R_w2c is transpose (orthogonal)
    R_w2c = R_c2w.T
    return R_w2c


def get_focal_length_px(intrinsics: CameraIntrinsics, img_w: int, img_h: int) -> float:
    """Calculates focal length in pixels from field of view (FOV) and lens model."""
    fov_rad = math.radians(intrinsics.fov_deg)
    dimension = img_w if intrinsics.fov_axis == "horizontal" else math.hypot(img_w, img_h)

    model = intrinsics.lens_model
    if model == LensModelType.RECTILINEAR:
        # w = 2 * f * tan(fov / 2)
        f = (dimension / 2.0) / math.tan(fov_rad / 2.0)
    elif model == LensModelType.FISHEYE_EQUIDISTANT:
        # w = 2 * f * (fov / 2) = f * fov
        f = (dimension / 2.0) / (fov_rad / 2.0)
    elif model == LensModelType.FISHEYE_EQUISOLID:
        # w = 4 * f * sin(fov / 4)
        f = (dimension / 2.0) / (2.0 * math.sin(fov_rad / 4.0))
    elif model == LensModelType.FISHEYE_STEREOGRAPHIC:
        # w = 4 * f * tan(fov / 4)
        f = (dimension / 2.0) / (2.0 * math.tan(fov_rad / 4.0))
    elif model == LensModelType.FISHEYE_ORTHOGRAPHIC:
        # w = 2 * f * sin(fov / 2)
        f = (dimension / 2.0) / math.sin(fov_rad / 2.0)
    elif model == LensModelType.PANORAMIC_CYLINDRICAL:
        # Full horizontal angular breadth: w = f * fov_rad
        f = dimension / max(1e-5, fov_rad)
    else:
        f = (dimension / 2.0) / math.tan(fov_rad / 2.0)

    return f


def project_world_point_to_camera(
    v_world: np.ndarray,
    R_w2c: np.ndarray,
    intrinsics: CameraIntrinsics,
    img_w: int,
    img_h: int
) -> Optional[Tuple[float, float]]:
    """
    Projects a single 3D unit vector in World frame (X=East, Y=North, Z=Up)
    onto 2D image coordinates (pixel x, y).
    Returns None if point is outside the lens optical field of view or behind camera.
    """
    p_cam = R_w2c @ v_world
    xc, yc, zc = float(p_cam[0]), float(p_cam[1]), float(p_cam[2])

    f = get_focal_length_px(intrinsics, img_w, img_h)
    cx = (img_w / 2.0) + intrinsics.center_offset_x * img_w
    cy = (img_h / 2.0) + intrinsics.center_offset_y * img_h

    norm_xy = math.hypot(xc, yc)
    if norm_xy < 1e-12:
        if zc > 0:
            return (cx, cy)
        return None

    theta = math.atan2(norm_xy, zc)  # Angle from optical axis (0 to pi)
    phi = math.atan2(yc, xc)         # Azimuth in camera plane

    model = intrinsics.lens_model
    if model == LensModelType.RECTILINEAR:
        if zc <= 0 or theta >= math.radians(89.5):
            return None
        r_u = math.tan(theta)
        # Apply radial & tangential distortion
        r2 = r_u * r_u
        r4 = r2 * r2
        radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * r4
        r_d = r_u * radial

        # Normalized coordinates with distortion
        x_norm = (xc / zc) * radial
        y_norm = (yc / zc) * radial
        if intrinsics.p1 != 0.0 or intrinsics.p2 != 0.0:
            dx = 2.0 * intrinsics.p1 * x_norm * y_norm + intrinsics.p2 * (r2 + 2.0 * x_norm * x_norm)
            dy = intrinsics.p1 * (r2 + 2.0 * y_norm * y_norm) + 2.0 * intrinsics.p2 * x_norm * y_norm
            x_norm += dx
            y_norm += dy

        px = cx + f * x_norm
        py = cy + f * y_norm

    elif model == LensModelType.FISHEYE_EQUIDISTANT:
        r_d = theta
        px = cx + f * r_d * math.cos(phi)
        py = cy + f * r_d * math.sin(phi)

    elif model == LensModelType.FISHEYE_EQUISOLID:
        r_d = 2.0 * math.sin(theta / 2.0)
        px = cx + f * r_d * math.cos(phi)
        py = cy + f * r_d * math.sin(phi)

    elif model == LensModelType.FISHEYE_STEREOGRAPHIC:
        if theta >= math.radians(170.0):
            return None
        r_d = 2.0 * math.tan(theta / 2.0)
        px = cx + f * r_d * math.cos(phi)
        py = cy + f * r_d * math.sin(phi)

    elif model == LensModelType.FISHEYE_ORTHOGRAPHIC:
        r_d = math.sin(theta)
        px = cx + f * r_d * math.cos(phi)
        py = cy + f * r_d * math.sin(phi)

    elif model == LensModelType.PANORAMIC_CYLINDRICAL:
        # Full 180°+ panoramic horizontal breadth (Reolink Duo series)
        az_c = math.atan2(xc, zc)
        fov_rad = math.radians(intrinsics.fov_deg)
        if abs(az_c) > (fov_rad / 2.0) + math.radians(8.0):
            return None
        d_xz = math.hypot(xc, zc)
        alt_c = math.atan2(yc, max(d_xz, 1e-12))
        r2 = az_c * az_c + alt_c * alt_c
        radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * (r2 * r2)
        px = cx + f * (az_c * radial)
        py = cy + f * (math.tan(alt_c) * radial)

    else:
        r_d = theta
        px = cx + f * r_d * math.cos(phi)
        py = cy + f * r_d * math.sin(phi)

    return (px, py)


def backproject_pixel_to_world_ray(
    px: float,
    py: float,
    R_c2w: np.ndarray,
    intrinsics: CameraIntrinsics,
    img_w: int,
    img_h: int
) -> Optional[np.ndarray]:
    """
    Backprojects a 2D image pixel coordinate (px, py) to a 3D unit ray in Topocentric World frame.
    Returns None if pixel is outside the valid physical range of the lens model.
    """
    f = get_focal_length_px(intrinsics, img_w, img_h)
    cx = (img_w / 2.0) + intrinsics.center_offset_x * img_w
    cy = (img_h / 2.0) + intrinsics.center_offset_y * img_h

    dx = (px - cx) / f
    dy = (py - cy) / f

    model = intrinsics.lens_model
    if model == LensModelType.PANORAMIC_CYLINDRICAL:
        az_c = dx
        alt_c = math.atan(dy)
        xc = math.sin(az_c) * math.cos(alt_c)
        yc = math.sin(alt_c)
        zc = math.cos(az_c) * math.cos(alt_c)
        ray_cam = np.array([xc, yc, zc], dtype=np.float64)
        ray_world = R_c2w @ ray_cam
        norm = np.linalg.norm(ray_world)
        if norm > 1e-12:
            ray_world /= norm
        return ray_world

    r = math.hypot(dx, dy)
    phi = math.atan2(dy, dx)

    if model == LensModelType.RECTILINEAR:
        theta = math.atan(r)
    elif model == LensModelType.FISHEYE_EQUIDISTANT:
        theta = r
    elif model == LensModelType.FISHEYE_EQUISOLID:
        val = r / 2.0
        if abs(val) > 1.0:
            return None
        theta = 2.0 * math.asin(val)
    elif model == LensModelType.FISHEYE_STEREOGRAPHIC:
        theta = 2.0 * math.atan(r / 2.0)
    elif model == LensModelType.FISHEYE_ORTHOGRAPHIC:
        if abs(r) > 1.0:
            return None
        theta = math.asin(r)
    else:
        theta = r

    if theta > math.pi:
        return None

    # Ray in camera frame
    xc = math.sin(theta) * math.cos(phi)
    yc = math.sin(theta) * math.sin(phi)
    zc = math.cos(theta)
    ray_cam = np.array([xc, yc, zc], dtype=np.float64)

    # Ray in world frame
    ray_world = R_c2w @ ray_cam
    norm = np.linalg.norm(ray_world)
    if norm > 1e-12:
        ray_world /= norm
    return ray_world
