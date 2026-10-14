from typing import Any, Dict
import cv2
import numpy as np

from backend.plugins.base import BaseVisionPlugin


class WeatherClassifierPlugin(BaseVisionPlugin):
    """
    Automated vision-based sky condition and cloud cover estimator.
    Uses Red-to-Blue Ratio (RBR) for daytime all-sky cloud segmentation
    and luminance/star-contrast metrics for nighttime cloud estimation.
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="weather_classifier", enabled=enabled)

    def process_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled or frame is None:
            return {"status": "disabled"}

        h, w = frame.shape[:2]
        # Crop or focus on central 70% of sky to avoid ground horizon artifacts
        cx, cy = w // 2, h // 2
        r_inner = int(min(w, h) * 0.35)

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (cx, cy), r_inner, 255, -1)

        sun_alt = metadata.get("sun_altitude_deg", -20.0)
        is_day = sun_alt > 0.0

        b = frame[:, :, 0].astype(np.float32)
        g = frame[:, :, 1].astype(np.float32)
        r = frame[:, :, 2].astype(np.float32)

        valid_pixels = mask > 0

        if is_day:
            # Daytime: Red-to-Blue ratio (RBR) method
            # Clear blue sky has low R/B (< 0.65). Clouds have high R/B (> 0.75).
            rbr = (r + 1.0) / (b + 1.0)
            cloud_mask = (rbr > 0.72) & valid_pixels
            cloud_fraction = float(np.count_nonzero(cloud_mask)) / float(np.count_nonzero(valid_pixels))

            clarity_score = round(max(0.0, min(100.0, (1.0 - cloud_fraction) * 100.0)), 1)
        else:
            # Nighttime: Mean sky luminance and contrast variance
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            sample = gray[valid_pixels]
            mean_lum = float(np.mean(sample))
            std_lum = float(np.std(sample))

            # Clear night skies have low mean luminance (< 25) with star variance
            # Overcast night skies near city light pollution glow flat orange/gray with low star variance
            if mean_lum > 60:
                cloud_fraction = 0.85
            elif mean_lum > 35:
                cloud_fraction = 0.50
            else:
                cloud_fraction = 0.10

            clarity_score = round(max(0.0, min(100.0, 100.0 - cloud_fraction * 100.0)), 1)

        cloud_percentage = round(cloud_fraction * 100.0, 1)
        octas = int(round(cloud_fraction * 8.0))

        if cloud_percentage < 15.0:
            condition = "Clear Sky"
        elif cloud_percentage < 45.0:
            condition = "Partly Cloudy"
        elif cloud_percentage < 80.0:
            condition = "Mostly Cloudy"
        else:
            condition = "Overcast"

        return {
            "is_day": is_day,
            "condition": condition,
            "cloud_cover_percent": cloud_percentage,
            "cloud_cover_octas": f"{octas}/8",
            "clarity_score": clarity_score,
            "sun_altitude_deg": round(sun_alt, 2),
            "sky_state": metadata.get("sky_state", "night")
        }
