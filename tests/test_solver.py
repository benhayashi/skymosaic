import datetime
import math
import cv2
import numpy as np
import pytest

from backend.astrometry.camera_model import build_camera_rotation_matrix, project_world_point_to_camera
from backend.astrometry.celestial import BRIGHT_STARS, altaz_to_unit_vector, get_lst_deg, radec_to_altaz
from backend.astrometry.solver import detect_star_centroids, solve_camera_pose_from_matches
from backend.config import CameraExtrinsics, CameraIntrinsics, LensModelType


def test_detect_star_centroids():
    img = np.zeros((300, 400), dtype=np.uint8)
    # Draw two artificial stars
    cv2.circle(img, (100, 150), 2, 255, -1)
    cv2.circle(img, (250, 80), 3, 230, -1)

    stars = detect_star_centroids(img, min_area=1, threshold_percentile=90.0)
    assert len(stars) >= 2

    # Verify positions close to drawn centers
    pts = [(s["x"], s["y"]) for s in stars]
    found_100_150 = any(abs(p[0] - 100) < 3 and abs(p[1] - 150) < 3 for p in pts)
    found_250_80 = any(abs(p[0] - 250) < 3 and abs(p[1] - 80) < 3 for p in pts)
    assert found_100_150
    assert found_250_80


def test_solve_camera_pose_from_matches():
    # Ground truth parameters
    true_az = 120.0
    true_alt = 50.0
    true_roll = 5.0
    true_fov = 80.0
    lens_model = LensModelType.RECTILINEAR
    w, h = 1280, 720

    ext = CameraExtrinsics(azimuth_deg=true_az, altitude_deg=true_alt, roll_deg=true_roll)
    intr = CameraIntrinsics(lens_model=lens_model, fov_deg=true_fov)
    R_w2c = build_camera_rotation_matrix(ext)

    dt = datetime.datetime(2026, 10, 14, 3, 0, 0, tzinfo=datetime.timezone.utc)
    lat, lon = 40.71, -74.00
    lst = get_lst_deg(dt, lon)

    # Project known catalog stars to create synthetic ground truth matches
    matches = []
    for star in BRIGHT_STARS:
        alt, az = radec_to_altaz(star["ra"], star["dec"], lat, lst)
        if alt < 10.0:
            continue
        v = altaz_to_unit_vector(alt, az)
        proj = project_world_point_to_camera(v, R_w2c, intr, w, h)
        if proj is not None:
            px, py = proj
            if 50 < px < w - 50 and 50 < py < h - 50:
                matches.append({
                    "pixel_x": px,
                    "pixel_y": py,
                    "star_id": star["id"]
                })
        if len(matches) >= 4:
            break

    assert len(matches) >= 3, "Needed at least 3 visible synthetic stars"

    # Perturb initial guess
    init_ext = CameraExtrinsics(azimuth_deg=true_az + 5.0, altitude_deg=true_alt - 4.0, roll_deg=0.0)

    sol = solve_camera_pose_from_matches(
        matches=matches,
        dt=dt,
        lat_deg=lat,
        lon_deg=lon,
        img_w=w,
        img_h=h,
        lens_model=lens_model,
        init_extrinsics=init_ext,
        init_fov=true_fov + 2.0
    )

    assert sol["success"]
    assert abs(sol["azimuth_deg"] - true_az) < 1.0
    assert abs(sol["altitude_deg"] - true_alt) < 1.0
    assert abs(sol["fov_deg"] - true_fov) < 1.0
    assert sol["rms_error_px"] < 2.0
