"""
AI Vision Masking Service
Integrates local VLM servers (Ollama, LM Studio) or OpenAI-compatible vision APIs
to automatically locate and segment stationary foreground obstructions
(trees, foliage, utility poles, antennas, wires, buildings, and ground).
"""

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import cv2
import httpx
import numpy as np

from backend.config import AIVisionMaskingConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert astronomical all-sky computer vision system.
Your job is to identify all stationary foreground obstructions in this camera image that obstruct the sky view (such as trees, tree branches, utility poles, light posts, power lines, fences, rooflines, buildings, and ground).
Return ONLY a valid JSON object matching this schema:
{
  "obstacles": [
    {
      "label": "tree" | "utility_pole" | "building" | "ground" | "obstruction",
      "box_2d": [ymin, xmin, ymax, xmax]
    }
  ]
}
Where coordinates ymin, xmin, ymax, xmax are normalized between 0 and 1000 (0 = top/left, 1000 = bottom/right).
Do not include any conversational markdown, explanation, or text outside the raw JSON object.
"""


def encode_image_base64(img: np.ndarray, max_dim: int = 1280, quality: int = 85) -> str:
    """Resizes and encodes an image to a JPEG base64 string."""
    h, w = img.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ret, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ret:
        raise ValueError("Failed to encode image to JPEG.")
    return base64.b64encode(buf.tobytes()).decode("utf-8")


def parse_ai_json_response(text: str) -> Dict[str, Any]:
    """Extracts and parses JSON object from AI model text response."""
    # Strip markdown code blocks if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Search for first JSON object {...}
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return {"obstacles": []}


def refine_box_to_polygon(
    img: np.ndarray,
    box_2d: List[float],
    approx_epsilon_px: float = 3.0
) -> Optional[List[List[float]]]:
    """
    Refines a coarse bounding box into a contour polygon using local color/gradient contrast.
    If contrast refinement yields poor results, falls back to the rectangular box polygon.
    """
    h, w = img.shape[:2]
    ymin = max(0, min(1000, box_2d[0])) / 1000.0
    xmin = max(0, min(1000, box_2d[1])) / 1000.0
    ymax = max(0, min(1000, box_2d[2])) / 1000.0
    xmax = max(0, min(1000, box_2d[3])) / 1000.0

    if ymax <= ymin or xmax <= xmin:
        return None

    px_ymin = int(round(ymin * h))
    px_xmin = int(round(xmin * w))
    px_ymax = int(round(ymax * h))
    px_xmax = int(round(xmax * w))

    roi = img[px_ymin:px_ymax, px_xmin:px_xmax]
    if roi.size == 0 or roi.shape[0] < 8 or roi.shape[1] < 8:
        # Simple rectangle polygon
        return [
            [round(xmin, 4), round(ymin, 4)],
            [round(xmax, 4), round(ymin, 4)],
            [round(xmax, 4), round(ymax, 4)],
            [round(xmin, 4), round(ymax, 4)]
        ]

    # Threshold dark foliage/pole structures against lighter sky within ROI
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray_roi, (5, 5), 1.5)
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Edge filter
    sobel = cv2.Sobel(blurred, cv2.CV_32F, 1, 1, ksize=3)
    edges = cv2.normalize(np.abs(sobel), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, edge_thresh = cv2.threshold(edges, 40, 255, cv2.THRESH_BINARY)
    combined = cv2.bitwise_or(thresh, edge_thresh)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    closed = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        largest_cnt = max(contours, key=cv2.contourArea)
        if cv2.contourArea(largest_cnt) > 50:
            approx = cv2.approxPolyDP(largest_cnt, approx_epsilon_px, closed=True)
            if len(approx) >= 3:
                poly = []
                for pt in approx:
                    gx = float(px_xmin + pt[0][0]) / float(w)
                    gy = float(px_ymin + pt[0][1]) / float(h)
                    poly.append([round(max(0.0, min(1.0, gx)), 4), round(max(0.0, min(1.0, gy)), 4)])
                return poly

    # Fallback to rectangular box polygon
    return [
        [round(xmin, 4), round(ymin, 4)],
        [round(xmax, 4), round(ymin, 4)],
        [round(xmax, 4), round(ymax, 4)],
        [round(xmin, 4), round(ymax, 4)]
    ]


class AIVisionMaskingService:
    """Service client for calling vision models and producing obstacle polygons."""

    def __init__(self, config: AIVisionMaskingConfig):
        self.config = config

    def update_config(self, config: AIVisionMaskingConfig):
        self.config = config

    def test_connection(self) -> Dict[str, Any]:
        """Tests connectivity to the configured AI server and returns available models."""
        base_url = self.config.api_url.rstrip("/")
        provider = self.config.provider.lower()
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            with httpx.Client(timeout=5.0) as client:
                if provider == "ollama":
                    resp = client.get(f"{base_url}/api/tags")
                    if resp.status_code == 200:
                        data = resp.json()
                        models = [m.get("name") for m in data.get("models", [])]
                        return {
                            "status": "ok",
                            "provider": "ollama",
                            "connected": True,
                            "reachable": True,
                            "models": models,
                            "message": f"Connected to Ollama! Found {len(models)} installed model(s)."
                        }
                    return {"status": "error", "connected": False, "reachable": False, "error": f"Ollama HTTP {resp.status_code}: {resp.text}", "message": f"Ollama HTTP {resp.status_code}: {resp.text}"}

                elif provider in ("lmstudio", "openai_compatible"):
                    # Handle base URLs that already end with /v1
                    clean_url = base_url if not base_url.endswith("/v1") else base_url[:-3]
                    resp = client.get(f"{clean_url}/v1/models", headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        models = [m.get("id") for m in data.get("data", [])]
                        return {
                            "status": "ok",
                            "provider": provider,
                            "connected": True,
                            "reachable": True,
                            "models": models,
                            "message": f"Connected to {provider.upper()} API! Found {len(models)} model(s)."
                        }
                    return {"status": "error", "connected": False, "reachable": False, "error": f"API HTTP {resp.status_code}: {resp.text}", "message": f"API HTTP {resp.status_code}: {resp.text}"}
                else:
                    return {"status": "error", "connected": False, "reachable": False, "error": f"Unknown provider: {provider}", "message": f"Unknown provider: {provider}"}
        except Exception as e:
            return {"status": "error", "connected": False, "reachable": False, "error": str(e), "message": f"Connection failed: {str(e)}"}

    def auto_mask_frame(self, img: np.ndarray) -> List[Dict[str, Any]]:
        """
        Submits camera frame to local/cloud AI vision model and translates
        detected obstructions into tight polygonal masks.
        """
        if img is None or img.size == 0:
            return []

        base_url = self.config.api_url.rstrip("/")
        provider = self.config.provider.lower()
        img_b64 = encode_image_base64(img)

        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        prompt = SYSTEM_PROMPT
        raw_response_text = ""

        try:
            with httpx.Client(timeout=self.config.timeout_sec) as client:
                if provider == "ollama":
                    # Ollama vision generate API
                    payload = {
                        "model": self.config.model,
                        "prompt": prompt,
                        "images": [img_b64],
                        "stream": False,
                        "format": "json"
                    }
                    resp = client.post(f"{base_url}/api/generate", json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    raw_response_text = data.get("response", "")

                elif provider in ("lmstudio", "openai_compatible"):
                    # OpenAI-compatible /v1/chat/completions
                    clean_url = base_url if not base_url.endswith("/v1") else base_url[:-3]
                    payload = {
                        "model": self.config.model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                                    }
                                ]
                            }
                        ],
                        "temperature": 0.1,
                        "max_tokens": 1024
                    }
                    resp = client.post(f"{clean_url}/v1/chat/completions", json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        raw_response_text = choices[0].get("message", {}).get("content", "")
                else:
                    raise ValueError(f"Unsupported AI provider: {provider}")

        except Exception as e:
            logger.error("AI vision masking request failed: %s", e)
            raise RuntimeError(f"AI auto-masking failed on {provider}: {e}")

        parsed = parse_ai_json_response(raw_response_text)
        obstacles_data = parsed.get("obstacles", [])

        results: List[Dict[str, Any]] = []
        for idx, obs in enumerate(obstacles_data):
            box = obs.get("box_2d")
            label = obs.get("label", "obstruction").replace("_", " ").title()
            if not box or len(box) != 4:
                continue

            polygon = refine_box_to_polygon(img, box)
            if polygon and len(polygon) >= 3:
                results.append({
                    "id": f"ai_obs_{idx + 1}",
                    "label": f"AI {label} #{idx + 1}",
                    "polygon": polygon,
                    "box_2d": box
                })

        return results
