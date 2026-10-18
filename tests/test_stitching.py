import numpy as np
import pytest

from backend.config import (
    CameraConfig,
    CameraExtrinsics,
    CameraIntrinsics,
    LensModelType,
    SkyViewConfig,
    StitchTargetProjection,
    get_default_config,
)
from backend.stitching.stitcher import MultiCameraStitcher
from backend.stitching.warp_lut import (
    build_normalized_weight_maps,
    compute_camera_remap_lut,
    generate_target_world_grid,
)


def test_generate_target_world_grid_polar():
    cfg = get_default_config()
    cfg.stitching.projection = StitchTargetProjection.ZENITHAL_POLAR
    cfg.stitching.canvas_size = 400

    vx, vy, vz, valid_mask = generate_target_world_grid(cfg.stitching)
    assert vx.shape == (400, 400)
    assert vy.shape == (400, 400)
    assert vz.shape == (400, 400)
    assert valid_mask.shape == (400, 400)

    # Center pixel should point straight up (vz ~ 1.0, vx ~ 0, vy ~ 0)
    center_idx = 199
    assert vz[center_idx, center_idx] > 0.98


def test_multi_camera_stitcher_blending():
    cfg = get_default_config()
    cfg.stitching.canvas_size = 300

    stitcher = MultiCameraStitcher(cfg)

    # Create dummy color frames for 3 cameras
    w, h = 640, 360
    f1 = np.full((h, w, 3), [255, 0, 0], dtype=np.uint8)   # Blue
    f2 = np.full((h, w, 3), [0, 255, 0], dtype=np.uint8)   # Green
    f3 = np.full((h, w, 3), [0, 0, 255], dtype=np.uint8)   # Red

    frames = {
        "cam_north": f1,
        "cam_southeast": f2,
        "cam_southwest": f3
    }

    composite = stitcher.stitch_frames(frames, overlay_annotations=True)
    assert composite.shape == (300, 300, 3)
    # The canvas should not be entirely black
    assert np.count_nonzero(composite) > 1000


def test_min_elevation_masking_in_remap_lut():
    cfg = get_default_config()
    grid_vx, grid_vy, grid_vz, valid_mask = generate_target_world_grid(cfg.stitching)

    cam = CameraConfig(
        id="test_cam",
        name="Test Cam",
        min_elevation_deg=10.0,
        extrinsics=CameraExtrinsics(azimuth_deg=0.0, altitude_deg=45.0, roll_deg=0.0),
        intrinsics=CameraIntrinsics(lens_model=LensModelType.RECTILINEAR, fov_deg=90.0)
    )

    map_x, map_y, cam_valid = compute_camera_remap_lut(cam, 640, 480, grid_vx, grid_vy, grid_vz, valid_mask)
    # At any pixel where cam_valid > 0, the world elevation (sin(alt)=grid_vz) must be >= sin(10 deg)
    valid_pixels = cam_valid > 0
    assert np.count_nonzero(valid_pixels) > 0
    min_sin = np.sin(np.radians(10.0))
    assert np.all(grid_vz[valid_pixels] >= (min_sin - 1e-4))


def test_camera_flip_in_remap_lut():
    cfg = get_default_config()
    grid_vx, grid_vy, grid_vz, valid_mask = generate_target_world_grid(cfg.stitching)

    # Standard un-flipped camera
    cam_normal = CameraConfig(
        id="cam_norm",
        name="Norm Cam",
        flip_horizontal=False,
        flip_vertical=False,
        extrinsics=CameraExtrinsics(azimuth_deg=0.0, altitude_deg=45.0, roll_deg=0.0),
        intrinsics=CameraIntrinsics(lens_model=LensModelType.RECTILINEAR, fov_deg=90.0)
    )
    # Horizontally flipped camera
    cam_flipped = CameraConfig(
        id="cam_flip",
        name="Flip Cam",
        flip_horizontal=True,
        flip_vertical=False,
        extrinsics=CameraExtrinsics(azimuth_deg=0.0, altitude_deg=45.0, roll_deg=0.0),
        intrinsics=CameraIntrinsics(lens_model=LensModelType.RECTILINEAR, fov_deg=90.0)
    )

    w, h = 640, 480
    map_x1, map_y1, mask1 = compute_camera_remap_lut(cam_normal, w, h, grid_vx, grid_vy, grid_vz, valid_mask)
    map_x2, map_y2, mask2 = compute_camera_remap_lut(cam_flipped, w, h, grid_vx, grid_vy, grid_vz, valid_mask)

    # Masks should have the same coverage
    assert np.array_equal(mask1, mask2)
    valid_pts = mask1 > 0
    assert np.count_nonzero(valid_pts) > 0

    # In flipped LUT, map_x should equal (w - 1) - map_x1
    assert np.allclose(map_x2[valid_pts], (w - 1.0) - map_x1[valid_pts], atol=1e-3)
    # map_y should remain identical
    assert np.allclose(map_y2[valid_pts], map_y1[valid_pts], atol=1e-3)

