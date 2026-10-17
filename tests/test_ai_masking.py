import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from backend.config import AIVisionMaskingConfig
from backend.plugins.ai_masking_service import (
    AIVisionMaskingService,
    encode_image_base64,
    parse_ai_json_response,
    refine_box_to_polygon,
)


def test_encode_image_base64():
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    b64 = encode_image_base64(img)
    assert isinstance(b64, str)
    assert len(b64) > 0


def test_parse_ai_json_response():
    # Markdown wrapped json
    raw = '```json\n{"obstacles": [{"label": "tree", "box_2d": [100, 200, 300, 400]}]}\n```'
    parsed = parse_ai_json_response(raw)
    assert len(parsed["obstacles"]) == 1
    assert parsed["obstacles"][0]["label"] == "tree"
    assert parsed["obstacles"][0]["box_2d"] == [100, 200, 300, 400]

    # Raw text with JSON embedded
    raw_mixed = 'Here are the obstacles:\n{"obstacles": [{"label": "pole", "box_2d": [50, 50, 500, 100]}]}\nHope this helps!'
    parsed_mixed = parse_ai_json_response(raw_mixed)
    assert len(parsed_mixed["obstacles"]) == 1
    assert parsed_mixed["obstacles"][0]["label"] == "pole"

    # Malformed text
    parsed_bad = parse_ai_json_response("No obstacles found here at all.")
    assert parsed_bad == {"obstacles": []}


def test_refine_box_to_polygon():
    img = np.full((200, 200, 3), 180, dtype=np.uint8)
    # Draw a dark tree square inside img
    img[50:150, 50:150] = 30

    box = [250, 250, 750, 750]  # normalized 0-1000 coordinates (y: 50..150, x: 50..150)
    poly = refine_box_to_polygon(img, box)
    assert poly is not None
    assert len(poly) >= 3  # must form a valid polygon
    for pt in poly:
        assert len(pt) == 2
        assert 0.0 <= pt[0] <= 1.0
        assert 0.0 <= pt[1] <= 1.0


def test_ai_masking_service_ollama_mock():
    cfg = AIVisionMaskingConfig(
        enabled=True,
        provider="ollama",
        api_url="http://localhost:11434",
        model="llama3.2-vision",
    )
    service = AIVisionMaskingService(cfg)

    fake_response = {
        "response": json.dumps({
            "obstacles": [
                {"label": "pine_tree", "box_2d": [200, 300, 800, 600]}
            ]
        })
    }

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        img = np.full((300, 400, 3), 150, dtype=np.uint8)
        obstacles = service.auto_mask_frame(img)
        assert len(obstacles) == 1
        assert "Pine Tree" in obstacles[0]["label"]
        assert len(obstacles[0]["polygon"]) >= 4


def test_ai_masking_service_openai_mock():
    cfg = AIVisionMaskingConfig(
        enabled=True,
        provider="openai_compatible",
        api_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        api_key="sk-test",
    )
    service = AIVisionMaskingService(cfg)

    fake_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "obstacles": [
                            {"label": "utility_pole", "box_2d": [100, 450, 900, 500]}
                        ]
                    })
                }
            }
        ]
    }

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake_response
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        img = np.full((300, 400, 3), 150, dtype=np.uint8)
        obstacles = service.auto_mask_frame(img)
        assert len(obstacles) == 1
        assert "Utility Pole" in obstacles[0]["label"]
        assert len(obstacles[0]["polygon"]) >= 4


def test_ai_masking_service_test_connection_offline():
    cfg = AIVisionMaskingConfig(
        enabled=True,
        provider="ollama",
        api_url="http://127.0.0.1:9999",  # unroutable/closed port
        model="llama3.2-vision",
    )
    service = AIVisionMaskingService(cfg)
    result = service.test_connection()
    assert result["reachable"] is False
    assert "error" in result
