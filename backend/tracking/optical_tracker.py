import datetime
import math
import threading
import time
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

from backend.astrometry.camera_model import (
    build_camera_rotation_matrix,
    project_world_point_to_camera,
)
from backend.astrometry.celestial import get_all_celestial_objects, get_sun_coordinates
from backend.plugins.metar_service import (
    fetch_metar,
    evaluate_aircraft_visibility,
    evaluate_celestial_visibility,
)
from backend.plugins.object_detection_plugin import ObjectDetectionPlugin
from backend.plugins.weather_plugin import WeatherClassifierPlugin
from backend.stitching.warp_lut import point_in_polygon
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
        self._lock = threading.RLock()
        self._cam_sizes: Dict[str, Tuple[int, int]] = {}
        self._frame_counter = 0
        self._cached_vision: List[Dict] = []
        self._cached_weather: Dict = {}
        self.vision_every_n = 5
        self.weather_every_n = 10

    def set_camera_sizes(self, sizes: Dict[str, Tuple[int, int]]) -> None:
        """Provides native camera frame sizes so obstacle masks are projected with the right aspect."""
        self._cam_sizes = dict(sizes)

    def update_config(self, config: SkyViewConfig) -> None:
        self.config = config
        self.adsb_tracker.config = config.adsb
        self.adsb_tracker.update_observer(config.observer)
        self.satellite_tracker.update_config(config.satellites)
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

    def is_point_in_obstacle_mask(self, alt: float, az: float) -> bool:
        """
        Returns True if the sky coordinate (alt, az) falls within any configured camera's
        obstacle polygon masks (trees, poles, antennas, buildings).
        """
        alt_rad = math.radians(alt)
        az_rad = math.radians(az)
        cos_alt = math.cos(alt_rad)
        v_world = np.array([
            cos_alt * math.sin(az_rad),
            cos_alt * math.cos(az_rad),
            math.sin(alt_rad)
        ], dtype=np.float64)

        for cam in self.config.cameras:
            if not cam.enabled:
                continue
            all_masks = cam.get_all_polygon_masks() if hasattr(cam, "get_all_polygon_masks") else (
                cam.polygon_masks if getattr(cam, "polygon_masks", None) else [cam.polygon_mask]
            )
            if not all_masks:
                continue

            # Project into camera coordinates (using standard reference 1920x1080 for normalized coords)
            R_w2c = build_camera_rotation_matrix(cam.extrinsics)
            cw, ch = self._cam_sizes.get(cam.id, (1920, 1080))
            pt = project_world_point_to_camera(v_world, R_w2c, cam.intrinsics, cw, ch)
            if pt is not None:
                px, py = pt
                norm_x = px / float(cw)
                norm_y = py / float(ch)
                if 0.0 <= norm_x <= 1.0 and 0.0 <= norm_y <= 1.0:
                    if point_in_polygon(px, py, all_masks, cw, ch):
                        return True
        return False

    def process_stitched_frame(self, frame: np.ndarray) -> Dict:
        with self._lock:
            return self._process_stitched_frame(frame)

    def _process_stitched_frame(self, frame: np.ndarray) -> Dict:
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
        self._frame_counter += 1
        if self._frame_counter % self.weather_every_n == 1 or not self._cached_weather:
            self._cached_weather = self.weather_plugin.process_frame(
                frame,
                metadata={
                    "sun_altitude_deg": sun_info["altitude_deg"],
                    "sky_state": sun_info["sky_state"],
                    "is_day": sun_info["sky_state"] == "day"
                }
            )
        weather_output = self._cached_weather

        # 3. Detect moving optical targets via frame differencing
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 1.0)

        scale = w / 1200.0  # thresholds are tuned for a 1200px canvas
        detected_centroids = []
        global_motion = False
        if self._prev_frame is not None and self._prev_frame.shape == blurred.shape:
            diff = cv2.absdiff(blurred, self._prev_frame)
            _, thresh = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
            # Global-change rejection (exposure / IR-cut switch / lightning): ignore the whole frame
            changed_frac = float(np.count_nonzero(thresh)) / float(thresh.size)
            if changed_frac > 0.04:
                global_motion = True
            else:
                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                min_area = max(2.0, 4.0 * scale * scale)
                max_area = 600.0 * scale * scale
                min_elev = getattr(self.config.stitching, "min_elevation_deg", 0.0)
                for cnt in contours:
                    area = cv2.contourArea(cnt)
                    if min_area <= area <= max_area:
                        M = cv2.moments(cnt)
                        if M["m00"] > 0:
                            cx = M["m10"] / M["m00"]
                            cy = M["m01"] / M["m00"]
                            # Filter out static ground objects and traffic below horizon
                            alt, az = self.pixel_to_altaz(cx, cy, w, h)
                            if alt >= min_elev:
                                # Filter out motion originating within obstacle masks (swaying trees, vibrating poles)
                                if not self.is_point_in_obstacle_mask(alt, az):
                                    detected_centroids.append((cx, cy))

        self._prev_frame = blurred
        if global_motion:
            self._tracks.clear()

        # 4. Associate detected centroids with active tracks
        unmatched_detections = list(detected_centroids)
        active_tracks_list = []

        for track_id, track in list(self._tracks.items()):
            cur_u, cur_v = track.current_pos
            best_det = None
            min_dist = 40.0 * scale  # max pixel association distance

            for det in unmatched_detections:
                dist = math.hypot(det[0] - cur_u, det[1] - cur_v)
                if dist < min_dist:
                    min_dist = dist
                    best_det = det

            if best_det is not None:
                track.add_point(best_det[0], best_det[1], now)
                unmatched_detections.remove(best_det)
                active_tracks_list.append(track)
            elif now - track.last_update > 4.0:
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

        metar = None
        if obs.metar_airport_code:
            metar = fetch_metar(obs.metar_airport_code)

        # A) Add known prominent celestial objects (Sun, Moon, Planets & bright stars)
        for obj in visible_celestial:
            is_sun_moon = obj.get("type") in ("sun", "moon") or obj.get("id") in ("sun", "moon")
            is_prominent = is_sun_moon or obj.get("mag", 5.0) <= 1.0 or obj.get("type") == "planet"
            if is_prominent:
                vis_eval = evaluate_celestial_visibility(
                    sun_alt_deg=sun_info["altitude_deg"],
                    obj_type="moon" if is_sun_moon else "star",
                    metar=metar,
                    mode=obs.sky_visibility_mode
                )
                if not vis_eval["visible"] and obs.sky_visibility_mode == "adaptive":
                    continue

                pt = self.altaz_to_pixel(obj["altitude_deg"], obj["azimuth_deg"], w, h)
                if pt and 0 <= pt[0] < w and 0 <= pt[1] < h:
                    color = "#fbbf24" if is_sun_moon else "#facc15"
                    classified_targets.append({
                        "id": f"cel_{obj['id']}",
                        "category": "celestial",
                        "subtype": obj.get("type", "star"),
                        "label": f"{obj['name']} ({obj.get('mag', '')}m)" if "mag" in obj else obj["name"],
                        "pixel_x": round(pt[0], 1),
                        "pixel_y": round(pt[1], 1),
                        "altitude_deg": obj["altitude_deg"],
                        "azimuth_deg": obj["azimuth_deg"],
                        "speed_deg_s": 0.0,
                        "color": color,
                        "visible": vis_eval["visible"],
                        "visibility_status": "visible",
                        "visibility_reason": vis_eval["reason"],
                        "telemetry": obj
                    })

        # B) Add known ADS-B Aircraft projected onto dome (with METAR adaptive visibility)

        for ac in visible_aircraft:
            pt = self.altaz_to_pixel(ac["altitude_deg"], ac["azimuth_deg"], w, h)
            if pt and 0 <= pt[0] < w and 0 <= pt[1] < h:
                dist_km = ac.get("range_km", 0.0)
                dist_mi = dist_km * 0.621371
                vis_eval = evaluate_aircraft_visibility(
                    distance_miles=dist_mi,
                    altitude_ft=ac.get("altitude_ft"),
                    metar=metar,
                    mode=obs.sky_visibility_mode
                )
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
                    "color": "#06b6d4" if vis_eval["visible"] else "#64748b",
                    "visible": vis_eval["visible"],
                    "visibility_status": vis_eval["status"],
                    "visibility_reason": vis_eval["reason"],
                    "range_miles": round(dist_mi, 1),
                    "range_km": round(dist_km, 1),
                    "telemetry": ac
                })

        # C) Add active orbiting Satellites (with celestial visibility evaluation)
        for sat in visible_satellites:
            pt = self.altaz_to_pixel(sat["altitude_deg"], sat["azimuth_deg"], w, h)
            if pt and 0 <= pt[0] < w and 0 <= pt[1] < h:
                vis_eval = evaluate_celestial_visibility(
                    sun_alt_deg=sun_info["altitude_deg"],
                    obj_type="satellite",
                    metar=metar,
                    mode=obs.sky_visibility_mode
                )
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
                    "color": "#c084fc" if vis_eval["visible"] else "#64748b",
                    "visible": vis_eval["visible"],
                    "visibility_status": "visible" if vis_eval["visible"] else "obscured",
                    "visibility_reason": vis_eval["reason"],
                    "telemetry": sat
                })

        # D) Classify optical moving tracks. Tracks explained by ADS-B or satellites are already drawn
        #    from their own feed (A/B/C above), so they are NOT emitted again (no duplicates).
        r_max = (min(w, h) / 2.0) * self.config.stitching.horizon_mask_radius
        min_motion = self.config.tracking.min_motion_pixels * scale
        for track in active_tracks_list:
            if len(track.points) < 5:
                continue
            duration = track.points[-1][2] - track.points[0][2]
            if duration < 1.5:
                continue

            tu, tv = track.current_pos
            alt, az = self.pixel_to_altaz(tu, tv, w, h)
            vel_px = track.velocity_px_s
            # Convert pixel velocity to angular velocity on dome (90 deg / r_max px)
            vel_deg_s = round((vel_px / max(1.0, r_max)) * 90.0, 2)
            net_disp = math.hypot(tu - track.points[0][0], tv - track.points[0][1])

            # Jitter / noise: tracks that don't actually travel are ignored
            if net_disp < min_motion * (len(track.points) - 1):
                track.classification = "static"
                continue

            matched_plane = self.adsb_tracker.correlate_target(alt, az, tolerance_deg=self.config.tracking.match_tolerance_deg)
            if matched_plane:
                track.classification = "airplane"
                track.label = f"{matched_plane['flight']} (ADS-B)"
                track.telemetry = matched_plane
                continue

            matched_sat = self.satellite_tracker.correlate_target(alt, az, tolerance_deg=self.config.tracking.match_tolerance_deg)
            if matched_sat:
                track.classification = "satellite"
                track.label = matched_sat["name"]
                track.telemetry = matched_sat
                continue

            if vel_deg_s < 0.15:
                track.classification = "drift"  # too slow to be a transient light (clouds, noise)
                continue

            # Must be a distinct bright point against its local surroundings
            iu, iv = int(min(max(tu, 0), w - 1)), int(min(max(tv, 0), h - 1))
            y0, y1, x0, x1 = max(0, iv - 12), min(h, iv + 13), max(0, iu - 12), min(w, iu + 13)
            patch = gray[y0:y1, x0:x1]
            if patch.size == 0 or float(gray[iv, iu]) - float(np.median(patch)) < 15.0:
                track.classification = "faint"
                continue

            # UNIDENTIFIED LIGHT
            track.classification = "unidentified"
            track.label = f"Unidentified light #{track.track_id} ({vel_deg_s}°/s)"
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
                "subtype": "Unidentified light",
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

        # 6. Run Object Vision Plugin (birds/drones/meteors) at a reduced cadence
        if self._frame_counter % self.vision_every_n == 1 or global_motion:
            vision_output = self.object_plugin.process_frame(
                frame,
                metadata={"is_day": sun_info["sky_state"] == "day"}
            )
            self._cached_vision = vision_output.get("detections", [])

        for idx, det in enumerate(self._cached_vision):
            cx, cy = det["center_x"], det["center_y"]
            alt, az = self.pixel_to_altaz(cx, cy, w, h)
            if alt < getattr(self.config.stitching, "min_elevation_deg", 0.0) or self.is_point_in_obstacle_mask(alt, az):
                continue
            classified_targets.append({
                "id": f"vision_{idx}",
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
            "metar": metar,
            "sky_visibility_mode": obs.sky_visibility_mode,
            "canvas_width": w,
            "canvas_height": h,
            "adsb_status": self.adsb_tracker.get_status(),
            "satellite_status": self.satellite_tracker.get_status(),
            "global_motion": global_motion,
            "targets_count": len(classified_targets),
            "targets": classified_targets,
            "unidentified_count": len(self._unidentified_events)
        }

    def get_unidentified_events(self) -> List[Dict]:
        return list(reversed(self._unidentified_events))
