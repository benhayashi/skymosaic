import datetime
import pytest
from fastapi.testclient import TestClient
from backend.api.app import app, dvr_manager
from backend.pipeline.dvr_manager import DVRManager


def test_dvr_manager_buffer_and_eviction():
    mgr = DVRManager(max_frames=5)
    base_time = datetime.datetime(2026, 10, 15, 2, 0, 0, tzinfo=datetime.timezone.utc)

    # Add 6 frames (1 over max capacity)
    for i in range(6):
        t = base_time + datetime.timedelta(seconds=i)
        mgr.add_frame(f"frame_{i}".encode("utf-8"), t)

    timeline = mgr.get_timeline()
    assert timeline["enabled"] is True
    assert timeline["frame_count"] == 5

    # Oldest frame (frame_0) should have been evicted; first frame should be frame_1
    first_frame, first_time = mgr.get_frame_by_offset(-4.0)
    assert first_frame == b"frame_1"

    # Latest frame should be frame_5 (offset 0.0)
    latest_frame, latest_time = mgr.get_frame_by_offset(0.0)
    assert latest_frame == b"frame_5"


def test_dvr_manager_timestamp_query():
    mgr = DVRManager(max_frames=10)
    base_time = datetime.datetime(2026, 10, 15, 2, 0, 0, tzinfo=datetime.timezone.utc)

    for i in range(5):
        t = base_time + datetime.timedelta(seconds=i * 10)  # 0s, 10s, 20s, 30s, 40s
        mgr.add_frame(f"frame_{i}".encode("utf-8"), t)

    # Query timestamp at 19 seconds -> should return frame_2 (20s)
    query_dt = base_time + datetime.timedelta(seconds=19)
    frame, found_dt = mgr.get_frame_by_timestamp(query_dt)
    assert frame == b"frame_2"
    assert found_dt == base_time + datetime.timedelta(seconds=20)


def test_dvr_api_endpoints():
    client = TestClient(app)

    # Ensure dvr_manager has at least one test frame
    dvr_manager.add_frame(b"\xff\xd8\xff\xe0test_jpeg_bytes\xff\xd9")

    # 1. Timeline endpoint
    res_timeline = client.get("/api/sky/dvr/timeline")
    assert res_timeline.status_code == 200
    data = res_timeline.json()
    assert data["enabled"] is True
    assert data["frame_count"] >= 1
    assert "start_time" in data
    assert "end_time" in data

    # 2. Frame endpoint with relative offset
    res_frame = client.get("/api/sky/dvr/frame?offset_sec=0")
    assert res_frame.status_code == 200
    assert res_frame.headers["content-type"] == "image/jpeg"
    assert "x-frame-timestamp" in res_frame.headers
    assert "x-frame-age-sec" in res_frame.headers
