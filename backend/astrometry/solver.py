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
    init_fov: Optional[float] = None,
    init_k1: Optional[float] = None,
    solve_distortion: bool = False
) -> Dict:
    """
    Solves for camera orientation (Azimuth, Altitude, Roll), Field of View (FOV),
    and optionally radial lens distortion (k1) using user-matched star points.
    
    Returns:
        dict containing 'azimuth_deg', 'altitude_deg', 'roll_deg', 'fov_deg', 'k1',
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

    # Only enable distortion co-optimization if requested and we have >= 4 stars
    optimize_k1 = solve_distortion and len(world_vectors) >= 4

    # Initial guess
    init_az = init_extrinsics.azimuth_deg if init_extrinsics else 0.0
    init_alt = init_extrinsics.altitude_deg if init_extrinsics else 45.0
    init_roll = init_extrinsics.roll_deg if init_extrinsics else 0.0
    init_fov_val = init_fov if init_fov else 85.0
    k1_init_val = init_k1 if init_k1 is not None else 0.0

    if optimize_k1:
        x0 = [init_az, init_alt, init_roll, init_fov_val, k1_init_val]
        lower_bounds = [0.0, -10.0, -180.0, 20.0, -0.4]
        upper_bounds = [360.0, 90.0, 180.0, 220.0, 0.4]
    else:
        x0 = [init_az, init_alt, init_roll, init_fov_val]
        lower_bounds = [0.0, -10.0, -180.0, 20.0]
        upper_bounds = [360.0, 90.0, 180.0, 220.0]

    def residual_vector(params):
        if optimize_k1:
            az_val, alt_val, roll_val, fov_val, k1_val = params
        else:
            az_val, alt_val, roll_val, fov_val = params
            k1_val = k1_init_val

        ext = CameraExtrinsics(azimuth_deg=az_val % 360.0, altitude_deg=alt_val, roll_deg=roll_val)
        intr = CameraIntrinsics(lens_model=lens_model, fov_deg=fov_val, k1=k1_val)
        R_w2c = build_camera_rotation_matrix(ext)

        resids = []
        for v, (tx, ty) in zip(world_vectors, target_pixels):
            proj = project_world_point_to_camera(v, R_w2c, intr, img_w, img_h)
            if proj is None:
                resids.extend([500.0, 500.0])
            else:
                resids.extend([proj[0] - tx, proj[1] - ty])
        return resids

    try:
        from scipy.optimize import least_squares
        ls_res = least_squares(
            residual_vector,
            x0,
            bounds=(lower_bounds, upper_bounds),
            diff_step=1e-3,
            ftol=1e-8,
            xtol=1e-8,
            gtol=1e-8
        )
        opt_params = ls_res.x
        is_success = bool(ls_res.success)
    except Exception:
        def objective(params):
            r = residual_vector(params)
            return math.sqrt(sum(x * x for x in r) / len(world_vectors))

        bounds_tuples = list(zip(lower_bounds, upper_bounds))
        res = minimize(objective, x0, method="Powell", bounds=bounds_tuples, options={"maxiter": 2000, "xtol": 1e-4})
        opt_params = res.x
        is_success = bool(res.success)

    opt_az = float(opt_params[0] % 360.0)
    opt_alt = float(max(-10.0, min(90.0, opt_params[1])))
    opt_roll = float(opt_params[2])
    while opt_roll > 180.0:
        opt_roll -= 360.0
    while opt_roll < -180.0:
        opt_roll += 360.0
    opt_fov = float(opt_params[3])
    opt_k1 = float(opt_params[4]) if optimize_k1 else k1_init_val

    # Compute final reprojections
    final_ext = CameraExtrinsics(azimuth_deg=opt_az, altitude_deg=opt_alt, roll_deg=opt_roll)
    final_intr = CameraIntrinsics(lens_model=lens_model, fov_deg=opt_fov, k1=opt_k1)
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
        "success": bool(is_success or rms_error < 15.0),
        "azimuth_deg": round(opt_az, 2),
        "altitude_deg": round(opt_alt, 2),
        "roll_deg": round(opt_roll, 2),
        "fov_deg": round(opt_fov, 2),
        "k1": round(opt_k1, 4),
        "rms_error_px": round(rms_error, 2),
        "reprojections": reprojections
    }


def auto_plate_solve_camera(
    image: np.ndarray,
    dt: datetime.datetime,
    lat_deg: float,
    lon_deg: float,
    lens_model: LensModelType = LensModelType.RECTILINEAR,
    init_extrinsics: Optional[CameraExtrinsics] = None,
    init_fov: Optional[float] = None,
    init_k1: Optional[float] = None,
    solve_distortion: bool = False,
    max_search_dist_px: float = 80.0
) -> Dict:
    """
    Autonomous Star Plate Solver:
    1. Detects star centroids on camera frame.
    2. Projects visible catalogue stars using observer lat/lon, NTP datetime, and estimated/prior pose.
    3. Solves optimal assignment (Hungarian / nearest mutual distance) between detected centroids and catalog stars.
    4. Runs non-linear optimization to solve refined Azimuth, Altitude, Roll, and FOV.
    """
    h, w = image.shape[:2]
    detected_stars = detect_star_centroids(image, min_area=2, max_stars=35)
    if len(detected_stars) < 2:
        return {
            "success": False,
            "error": f"Insufficient stars detected on sensor ({len(detected_stars)} found, minimum 2 required)."
        }

    # 1. Project candidate catalog stars using initial prior pose
    est_az = init_extrinsics.azimuth_deg if init_extrinsics else 0.0
    est_alt = init_extrinsics.altitude_deg if init_extrinsics else 45.0
    est_roll = init_extrinsics.roll_deg if init_extrinsics else 0.0
    est_fov = init_fov if init_fov else 85.0

    ext_prior = CameraExtrinsics(azimuth_deg=est_az, altitude_deg=est_alt, roll_deg=est_roll)
    intr_prior = CameraIntrinsics(lens_model=lens_model, fov_deg=est_fov)
    R_w2c_prior = build_camera_rotation_matrix(ext_prior)

    lst_deg = get_lst_deg(dt, lon_deg)
    catalog_projections = []

    for star in BRIGHT_STARS:
        alt, az = radec_to_altaz(star["ra"], star["dec"], lat_deg, lst_deg)
        if alt < -2.0:
            continue
        v_world = altaz_to_unit_vector(alt, az)
        proj = project_world_point_to_camera(v_world, R_w2c_prior, intr_prior, w, h)
        if proj is not None:
            px, py = proj
            # Keep stars inside or near sensor boundary
            if -40 <= px <= w + 40 and -40 <= py <= h + 40:
                catalog_projections.append({
                    "star_id": star["id"],
                    "name": star["name"],
                    "mag": star["mag"],
                    "proj_x": px,
                    "proj_y": py
                })

    if len(catalog_projections) < 2:
        return {
            "success": False,
            "error": f"Fewer than 2 catalog reference stars fall in the camera's FOV (found {len(catalog_projections)})."
        }

    # 2. Greedy mutual nearest-neighbor matching
    matched_pairs = []
    used_det_indices = set()
    used_cat_ids = set()

    # Sort catalog projections by brightness (ascending magnitude)
    catalog_projections.sort(key=lambda s: s["mag"])

    for cat_star in catalog_projections:
        best_dist = float("inf")
        best_det_idx = -1
        cx, cy = cat_star["proj_x"], cat_star["proj_y"]

        for i, det in enumerate(detected_stars):
            if i in used_det_indices:
                continue
            dx = det["x"] - cx
            dy = det["y"] - cy
            dist = math.hypot(dx, dy)
            if dist < best_dist and dist <= max_search_dist_px:
                best_dist = dist
                best_det_idx = i

        if best_det_idx >= 0:
            used_det_indices.add(best_det_idx)
            used_cat_ids.add(cat_star["star_id"])
            det = detected_stars[best_det_idx]
            matched_pairs.append({
                "star_id": cat_star["star_id"],
                "star_name": cat_star["name"],
                "pixel_x": det["x"],
                "pixel_y": det["y"],
                "distance_px": round(best_dist, 2)
            })

    if len(matched_pairs) < 2:
        return {
            "success": False,
            "error": f"Could not correlate enough star patterns (only {len(matched_pairs)} matched)."
        }

    # 3. Solve refined camera pose using matched anchors
    sol = solve_camera_pose_from_matches(
        matches=matched_pairs,
        dt=dt,
        lat_deg=lat_deg,
        lon_deg=lon_deg,
        img_w=w,
        img_h=h,
        lens_model=lens_model,
        init_extrinsics=ext_prior,
        init_fov=est_fov,
        init_k1=init_k1,
        solve_distortion=solve_distortion
    )

    # 4. Outlier pruning (trim misidentified edge peaks or distortion outliers)
    if len(matched_pairs) >= 4 and sol.get("reprojections"):
        errs = [r["error_px"] for r in sol["reprojections"] if r.get("error_px") is not None]
        if errs:
            med_err = float(np.median(errs))
            thresh = max(14.0, med_err * 2.2)
            inliers = [
                m for m, r in zip(matched_pairs, sol["reprojections"])
                if r.get("error_px", 999.0) <= thresh
            ]
            if len(inliers) >= 3 and len(inliers) < len(matched_pairs):
                ref_ext = CameraExtrinsics(
                    azimuth_deg=sol["azimuth_deg"],
                    altitude_deg=sol["altitude_deg"],
                    roll_deg=sol["roll_deg"]
                )
                refined_sol = solve_camera_pose_from_matches(
                    matches=inliers,
                    dt=dt,
                    lat_deg=lat_deg,
                    lon_deg=lon_deg,
                    img_w=w,
                    img_h=h,
                    lens_model=lens_model,
                    init_extrinsics=ref_ext,
                    init_fov=sol["fov_deg"],
                    init_k1=sol.get("k1", init_k1),
                    solve_distortion=solve_distortion
                )
                if refined_sol.get("rms_error_px", 999.0) < sol.get("rms_error_px", 999.0):
                    sol = refined_sol
                    matched_pairs = inliers

    sol["matched_stars"] = matched_pairs
    sol["match_count"] = len(matched_pairs)
    return sol

