import math
import numpy as np
import pytest

from backend.astrometry.camera_model import (
    backproject_pixel_to_world_ray,
    build_camera_rotation_matrix,
    get_focal_length_px,
    project_world_point_to_camera,
)
from backend.config import CameraExtrinsics, CameraIntrinsics, LensModelType


def test_rotation_matrix_properties():
    ext = CameraExtrinsics(azimuth_deg=135.0, altitude_deg=45.0, roll_deg=15.0)
    R = build_camera_rotation_matrix(ext)

    # 1. Orthogonality: R @ R.T = I
    I_approx = R @ R.T
    assert np.allclose(I_approx, np.eye(3), atol=1e-6)

    # 2. Right-handed coordinate system: det(R) = +1
    det = np.linalg.det(R)
    assert pytest.approx(det, abs=1e-5) == 1.0


def test_camera_boresight_projects_to_center():
    ext = CameraExtrinsics(azimuth_deg=180.0, altitude_deg=60.0, roll_deg=0.0)
    R_w2c = build_camera_rotation_matrix(ext)
    intr = CameraIntrinsics(lens_model=LensModelType.RECTILINEAR, fov_deg=80.0)
    w, h = 1920, 1080

    # Point along camera boresight in world coordinates
    # For Az=180, Alt=60:
    cos_alt = math.cos(math.radians(60.0))
    sin_alt = math.sin(math.radians(60.0))
    # Az=180 -> East = 0, North = -cos_alt
    boresight_w = np.array([0.0, -cos_alt, sin_alt], dtype=np.float64)

    proj = project_world_point_to_camera(boresight_w, R_w2c, intr, w, h)
    assert proj is not None
    px, py = proj
    assert pytest.approx(px, abs=1.0) == w / 2.0
    assert pytest.approx(py, abs=1.0) == h / 2.0


def test_lens_models_forward_and_backprojection():
    models = [
        LensModelType.RECTILINEAR,
        LensModelType.FISHEYE_EQUIDISTANT,
        LensModelType.FISHEYE_EQUISOLID,
        LensModelType.FISHEYE_STEREOGRAPHIC,
        LensModelType.FISHEYE_ORTHOGRAPHIC,
        LensModelType.PANORAMIC_CYLINDRICAL,
    ]
    ext = CameraExtrinsics(azimuth_deg=0.0, altitude_deg=90.0, roll_deg=0.0) # pointing zenith
    R_w2c = build_camera_rotation_matrix(ext)
    R_c2w = R_w2c.T
    w, h = 1280, 720

    # Test point at Alt=80 deg, Az=45 deg (near zenith, well within FOV)
    alt_rad = math.radians(80.0)
    az_rad = math.radians(45.0)
    cos_alt = math.cos(alt_rad)
    v_orig = np.array([cos_alt * math.sin(az_rad), cos_alt * math.cos(az_rad), math.sin(alt_rad)])

    for model in models:
        intr = CameraIntrinsics(lens_model=model, fov_deg=100.0)
        proj = project_world_point_to_camera(v_orig, R_w2c, intr, w, h)
        assert proj is not None, f"Failed projection for {model}"
        px, py = proj
        assert 0 <= px < w and 0 <= py < h

        # Backproject
        ray_world = backproject_pixel_to_world_ray(px, py, R_c2w, intr, w, h)
        assert ray_world is not None, f"Failed backprojection for {model}"

        # Ray should match original unit vector
        cos_similarity = np.dot(ray_world, v_orig)
        assert pytest.approx(cos_similarity, abs=1e-4) == 1.0, f"Mismatch for {model}"
