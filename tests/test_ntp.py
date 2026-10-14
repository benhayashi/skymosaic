import pytest
from backend.tracking.ntp_client import NTPClient
from backend.config import get_default_config
from backend.stitching.stitcher import MultiCameraStitcher
import numpy as np


def test_ntp_client_sync_and_formatting():
    client = NTPClient(server="pool.ntp.org", timeout=2.0)
    # Test fallback or live sync
    synced = client.sync()
    # Regardless of network connectivity in tests, formatting must be valid
    zulu = client.format_timestamp("zulu")
    local = client.format_timestamp("local", "America/New_York")
    
    assert "UTC" in zulu or "Z" in zulu
    assert len(local) > 10
    status = client.get_status()
    assert "synced" in status
    assert "offset_ms" in status


def test_stitcher_timestamp_overlay():
    cfg = get_default_config()
    cfg.stitching.overlay_timestamp = True
    cfg.stitching.time_format = "zulu"
    stitcher = MultiCameraStitcher(cfg)
    
    # Create synthetic canvas
    img = np.zeros((1200, 1200, 3), dtype=np.uint8)
    annotated = stitcher.render_dome_annotations(img)
    assert annotated.shape == img.shape
    # Check that pixels were drawn (not all zeros)
    assert np.any(annotated > 0)
