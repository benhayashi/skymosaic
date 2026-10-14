import datetime
import math
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np
from scipy.optimize import minimize

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
from backend.config import CameraExtrinsics, CameraIntrinsics, LensModelType


def detect_star_centroids(
    image: np.ndarray,
    min_area: int = 2,
    max_area: int = 150,
    threshold_percentile: float = 98.5,
    max_stars: int = 50
) -> List[Dict]:
    """
    Detects bright star peaks and centroids in a night sky image with sub-pixel precision.
    Returns list of dicts with 'x', 'y', 'brightness', 'area'.
    """
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # Mild gaussian blur to suppress single-pixel sensor hot noise
    blurred = cv2.GaussianBlur(gray, (3, 3), 0.7)

    # Dynamic threshold based on image brightness percentile
    thresh_val = np.percentile(blurred, threshold_percentile)
    thresh_val = max(thresh_val, 25.0)  # Don't trigger on pitch black

    _, binary = cv2.threshold(blurred, thresh_val, 255, cv2.THRESH_BINARY)

    # Connected components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)

    stars = []
    h, w = gray.shape[:2]

    for label in range(1, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if min_area <= area <= max_area:
            cx, cy = centroids[label]
            # Discard edge artifacts
            if 5 < cx < w - 5 and 5 < cy < h - 5:
                # Sub-pixel refinement around centroid
                x_int, y_int = int(round(cx)), int(round(cy))
                window = blurred[max(0, y_int - 2):min(h, y_int + 3), max(0, x_int - 2):min(w, x_int + 3)]
                peak_val = float(np.max(window)) if window.size > 0 else float(blurred[y_int, x_int])

                stars.append({
                    "x": round(float(cx), 2),
                    "y": round(float(cy), 2),
                    "brightness": round(peak_val, 1),
                    "area": int(area)
                })

    # Sort descending by brightness
    stars.sort(key=lambda s: s["brightness"], reverse=True)
    return stars[:max_stars]


def solve_camera_pose_from_matches(
    matches: List[Dict],  # Each dict has: 'pixel_x', 'pixel_y', 'star_id'
    dt: datetime.datetime,
    lat_deg: float,
    lon_deg: float,
    img_w: int,
    img_h: int,
    lens_model: LensModelType,
    init_extrinsics: Optional[CameraExtrinsics] = None,
    init_fov: Optional[float] = None
) -> Dict:
    """
    Solves for camera orientation (Azimuth, Altitude, Roll) and Field of View (FOV)
    using user-matched star points on the test shot.
    
    Returns:
        dict containing 'azimuth_deg', 'altitude_deg', 'roll_deg', 'fov_deg',
        'rms_error_px', 'reprojections'.
    """
    if len(matches) < 2:
        raise ValueError("At least 2 star matches are required to solve camera orientation.")

    star_catalog_map = {s["id"]: s for s in BRIGHT_STARS}
    lst_deg = get_lst_deg(dt, lon_deg)

    world_vectors = []
    target_pixels = []

    for m in matches:
        star_id = m.get("star_id")
        if star_id not in star_catalog_map:
            continue
        star = star_catalog_map[star_id]
        alt, az = radec_to_altaz(star["ra"], star["dec"], lat_deg, lst_deg)
        v = altaz_to_unit_vector(alt, az)
        world_vectors.append(v)
        target_pixels.append((float(m["pixel_x"]), float(m["pixel_y"])))

    if len(world_vectors) < 2:
        raise ValueError("Could not find sufficient valid catalog stars among the matches.")

    # Initial guess
    init_az = init_extrinsics.azimuth_deg if init_extrinsics else 0.0
    init_alt = init_extrinsics.altitude_deg if init_extrinsics else 45.0
    init_roll = init_extrinsics.roll_deg if init_extrinsics else 0.0
    init_fov_val = init_fov if init_fov else 85.0

    x0 = [init_az, init_alt, init_roll, init_fov_val]
    bounds = [
        (0.0, 360.0),    # Azimuth
        (-10.0, 90.0),   # Altitude
        (-180.0, 180.0), # Roll
        (20.0, 220.0),   # FOV
    ]

    def objective(params):
        az_val, alt_val, roll_val, fov_val = params
        ext = CameraExtrinsics(azimuth_deg=az_val % 360.0, altitude_deg=alt_val, roll_deg=roll_val % 360.0)
        intr = CameraIntrinsics(lens_model=lens_model, fov_deg=fov_val)
        R_w2c = build_camera_rotation_matrix(ext)

        total_err_sq = 0.0
        for v, (tx, ty) in zip(world_vectors, target_pixels):
            proj = project_world_point_to_camera(v, R_w2c, intr, img_w, img_h)
            if proj is None:
                return 1e6
            else:
                px, py = proj
                total_err_sq += (px - tx) ** 2 + (py - ty) ** 2

        return math.sqrt(total_err_sq / len(world_vectors))

    res = minimize(objective, x0, method="Powell", bounds=bounds, options={"maxiter": 2000, "xtol": 1e-4})
    if not res.success or res.fun > 50.0:
        # Fallback Nelder-Mead
        res_nm = minimize(objective, x0, method="Nelder-Mead", bounds=bounds, options={"maxiter": 1000})
        if res_nm.fun < res.fun:
            res = res_nm


    opt_az, opt_alt, opt_roll, opt_fov = res.x
    opt_az = float(opt_az % 360.0)
    opt_alt = float(max(-10.0, min(90.0, opt_alt)))
    opt_roll = float(opt_roll % 360.0)
    opt_fov = float(opt_fov)

    # Compute final reprojections
    final_ext = CameraExtrinsics(azimuth_deg=opt_az, altitude_deg=opt_alt, roll_deg=opt_roll)
    final_intr = CameraIntrinsics(lens_model=lens_model, fov_deg=opt_fov)
    R_w2c = build_camera_rotation_matrix(final_ext)

    reprojections = []
    total_sq = 0.0
    for v, (tx, ty), m in zip(world_vectors, target_pixels, matches):
        proj = project_world_point_to_camera(v, R_w2c, final_intr, img_w, img_h)
        if proj is not None:
            px, py = proj
            err = math.hypot(px - tx, py - ty)
            total_sq += err * err
            reprojections.append({
                "star_id": m.get("star_id"),
                "target_x": round(tx, 2),
                "target_y": round(ty, 2),
                "projected_x": round(px, 2),
                "projected_y": round(py, 2),
                "error_px": round(err, 2)
            })
        else:
            reprojections.append({
                "star_id": m.get("star_id"),
                "target_x": round(tx, 2),
                "target_y": round(ty, 2),
                "projected_x": None,
                "projected_y": None,
                "error_px": 999.0
            })

    rms_error = math.sqrt(total_sq / len(reprojections)) if reprojections else 999.0

    return {
        "success": bool(res.success or rms_error < 15.0),
        "azimuth_deg": round(opt_az, 2),
        "altitude_deg": round(opt_alt, 2),
        "roll_deg": round(opt_roll, 2),
        "fov_deg": round(opt_fov, 2),
        "rms_error_px": round(rms_error, 2),
        "reprojections": reprojections
    }
