import cv2
import numpy as np
import pytest

from backend.plugins.object_detection_plugin import ObjectDetectionPlugin
from backend.plugins.weather_plugin import WeatherClassifierPlugin


def test_weather_classifier_day_clear_sky():
    plugin = WeatherClassifierPlugin(enabled=True)

    # Synthetic daytime clear blue sky (B high, R low)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[:, :, 0] = 220  # Blue
    frame[:, :, 1] = 160  # Green
    frame[:, :, 2] = 80   # Red

    res = plugin.process_frame(frame, metadata={"sun_altitude_deg": 45.0, "is_day": True})
    assert res["is_day"] is True
    assert res["cloud_cover_percent"] < 25.0
    assert "Clear" in res["condition"]


def test_weather_classifier_day_overcast():
    plugin = WeatherClassifierPlugin(enabled=True)

    # Synthetic daytime white overcast sky (R ~ B ~ G)
    frame = np.full((200, 200, 3), 200, dtype=np.uint8)

    res = plugin.process_frame(frame, metadata={"sun_altitude_deg": 30.0, "is_day": True})
    assert res["is_day"] is True
    assert res["cloud_cover_percent"] > 70.0
    assert "Cloudy" in res["condition"] or "Overcast" in res["condition"]


def test_object_detector_night_meteor_streak():
    plugin = ObjectDetectionPlugin(enabled=True)

    # Night sky with bright linear streak
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    cv2.line(frame, (50, 50), (180, 190), (255, 255, 255), 3)

    res = plugin.process_frame(frame, metadata={"is_day": False})
    assert res["count"] >= 1
    det = res["detections"][0]
    assert det["label"] == "meteor_streak"
    assert det["confidence"] > 0.6


def test_object_detector_day_bird_silhouette():
    plugin = ObjectDetectionPlugin(enabled=True)

    # Bright day sky with a small dark silhouette
    frame = np.full((300, 300, 3), 220, dtype=np.uint8)
    # Dark bird shape
    cv2.ellipse(frame, (150, 150), (25, 8), 0, 0, 360, (20, 20, 20), -1)

    res = plugin.process_frame(frame, metadata={"is_day": True})
    assert res["count"] >= 1
    assert any(d["label"] in ("bird", "airplane_silhouette", "aerial_object") for d in res["detections"])
