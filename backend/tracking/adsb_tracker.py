import datetime
import math
import time
from typing import Dict, List, Optional, Tuple
import httpx
import numpy as np

from backend.config import ADSBConfig, ObserverConfig


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> np.ndarray:
    """Converts WGS84 Geodetic coordinates (lat, lon, height meters) to ECEF coordinates."""
    a = 6378137.0          # WGS84 equatorial semi-major axis in meters
    f = 1.0 / 298.257223563 # WGS84 flattening
    e2 = 2.0 * f - f * f    # First eccentricity squared

    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    N = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)

    x = (N + alt_m) * cos_lat * cos_lon
    y = (N + alt_m) * cos_lat * sin_lon
    z = (N * (1.0 - e2) + alt_m) * sin_lat
    return np.array([x, y, z], dtype=np.float64)


def ecef_to_enu(
    p_ecef: np.ndarray,
    obs_lat_deg: float,
    obs_lon_deg: float,
    obs_ecef: np.ndarray
) -> np.ndarray:
    """Transforms an ECEF point into Topocentric East-North-Up (ENU) coordinates relative to observer."""
    dx = p_ecef - obs_ecef
    lat_rad = math.radians(obs_lat_deg)
    lon_rad = math.radians(obs_lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    # Rotation matrix ECEF to ENU
    e = -sin_lon * dx[0] + cos_lon * dx[1]
    n = -sin_lat * cos_lon * dx[0] - sin_lat * sin_lon * dx[1] + cos_lat * dx[2]
    u = cos_lat * cos_lon * dx[0] + cos_lat * sin_lon * dx[1] + sin_lat * dx[2]
    return np.array([e, n, u], dtype=np.float64)


def aircraft_to_altaz(
    ac_lat: float,
    ac_lon: float,
    ac_alt_m: float,
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float
) -> Tuple[float, float, float]:
    """
    Computes topocentric (Altitude deg, Azimuth deg, Slant Range km) of an aircraft relative to observer.
    """
    obs_ecef = geodetic_to_ecef(obs_lat, obs_lon, obs_alt_m)
    ac_ecef = geodetic_to_ecef(ac_lat, ac_lon, ac_alt_m)
    enu = ecef_to_enu(ac_ecef, obs_lat, obs_lon, obs_ecef)

    e, n, u = float(enu[0]), float(enu[1]), float(enu[2])
    dist_horiz = math.hypot(e, n)
    slant_range_m = math.hypot(dist_horiz, u)

    alt_rad = math.atan2(u, dist_horiz)
    az_rad = math.atan2(e, n)

    alt_deg = math.degrees(alt_rad)
    az_deg = math.degrees(az_rad) % 360.0
    range_km = slant_range_m / 1000.0

    return alt_deg, az_deg, range_km


class SyntheticADSBGenerator:
    """Generates realistic simulated commercial flights passing through observer airspace."""

    def __init__(self, obs_lat: float, obs_lon: float):
        self.obs_lat = obs_lat
        self.obs_lon = obs_lon
        self.flights = [
            {"icao": "a01234", "flight": "UAL812", "type": "B789", "lat": obs_lat + 0.15, "lon": obs_lon - 0.25, "alt_ft": 36000, "speed_kts": 480, "track_deg": 105.0},
            {"icao": "a45678", "flight": "DAL1240", "type": "A321", "lat": obs_lat - 0.18, "lon": obs_lon + 0.10, "alt_ft": 24000, "speed_kts": 390, "track_deg": 285.0},
            {"icao": "a789bc", "flight": "AAL415", "type": "B738", "lat": obs_lat + 0.08, "lon": obs_lon + 0.20, "alt_ft": 14000, "speed_kts": 280, "track_deg": 210.0},
            {"icao": "a9def0", "flight": "SWA198", "type": "B737", "lat": obs_lat - 0.05, "lon": obs_lon - 0.12, "alt_ft": 18500, "speed_kts": 310, "track_deg": 40.0},
        ]
        self.last_update = time.time()

    def update_positions(self) -> List[Dict]:
        now = time.time()
        dt = max(0.1, min(10.0, now - self.last_update))
        self.last_update = now

        for f in self.flights:
            # Advance position according to speed and track
            speed_mps = f["speed_kts"] * 0.514444
            dist_m = speed_mps * dt
            track_rad = math.radians(f["track_deg"])

            # 1 deg lat ~ 111132 m, 1 deg lon ~ 111132 * cos(lat)
            dlat = (dist_m * math.cos(track_rad)) / 111132.0
            dlon = (dist_m * math.sin(track_rad)) / (111132.0 * math.cos(math.radians(f["lat"])))

            f["lat"] += dlat
            f["lon"] += dlon

            # Wrap around if too far
            if math.hypot(f["lat"] - self.obs_lat, f["lon"] - self.obs_lon) > 2.5:
                # Reset closer
                f["lat"] = self.obs_lat - dlat * 10
                f["lon"] = self.obs_lon - dlon * 10

        return self.flights


class ADSBTracker:
    """
    Ingests ADS-B transponder data from local dump1090/readsb receiver or simulation,
    computes topocentric Alt/Az coordinates, and correlates with optical sky targets.
    """

    def __init__(self, adsb_cfg: ADSBConfig, obs_cfg: ObserverConfig):
        self.config = adsb_cfg
        self.observer = obs_cfg
        self.sim_gen = SyntheticADSBGenerator(obs_cfg.latitude, obs_cfg.longitude)
        self.cached_aircraft: List[Dict] = []
        self.last_poll_time = 0.0

    def update_observer(self, obs_cfg: ObserverConfig) -> None:
        self.observer = obs_cfg
        self.sim_gen.obs_lat = obs_cfg.latitude
        self.sim_gen.obs_lon = obs_cfg.longitude

    def test_connection(self, test_url: Optional[str] = None) -> Dict:
        """Tests live reachability of readsb/dump1090 aircraft.json endpoint."""
        target_url = test_url or self.config.url
        if not target_url:
            return {"reachable": False, "error": "Endpoint URL is empty."}

        # Normalize URL: if user passed http://ip:port without path, append standard readsb path
        url_to_test = target_url.strip()
        if not url_to_test.endswith(".json") and not url_to_test.endswith("/"):
            url_to_test += "/data/aircraft.json"
        elif url_to_test.endswith("/"):
            url_to_test += "data/aircraft.json"

        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(url_to_test)
                if resp.status_code == 200:
                    data = resp.json()
                    aircraft = data.get("aircraft", [])
                    return {
                        "reachable": True,
                        "url_tested": url_to_test,
                        "aircraft_count": len(aircraft),
                        "now": data.get("now"),
                        "total_tracked": data.get("total", len(aircraft)),
                        "version": data.get("version", "readsb/dump1090 compatible")
                    }
                else:
                    error_msg = f"Server responded with HTTP {resp.status_code}"
                    if resp.status_code == 404:
                        # Probe if the receiver is running in protobuf-only mode (readsb default)
                        pb_url = url_to_test.replace("/data/aircraft.json", "/data/aircraft.pb")
                        try:
                            pb_resp = client.get(pb_url)
                            if pb_resp.status_code == 200:
                                error_msg = (
                                    "readsb receiver found, but it is running in Protobuf-only mode (aircraft.pb active, aircraft.json missing). "
                                    "To enable JSON output in Docker Compose, add to your environment: "
                                    "- READSB_EXTRA_ARGS=--write-json /run/readsb "
                                    "(or mount a tmpfs on /run/readsb)."
                                )
                        except Exception:
                            pass
                    return {
                        "reachable": False,
                        "url_tested": url_to_test,
                        "status_code": resp.status_code,
                        "error": error_msg
                    }
        except Exception as e:
            return {
                "reachable": False,
                "url_tested": url_to_test,
                "error": str(e)
            }

    def poll_aircraft(self) -> List[Dict]:
        """Fetches aircraft and converts them to Topocentric Alt/Az coordinates."""
        raw_aircraft = []

        if not self.config.use_simulation and self.config.url:
            url = self.config.url.strip()
            if not url.endswith(".json") and not url.endswith("/"):
                url += "/data/aircraft.json"
            elif url.endswith("/"):
                url += "data/aircraft.json"

            try:
                with httpx.Client(timeout=2.0) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw_aircraft = data.get("aircraft", [])
            except Exception:
                # Fall back to simulation if local feed unavailable
                pass

        if not raw_aircraft:
            raw_aircraft = self.sim_gen.update_positions()

        visible_aircraft = []
        obs_lat = self.observer.latitude
        obs_lon = self.observer.longitude
        obs_alt = self.observer.elevation_m

        for ac in raw_aircraft:
            lat = ac.get("lat")
            lon = ac.get("lon")
            alt_ft = ac.get("alt_geom") or ac.get("alt_baro") or ac.get("alt_ft", 0)
            if lat is None or lon is None:
                continue

            alt_m = alt_ft * 0.3048
            alt_deg, az_deg, range_km = aircraft_to_altaz(lat, lon, alt_m, obs_lat, obs_lon, obs_alt)

            if alt_deg >= 0.0 and range_km <= self.config.max_range_km:
                flight = ac.get("flight", "").strip() or ac.get("icao", "Unknown")
                visible_aircraft.append({
                    "id": ac.get("icao") or ac.get("hex") or flight,
                    "flight": flight,
                    "type": ac.get("type", "Aircraft"),
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "altitude_ft": int(alt_ft),
                    "altitude_deg": round(alt_deg, 2),
                    "azimuth_deg": round(az_deg, 2),
                    "range_km": round(range_km, 1),
                    "speed_kts": int(ac.get("speed") or ac.get("speed_kts", 0)),
                    "track_deg": round(float(ac.get("track") or ac.get("track_deg", 0.0)), 1),
                    "squawk": ac.get("squawk", "1200"),
                    "source": "adsb"
                })

        self.cached_aircraft = visible_aircraft
        return visible_aircraft

    def correlate_target(self, target_alt: float, target_az: float, tolerance_deg: float = 3.0) -> Optional[Dict]:
        """
        Correlates an optical target at (target_alt, target_az) with known ADS-B aircraft.
        Returns closest matching flight if within angular tolerance.
        """
        best_match = None
        min_err = 999.0

        for ac in self.cached_aircraft:
            d_alt = ac["altitude_deg"] - target_alt
            d_az = (ac["azimuth_deg"] - target_az + 180.0) % 360.0 - 180.0
            ang_err = math.hypot(d_alt, d_az * math.cos(math.radians((ac["altitude_deg"] + target_alt) / 2.0)))

            if ang_err <= tolerance_deg and ang_err < min_err:
                min_err = ang_err
                best_match = {**ac, "angular_error_deg": round(ang_err, 2)}

        return best_match
