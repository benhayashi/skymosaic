import pytest
from fastapi.testclient import TestClient
from backend.api.app import app

client = TestClient(app)


def test_api_config():
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "observer" in data
    assert "cameras" in data
    assert "stitching" in data


def test_api_cameras_list():
    response = client.get("/api/cameras")
    assert response.status_code == 200
    cams = response.json()
    assert isinstance(cams, list)
    assert len(cams) >= 1


def test_api_astrometry_stars():
    response = client.get("/api/astrometry/stars")
    assert response.status_code == 200
    data = response.json()
    assert "stars" in data
    assert len(data["stars"]) > 0


def test_api_live_stitched_frame():
    response = client.get("/api/sky/live.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert len(response.content) > 1000


def test_api_camera_frame():
    response = client.get("/api/cameras/cam_north/frame")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_api_mode_switch():
    # Test GET mode
    res = client.get("/api/mode")
    assert res.status_code == 200
    assert "mode" in res.json()

    # Test POST mode change to long_exposure
    res_post = client.post("/api/mode", json={"mode": "long_exposure"})
    assert res_post.status_code == 200
    assert res_post.json()["mode"] == "long_exposure"

    # Switch back to feed
    res_back = client.post("/api/mode", json={"mode": "feed"})
    assert res_back.status_code == 200
    assert res_back.json()["mode"] == "feed"


def test_api_tracking_targets():
    res = client.get("/api/tracking/targets")
    assert res.status_code == 200
    data = res.json()
    assert "targets" in data
    assert "weather" in data
    assert "targets_count" in data


def test_api_tracking_adsb():
    res = client.get("/api/tracking/adsb")
    assert res.status_code == 200
    data = res.json()
    assert "aircraft" in data
    assert "count" in data


def test_api_tracking_satellites():
    res = client.get("/api/tracking/satellites")
    assert res.status_code == 200
    data = res.json()
    assert "satellites" in data


def test_api_weather_conditions():
    res = client.get("/api/weather/sky_conditions")
    assert res.status_code == 200
    data = res.json()
    assert "sun" in data
    assert "weather" in data
    assert "cloud_cover_percent" in data["weather"]


def test_api_unidentified_events():
    res = client.get("/api/events/unidentified")
    assert res.status_code == 200
    data = res.json()
    assert "events" in data
    assert "count" in data

