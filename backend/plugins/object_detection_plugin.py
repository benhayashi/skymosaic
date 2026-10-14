from typing import Any, Dict, List
import cv2
import numpy as np

from backend.plugins.base import BaseVisionPlugin


class ObjectDetectionPlugin(BaseVisionPlugin):
    """
    Vision-based object detector for birds, planes, drones (daytime)
    and transient meteor streaks (nighttime).
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="object_detector", enabled=enabled)

    def process_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled or frame is None:
            return {"detections": []}

        h, w = frame.shape[:2]
        is_day = metadata.get("is_day", False)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        detections: List[Dict[str, Any]] = []

        if is_day:
            # Daytime: Look for dark silhouettes against bright sky
            # Focus on upper sky region
            blurred = cv2.GaussianBlur(gray, (5, 5), 1.5)
            # Sky threshold
            sky_thresh = np.percentile(blurred, 60)
            _, dark_objects = cv2.threshold(blurred, sky_thresh * 0.6, 255, cv2.THRESH_BINARY_INV)

            contours, _ = cv2.findContours(dark_objects, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if 20 < area < 2500:
                    x, y, bw, bh = cv2.boundingRect(cnt)
                    # Exclude ground perimeter
                    if 0.1 * h < y < 0.85 * h and 0.05 * w < x < 0.95 * w:
                        aspect = float(bw) / max(1, bh)
                        # Bird wingspan vs plane vs drone
                        if 1.4 < aspect < 4.0 and area < 400:
                            label = "bird"
                            confidence = 0.82
                        elif aspect > 2.0 and area >= 400:
                            label = "airplane_silhouette"
                            confidence = 0.88
                        else:
                            label = "aerial_object"
                            confidence = 0.70

                        detections.append({
                            "label": label,
                            "confidence": confidence,
                            "bbox": [x, y, bw, bh],
                            "area": int(area),
                            "center_x": int(x + bw / 2),
                            "center_y": int(y + bh / 2)
                        })

        else:
            # Nighttime: Linear high-speed streak detection (meteor / fireball)
            # Find elongated bright structures
            _, bright_thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(bright_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = cv2.contourArea(cnt)
                if 25 < area < 1500:
                    rect = cv2.minAreaRect(cnt)
                    (rx, ry), (rw, rh), angle = rect
                    major = max(rw, rh)
                    minor = max(1.0, min(rw, rh))
                    elongation = major / minor
                    if elongation > 3.5:  # Distinct streak
                        detections.append({
                            "label": "meteor_streak",
                            "confidence": round(min(0.95, 0.6 + elongation * 0.05), 2),
                            "bbox": [int(rx - major / 2), int(ry - minor / 2), int(major), int(minor)],
                            "length_px": round(major, 1),
                            "angle_deg": round(angle, 1),
                            "center_x": int(rx),
                            "center_y": int(ry)
                        })

        return {
            "count": len(detections),
            "detections": detections[:10]
        }
