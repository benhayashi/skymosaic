import numpy as np
import pytest
from backend.config import CameraConfig, CameraExtrinsics, CameraIntrinsics, SkyViewConfig
from backend.stitching.warp_lut import (
    generate_camera_polygon_mask,
    normalize_polygon_list,
    point_in_polygon,
)
from backend.astrometry.obstacle_detector import detect_sky_obstacles
from backend.tracking.optical_tracker import OpticalTrackerEngine


def test_polygon_normalization():
    # Test single polygon
    single = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    normalized = normalize_polygon_list(single)
    assert len(normalized) == 1
    assert normalized[0] == single

    # Test multi-polygon
    multi = [
        [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
        [[0.7, 0.8], [0.9, 0.8], [0.8, 0.9]]
    ]
    norm_multi = normalize_polygon_list(multi)
    assert len(norm_multi) == 2
    assert norm_multi == multi

    # Empty
    assert normalize_polygon_list([]) == []
    assert normalize_polygon_list(None) == []


def test_generate_camera_polygon_mask():
    # 100x100 image
    w, h = 100, 100
    # Two rectangular obstacles: left pole (x: 10..20, y: 0..80) and right tree (x: 70..90, y: 20..70)
    obstacles = [
        [[0.10, 0.0], [0.20, 0.0], [0.20, 0.80], [0.10, 0.80]],
        [[0.70, 0.20], [0.90, 0.20], [0.90, 0.70], [0.70, 0.70]]
    ]
    mask = generate_camera_polygon_mask(obstacles, w, h)
    assert mask.shape == (100, 100)

    # Center sky should be valid (255)
    assert mask[50, 50] == 255

    # Inside left pole should be masked (0)
    assert mask[40, 15] == 0

    # Inside right tree should be masked (0)
    assert mask[40, 80] == 0

    # Point in polygon test
    assert point_in_polygon(15, 40, obstacles, w, h) is True
    assert point_in_polygon(50, 50, obstacles, w, h) is False


def test_detect_sky_obstacles_synthetic():
    # Create a synthetic image: bright sky (top) with a dark vertical pole and dark foliage tree
    img = np.full((400, 600, 3), 200, dtype=np.uint8)  # Bright sky background

    # Tall vertical pole (dark: 30)
    img[20:380, 80:110] = 30

    # Tree foliage block (dark: 45)
    img[120:360, 400:520] = 45

    obstacles = detect_sky_obstacles(img, min_area_px=200)
    assert len(obstacles) >= 2

    # Verify at least one pole and one tree classified
    labels = [o["label"] for o in obstacles]
    assert any("Pole" in l or "Vertical" in l for l in labels)
    assert any("Tree" in l or "Foliage" in l for l in labels)

    # Verify normalized coordinates
    for obs in obstacles:
        poly = obs["polygon"]
        assert len(poly) >= 3
        for pt in poly:
            assert 0.0 <= pt[0] <= 1.0
            assert 0.0 <= pt[1] <= 1.0


def test_camera_config_multi_polygons_and_mask_behavior():
    cam = CameraConfig(
        id="cam_test",
        name="Test Camera",
        polygon_masks=[
            [[0.1, 0.1], [0.2, 0.1], [0.2, 0.5], [0.1, 0.5]],
            [[0.6, 0.2], [0.8, 0.2], [0.8, 0.7], [0.6, 0.7]]
        ],
        polygon_mask=[[0.0, 0.85], [1.0, 0.85], [1.0, 1.0], [0.0, 1.0]],
        mask_tracking_only=True
    )

    all_masks = cam.get_all_polygon_masks()
    assert len(all_masks) == 3
    assert cam.mask_tracking_only is True


def test_optical_tracker_skips_obstacle_motion():
    config = SkyViewConfig()
    # Configure a camera with a tall pole mask directly in center of FOV
    cam = CameraConfig(
        id="cam_pole",
        name="Front Yard",
        enabled=True,
        extrinsics=CameraExtrinsics(azimuth_deg=0.0, altitude_deg=45.0, roll_deg=0.0),
        intrinsics=CameraIntrinsics(fov_deg=90.0),
        polygon_masks=[
            # Center pole in camera frame: x: 0.45..0.55, y: 0.1..0.9
            [[0.45, 0.10], [0.55, 0.10], [0.55, 0.90], [0.45, 0.90]]
        ],
        mask_tracking_only=True
    )
    config.cameras = [cam]

    tracker = OpticalTrackerEngine(config)

    # Point at Az=0.0, Alt=45.0 (which projects to center of camera cam_pole)
    # This should be inside the pole obstacle mask!
    is_masked = tracker.is_point_in_obstacle_mask(alt=45.0, az=0.0)
    assert is_masked is True

    # Point at Az=90.0, Alt=45.0 (far to the East, outside this camera's FOV / mask)
    is_masked_sky = tracker.is_point_in_obstacle_mask(alt=45.0, az=90.0)
    assert is_masked_sky is False
