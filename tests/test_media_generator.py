import os
import cv2
import numpy as np
import pytest

from backend.pipeline.media_generator import MediaGenerator


def test_keogram_and_startrails_generation(tmp_path):
    # Generate 3 dummy night sky frames
    paths = []
    for i in range(3):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        # Move a star across frames
        cv2.circle(img, (20 + i * 25, 50), 3, (255, 255, 255), -1)
        p = tmp_path / f"frame_{i}.jpg"
        cv2.imwrite(str(p), img)
        paths.append(str(p))

    # Test Keogram
    keogram_out = tmp_path / "keogram.jpg"
    res_k = MediaGenerator.generate_keogram(paths, str(keogram_out), slice_width=2)
    assert res_k is not None
    assert os.path.exists(res_k)
    k_img = cv2.imread(res_k)
    assert k_img.shape[0] == 100
    assert k_img.shape[1] == 3  # 3 frames -> 3 column slices


    # Test Startrails
    startrails_out = tmp_path / "startrails.jpg"
    res_s = MediaGenerator.generate_startrails(paths, str(startrails_out), threshold=0.01)
    assert res_s is not None
    assert os.path.exists(res_s)
    s_img = cv2.imread(res_s)
    assert s_img.shape == (100, 100, 3)
    # The trails image should have all 3 stars accumulated
    gray = cv2.cvtColor(s_img, cv2.COLOR_BGR2GRAY)
    assert np.count_nonzero(gray > 200) >= 3


def test_timelapse_generation(tmp_path):
    paths = []
    for i in range(4):
        img = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.putText(img, f"Frame {i}", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        p = tmp_path / f"tl_{i}.jpg"
        cv2.imwrite(str(p), img)
        paths.append(str(p))

    tl_out = tmp_path / "timelapse.mp4"
    res_tl = MediaGenerator.generate_timelapse(paths, str(tl_out), fps=5)
    assert res_tl is not None
    assert os.path.exists(res_tl)
    assert os.path.getsize(res_tl) > 100
