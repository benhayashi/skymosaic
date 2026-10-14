import datetime
import math
import time
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

from backend.astrometry.celestial import get_all_celestial_objects, get_sun_coordinates
from backend.config import ObserverConfig, SkyViewConfig, TrackingConfig
from backend.plugins.object_detection_plugin import ObjectDetectionPlugin
from backend.plugins.weather_plugin import WeatherClassifierPlugin
from backend.tracking.adsb_tracker import ADSBTracker
from backend.tracking.satellite_tracker import SatelliteTracker


class OpticalTrack:
    """Represents a continuous trajectory of an object across frames."""

    def __init__(self, track_id: int, initial_u: float, initial_v: float, t: float):
        self.track_id = track_id
        self.points: List[Tuple[float, float, float]] = [(initial_u, initial_v, t)]  # (u, v, timestamp)
        self.last_update = t
        self.classification = "unknown"
        self.label = f"Target #{track_id}"
        self.telemetry: Dict = {}
        self.is_unidentified = False

    def add_point(self, u: float, v: float, t: float):
        self.points.append((u, v, t))
        self.last_update = t
        if len(self.points) > 30:
            self.points.pop(0)

    @property
    def current_pos(self) -> Tuple[float, float]:
        return (self.points[-1][0], self.points[-1][1])

    @property
    def velocity_px_s(self) -> float:
        if len(self.points) < 2:
            return 0.0
        p_first = self.points[0]
        p_last = self.points[-1]
        dt = p_last[2] - p_first[2]
        if dt < 1e-4:
            return 0.0
        dist = math.hypot(p_last[0] - p_first[0], p_last[1] - p_first[1])
        return dist / dt


class OpticalTrackerEngine:
    """
    Real-time optical motion tracker and hierarchical classification fusion engine.
    Fuses celestial astrometry, satellite TLE ephemeris, ADS-B transponders,
    and AI vision plugins to categorize every light and target in the sky.
    """

    def __init__(self, config: SkyViewConfig):
        self.config = config
        self.adsb_tracker = ADSBTracker(config.adsb, config.observer)
        self.satellite_tracker = SatelliteTracker(config.satellites, config.observer)
        self.weather_plugin = WeatherClassifierPlugin(enabled=config.plugins.weather_enabled)
        self.object_plugin = ObjectDetectionPlugin(enabled=config.plugins.daytime_detector_enabled)

        self._prev_frame: Optional[np.ndarray] = None
        self._tracks: Dict[int, OpticalTrack] = {}
        self._next_track_id = 1
        self._unidentified_events: List[Dict] = []

    def update_config(self, config: SkyViewConfig) -> None:
        self.config = config
        self.adsb_tracker.config = config.adsb
        self.adsb_tracker.update_observer(config.observer)
        self.satellite_tracker.config = config.satellites
        self.satellite_tracker.update_observer(config.observer)
        self.weather_plugin.enabled = config.plugins.weather_enabled
        self.object_plugin.enabled = config.plugins.daytime_detector_enabled

    def pixel_to_altaz(self, u: float, v: float, canvas_w: int, canvas_h: int) -> Tuple[float, float]:
        """
        Converts pixel coordinates on the Zenithal Polar Dome to Topocentric (Alt, Az) degrees.
        """
        u0 = (canvas_w - 1) / 2.0
        v0 = (canvas_h - 1) / 2.0
        r_max = (min(canvas_w, canvas_h) / 2.0) * self.config.stitching.horizon_mask_radius

        du = u - u0
        dv = v - v0
        dist = math.hypot(du, dv)

        # Altitude (0 at horizon ring, 90 at center)
        theta = (dist / max(1.0, r_max)) * (math.pi / 2.0)
        alt = 90.0 - math.degrees(theta)
        # Azimuth (0 North = top, 90 East = right)
        az = math.degrees(math.atan2(du, -dv)) % 360.0

        return alt, az

    def altaz_to_pixel(self, alt: float, az: float, canvas_w: int, canvas_h: int) -> Optional[Tuple[float, float]]:
        """Converts Topocentric (Alt, Az) to pixel coordinates on the Zenithal Polar Dome."""
        if alt < -1.0:
            return None
        u0 = (canvas_w - 1) / 2.0
        v0 = (canvas_h - 1) / 2.0
        r_max = (min(canvas_w, canvas_h) / 2.0) * self.config.stitching.horizon_mask_radius

        theta = math.radians(90.0 - alt)
        r = (theta / (math.pi / 2.0)) * r_max
        az_rad = math.radians(az)

        px = u0 + r * math.sin(az_rad)
        py = v0 - r * math.cos(az_rad)
        return (px, py)

    def process_stitched_frame(self, frame: np.ndarray) -> Dict:
        """
        Runs motion detection, tracks active objects, executes multi-source
        identification hierarchy, and runs AI vision plugins.
        """
        now = time.time()
        dt_utc = datetime.datetime.now(datetime.timezone.utc)
        h, w = frame.shape[:2]

        # 1. Update ephemeris and external feeds
        obs = self.config.observer
        sun_info = get_sun_coordinates(dt_utc, obs.latitude, obs.longitude)
        visible_celestial = get_all_celestial_objects(dt_utc, obs.latitude, obs.longitude, min_alt_deg=0.0)
        visible_aircraft = self.adsb_tracker.poll_aircraft()
        visible_satellites = self.satellite_tracker.compute_satellite_passes(dt_utc)

        # 2. Run Weather Vision Plugin
        weather_output = self.weather_plugin.process_frame(
            frame,
            metadata={
                "sun_altitude_deg": sun_info["altitude_deg"],
                "sky_state": sun_info["sky_state"],
                "is_day": sun_info["sky_state"] == "day"
            }
        )

        # 3. Detect moving optical targets via frame differencing
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 1.0)

        detected_centroids = []
        if self._prev_frame is not None:
            diff = cv2.absdiff(blurred, self._prev_frame)
            _, thresh = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for cnt in contours:
                area = cv2.contourArea(cnt)
                if 4 <= area <= 600:
                    M = cv2.moments(cnt)
                    if M["m00"] > 0:
                        cx = M["m10"] / M["m00"]
                        cy = M["m01"] / M["m00"]
                        detected_centroids.append((cx, cy))

        self._prev_frame = blurred

        # 4. Associate detected centroids with active tracks
        unmatched_detections = list(detected_centroids)
        active_tracks_list = []

        for track_id, track in list(self._tracks.items()):
            cur_u, cur_v = track.current_pos
            best_det = None
            min_dist = 40.0  # max pixel association distance

            for det in unmatched_detections:
                dist = math.hypot(det[0] - cur_u, det[1] - cur_v)
                if dist < min_dist:
                    min_dist = dist
                    best_det = det

            if best_det is not None:
                track.add_point(best_det[0], best_det[1], now)
                unmatched_detections.remove(best_det)
                active_tracks_list.append(track)
            elif now - track.last_update > 2.0:
                # Track lost
                del self._tracks[track_id]

        # Create new tracks for remaining centroids
        for det in unmatched_detections:
            t = OpticalTrack(self._next_track_id, det[0], det[1], now)
            self._tracks[self._next_track_id] = t
            self._next_track_id += 1
            active_tracks_list.append(t)

        # 5. Hierarchical Object Classification
        classified_targets = []

        # A) Add known prominent celestial objects (Planets & bright stars)
        for obj in visible_celestial:
            if obj.get("mag", 5.0) <= 1.0 or obj.get("type") in ("planet", "moon"):
                pt = self.altaz_to_pixel(obj["altitude_deg"], obj["azimuth_deg"], w, h)
                if pt and 0 <= pt[0] < w and 0 <= pt[1] < h:
                    classified_targets.append({
                        "id": f"cel_{obj['id']}",
                        "category": "celestial",
                        "subtype": obj.get("type", "star"),
                        "label": f"{obj['name']} ({obj.get('mag', '')}m)",
                        "pixel_x": round(pt[0], 1),
                        "pixel_y": round(pt[1], 1),
                        "altitude_deg": obj["altitude_deg"],
                        "azimuth_deg": obj["azimuth_deg"],
                        "speed_deg_s": 0.0,
                        "color": "#facc15",  # Yellow
                        "telemetry": obj
                    })

        # B) Add known ADS-B Aircraft projected onto dome
        for ac in visible_aircraft:
            pt = self.altaz_to_pixel(ac["altitude_deg"], ac["azimuth_deg"], w, h)
            if pt and 0 <= pt[0] < w and 0 <= pt[1] < h:
                classified_targets.append({
                    "id": f"adsb_{ac['id']}",
                    "category": "airplane",
                    "subtype": ac["type"],
                    "label": f"{ac['flight']} (FL{ac['altitude_ft']//100})",
                    "pixel_x": round(pt[0], 1),
                    "pixel_y": round(pt[1], 1),
                    "altitude_deg": ac["altitude_deg"],
                    "azimuth_deg": ac["azimuth_deg"],
                    "speed_kts": ac["speed_kts"],
                    "speed_deg_s": round(ac["speed_kts"] * 0.514 / max(100.0, ac["range_km"] * 1000.0) * 57.3, 2),
                    "color": "#06b6d4",  # Cyan
                    "telemetry": ac
                })

        # C) Add active orbiting Satellites
        for sat in visible_satellites:
            pt = self.altaz_to_pixel(sat["altitude_deg"], sat["azimuth_deg"], w, h)
            if pt and 0 <= pt[0] < w and 0 <= pt[1] < h:
                classified_targets.append({
                    "id": f"sat_{sat['id']}",
                    "category": "satellite",
                    "subtype": "LEO",
                    "label": sat["name"],
                    "pixel_x": round(pt[0], 1),
                    "pixel_y": round(pt[1], 1),
                    "altitude_deg": sat["altitude_deg"],
                    "azimuth_deg": sat["azimuth_deg"],
                    "speed_deg_s": sat["angular_speed_deg_s"],
                    "color": "#c084fc",  # Purple
                    "telemetry": sat
                })

        # D) Classify optical moving tracks
        for track in active_tracks_list:
            if len(track.points) < 3:
                continue

            tu, tv = track.current_pos
            alt, az = self.pixel_to_altaz(tu, tv, w, h)
            vel_px = track.velocity_px_s
            # Convert pixel velocity to angular velocity on dome (90 deg / r_max px)
            r_max = (min(w, h) / 2.0) * self.config.stitching.horizon_mask_radius
            vel_deg_s = round((vel_px / max(1.0, r_max)) * 90.0, 2)

            # Check ADS-B match
            matched_plane = self.adsb_tracker.correlate_target(alt, az, tolerance_deg=self.config.tracking.match_tolerance_deg)
            if matched_plane:
                track.classification = "airplane"
                track.label = f"{matched_plane['flight']} (ADS-B)"
                track.telemetry = matched_plane
                continue

            # Check Satellite match
            matched_sat = self.satellite_tracker.correlate_target(alt, az, tolerance_deg=self.config.tracking.match_tolerance_deg)
            if matched_sat:
                track.classification = "satellite"
                track.label = matched_sat["name"]
                track.telemetry = matched_sat
                continue

            # Check Celestial (stationary)
            if vel_deg_s < 0.05:
                track.classification = "celestial"
                continue

            # UNIDENTIFIED AERIAL OBJECT (UAP / Transient)
            if vel_deg_s >= 0.15:
                track.classification = "unidentified"
                track.label = f"UAP #{track.track_id} ({vel_deg_s}°/s)"
                track.is_unidentified = True

                uap_record = {
                    "track_id": track.track_id,
                    "timestamp": dt_utc.isoformat(),
                    "pixel_x": round(tu, 1),
                    "pixel_y": round(tv, 1),
                    "altitude_deg": round(alt, 2),
                    "azimuth_deg": round(az, 2),
                    "angular_speed_deg_s": vel_deg_s,
                    "color": "#ef4444",  # Red Alert
                    "points_count": len(track.points)
                }

                classified_targets.append({
                    "id": f"uap_{track.track_id}",
                    "category": "unidentified",
                    "subtype": "UAP",
                    "label": track.label,
                    "pixel_x": round(tu, 1),
                    "pixel_y": round(tv, 1),
                    "altitude_deg": round(alt, 2),
                    "azimuth_deg": round(az, 2),
                    "speed_deg_s": vel_deg_s,
                    "color": "#ef4444",
                    "telemetry": uap_record
                })

                # Log event once per track (suppressed if in live_testing mode or auto_record is disabled)
                recording_enabled = (
                    self.config.mode != OperatingMode.LIVE_TESTING
                    and self.config.tracking.uap_auto_record
                )
                if recording_enabled and not any(e["track_id"] == track.track_id for e in self._unidentified_events):
                    self._unidentified_events.append(uap_record)
                    if len(self._unidentified_events) > 50:
                        self._unidentified_events.pop(0)

        # 6. Run Object Vision Plugin (birds/drones/meteors)
        vision_output = self.object_plugin.process_frame(
            frame,
            metadata={"is_day": sun_info["sky_state"] == "day"}
        )

        for det in vision_output.get("detections", []):
            cx, cy = det["center_x"], det["center_y"]
            alt, az = self.pixel_to_altaz(cx, cy, w, h)
            classified_targets.append({
                "id": f"vision_{len(classified_targets)}",
                "category": "vision_ai",
                "subtype": det["label"],
                "label": f"{det['label'].replace('_', ' ').title()} ({int(det['confidence']*100)}%)",
                "pixel_x": cx,
                "pixel_y": cy,
                "altitude_deg": round(alt, 1),
                "azimuth_deg": round(az, 1),
                "speed_deg_s": 0.0,
                "color": "#f97316",  # Orange
                "telemetry": det
            })

        return {
            "timestamp": dt_utc.isoformat(),
            "mode": self.config.mode.value,
            "sky_state": sun_info["sky_state"],
            "weather": weather_output,
            "targets_count": len(classified_targets),
            "targets": classified_targets,
            "unidentified_count": len(self._unidentified_events)
        }

    def get_unidentified_events(self) -> List[Dict]:
        return list(reversed(self._unidentified_events))
