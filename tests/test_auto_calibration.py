import datetime
import math
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.astrometry.auto_calibrator import AutoCalibrationManager
from backend.astrometry.camera_model import build_camera_rotation_matrix, project_world_point_to_camera
from backend.astrometry.celestial import BRIGHT_STARS, altaz_to_unit_vector, get_lst_deg, radec_to_altaz
from backend.astrometry.solver import auto_plate_solve_camera
from backend.config import (
    CameraConfig,
    CameraExtrinsics,
    CameraIntrinsics,
    CameraSourceType,
    LensModelType,
    ObserverConfig,
    SkyViewConfig,
)


def test_auto_plate_solve_synthetic_image():
    # 1. Setup ground truth camera & observer
    true_az = 90.0
    true_alt = 45.0
    true_roll = 0.0
    true_fov = 85.0
    w, h = 1280, 720
    lens_model = LensModelType.RECTILINEAR

    dt = datetime.datetime(2026, 10, 14, 2, 0, 0, tzinfo=datetime.timezone.utc)
    lat, lon = 40.71, -74.00
    lst = get_lst_deg(dt, lon)

    ext_true = CameraExtrinsics(azimuth_deg=true_az, altitude_deg=true_alt, roll_deg=true_roll)
    intr_true = CameraIntrinsics(lens_model=lens_model, fov_deg=true_fov)
    R_w2c_true = build_camera_rotation_matrix(ext_true)

    # 2. Render synthetic sky image with stars
    img = np.zeros((h, w, 3), dtype=np.uint8)
    star_count = 0

    for star in BRIGHT_STARS:
        alt, az = radec_to_altaz(star["ra"], star["dec"], lat, lst)
        if alt < 10.0:
            continue
        v = altaz_to_unit_vector(alt, az)
        proj = project_world_point_to_camera(v, R_w2c_true, intr_true, w, h)
        if proj is not None:
            px, py = proj
            if 40 < px < w - 40 and 40 < py < h - 40:
                cv2.circle(img, (int(round(px)), int(round(py))), 3, (255, 255, 255), -1)
                star_count += 1

    assert star_count >= 3, f"Need at least 3 visible stars, found {star_count}"

    # 3. Perturbed initial guess (simulating mount drift: 4 deg off Az, 3 deg off Alt, 2 deg off FOV)
    ext_init = CameraExtrinsics(azimuth_deg=true_az + 4.0, altitude_deg=true_alt - 3.0, roll_deg=1.5)
    init_fov = true_fov - 2.5

    # 4. Run autonomous plate solver
    sol = auto_plate_solve_camera(
        image=img,
        dt=dt,
        lat_deg=lat,
        lon_deg=lon,
        lens_model=lens_model,
        init_extrinsics=ext_init,
        init_fov=init_fov,
        max_search_dist_px=80.0
    )

    assert sol["success"] is True
    assert sol["match_count"] >= 3
    assert abs(sol["azimuth_deg"] - true_az) < 1.0
    assert abs(sol["altitude_deg"] - true_alt) < 1.0
    assert abs(sol["roll_deg"] - true_roll) < 1.0
    assert abs(sol["fov_deg"] - true_fov) < 1.0
    assert sol["rms_error_px"] < 1.5


def test_auto_calibration_manager_flow():
    # Setup mock stream manager and config
    cfg = SkyViewConfig()
    cfg.observer = ObserverConfig(latitude=40.71, longitude=-74.00, elevation_m=10.0, timezone_str="UTC")
    cfg.plugins.auto_calibration.enabled = True
    cfg.plugins.auto_calibration.require_night = True
    cfg.plugins.auto_calibration.max_rms_error_px = 5.0
    cfg.plugins.auto_calibration.min_star_matches = 2

    # Camera setup
    cam = CameraConfig(
        id="test_cam",
        name="Test Camera",
        source_type=CameraSourceType.SYNTHETIC,
        extrinsics=CameraExtrinsics(azimuth_deg=0.0, altitude_deg=45.0, roll_deg=0.0),
        intrinsics=CameraIntrinsics(lens_model=LensModelType.RECTILINEAR, fov_deg=85.0)
    )
    cfg.cameras = [cam]

    # Create synthetic frame with visible stars
    dt_night = datetime.datetime(2026, 10, 14, 3, 0, 0, tzinfo=datetime.timezone.utc)
    lst = get_lst_deg(dt_night, cfg.observer.longitude)
    R_w2c = build_camera_rotation_matrix(cam.extrinsics)
    img = np.zeros((720, 1280, 3), dtype=np.uint8)

    for star in BRIGHT_STARS:
        alt, az = radec_to_altaz(star["ra"], star["dec"], cfg.observer.latitude, lst)
        if alt < 10.0:
            continue
        v = altaz_to_unit_vector(alt, az)
        proj = project_world_point_to_camera(v, R_w2c, cam.intrinsics, 1280, 720)
        if proj is not None:
            px, py = proj
            if 30 < px < 1250 and 30 < py < 690:
                cv2.circle(img, (int(round(px)), int(round(py))), 3, (255, 255, 255), -1)

    class MockStreamManager:
        def get_camera_frame(self, cid):
            return img

    class MockNtpClient:
        def get_datetime(self):
            return dt_night

    class MockStitcher:
        ntp_client = MockNtpClient()

    saved_configs = []
    def save_cb(updated_cfg):
        saved_configs.append(updated_cfg)

    mgr = AutoCalibrationManager(
        config=cfg,
        stream_manager=MockStreamManager(),
        stitcher=MockStitcher(),
        on_calibration_applied=save_cb
    )

    # Status check
    status = mgr.get_status()
    assert status["enabled"] is True
    assert status["check_interval_minutes"] == 60

    # Perturb camera slightly
    cam.extrinsics.azimuth_deg = 3.0
    cam.extrinsics.altitude_deg = 42.0

    # Execute auto-calibration
    res = mgr.run_auto_calibration(camera_id="test_cam", force=True, apply_updates=True)
    assert res["success"] is True
    assert res["cameras_calibrated"] == 1
    assert len(saved_configs) == 1

    # Check that camera extrinsics were corrected
    assert abs(cam.extrinsics.azimuth_deg - 0.0) < 1.0
    assert abs(cam.extrinsics.altitude_deg - 45.0) < 1.0


def test_api_auto_calibration_endpoints():
    client = TestClient(app)

    # 1. GET auto status
    status_resp = client.get("/api/calibration/auto_status")
    assert status_resp.status_code == 200
    st_json = status_resp.json()
    assert "enabled" in st_json
    assert "check_interval_minutes" in st_json

    # 2. POST auto solve with force=True
    solve_resp = client.post("/api/calibration/auto_solve", json={
        "force": True,
        "apply_to_camera": False
    })
    assert solve_resp.status_code == 200
    sol_json = solve_resp.json()
    assert "status" in sol_json
    assert "results" in sol_json
