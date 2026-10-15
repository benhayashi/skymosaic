import datetime
import logging
import math
import os
import threading
import time
from typing import Dict, List, Optional

import httpx
from sgp4.api import Satrec, jday

from backend.astrometry.celestial import altaz_to_unit_vector
from backend.config import ObserverConfig, SatelliteConfig
from backend.tracking.adsb_tracker import ecef_to_enu, geodetic_to_ecef

logger = logging.getLogger(__name__)

TLE_DIR = os.path.join("data", "tle")
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
EARTH_R_KM = 6378.137

# Approximate standard (1000 km range, full phase) visual magnitudes for well-known objects.
STD_MAGNITUDES = {25544: -1.3, 48274: 0.0, 20580: 2.5, 49044: 0.0, 54216: 0.0}
DEFAULT_STD_MAG = 3.5


def _gmst_rad(jd: float) -> float:
    return math.radians((280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0)


def _sun_eci_unit(jd: float):
    """Low-precision (~0.01 deg) Sun direction in an equatorial inertial frame."""
    n = jd - 2451545.0
    L = math.radians((280.460 + 0.9856474 * n) % 360.0)
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return (math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam))


def parse_tle_text(text: str) -> List[Dict]:
    """Parses 3-line TLE text into [{name, satrec, norad}]."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = []
    i = 0
    while i + 2 < len(lines) + 0:
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            try:
                sat = Satrec.twoline2rv(lines[i + 1], lines[i + 2])
                out.append({"name": lines[i].strip(), "satrec": sat, "norad": int(sat.satnum)})
            except Exception:
                pass
            i += 3
        else:
            i += 1
    return out


class SatelliteTracker:
    """
    Real SGP4 satellite tracker. Fetches TLEs from CelesTrak (cached on disk, refreshed in
    the background), propagates them and computes topocentric Alt/Az for the observer.
    Simulation is only used when explicitly enabled.
    """

    def __init__(self, sat_cfg: SatelliteConfig, obs_cfg: ObserverConfig):
        self.config = sat_cfg
        self.observer = obs_cfg
        self.satellites: List[Dict] = []
        self.cached_visible_sats: List[Dict] = []
        self.tle_updated_at: float = 0.0
        self.tle_error: Optional[str] = None
        self._lock = threading.Lock()
        self._loading = False
        self._last_refresh_attempt = 0.0
        self._load_cached_tles()

    def update_observer(self, obs_cfg: ObserverConfig) -> None:
        self.observer = obs_cfg

    def update_config(self, sat_cfg: SatelliteConfig) -> None:
        if list(sat_cfg.tle_groups) != list(self.config.tle_groups):
            self._last_refresh_attempt = 0.0
        self.config = sat_cfg
        self._load_cached_tles()

    # ---------------- TLE management ----------------
    def _tle_path(self, group: str) -> str:
        return os.path.join(TLE_DIR, f"{group}.tle")

    def _load_cached_tles(self) -> None:
        sats: Dict[int, Dict] = {}
        newest = 0.0
        for group in self.config.tle_groups:
            path = self._tle_path(group)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for s in parse_tle_text(f.read()):
                            sats[s["norad"]] = s
                    newest = max(newest, os.path.getmtime(path))
                except Exception as e:
                    logger.warning("Failed reading TLE cache %s: %s", path, e)
        with self._lock:
            self.satellites = list(sats.values())
            if newest:
                self.tle_updated_at = newest

    def _refresh_tles(self) -> None:
        try:
            os.makedirs(TLE_DIR, exist_ok=True)
            errors = []
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                for group in self.config.tle_groups:
                    try:
                        r = client.get(CELESTRAK_URL.format(group=group))
                        r.raise_for_status()
                        if parse_tle_text(r.text):
                            with open(self._tle_path(group), "w", encoding="utf-8") as f:
                                f.write(r.text)
                        else:
                            errors.append(f"{group}: empty/invalid response")
                    except Exception as e:
                        errors.append(f"{group}: {e}")
            self.tle_error = "; ".join(errors) if errors else None
            self._load_cached_tles()
        finally:
            self._loading = False

    def _maybe_refresh(self) -> None:
        now = time.time()
        stale = (now - self.tle_updated_at) > self.config.tle_refresh_hours * 3600.0
        if self._loading or not stale or now - self._last_refresh_attempt < 600.0:
            return
        self._loading = True
        self._last_refresh_attempt = now
        threading.Thread(target=self._refresh_tles, name="TLERefresh", daemon=True).start()

    def get_status(self) -> Dict:
        return {
            "simulation": self.config.use_simulation,
            "tle_count": len(self.satellites),
            "tle_updated_at": self.tle_updated_at or None,
            "tle_age_hours": round((time.time() - self.tle_updated_at) / 3600.0, 1) if self.tle_updated_at else None,
            "tle_error": self.tle_error,
            "refreshing": self._loading,
        }

    # ---------------- Propagation ----------------
    def _topocentric(self, satrec, jd: float, fr: float, obs_ecef):
        err, r, _v = satrec.sgp4(jd, fr)
        if err != 0:
            return None
        theta = _gmst_rad(jd + fr)
        c, s = math.cos(theta), math.sin(theta)
        # TEME -> ECEF (ignoring polar motion)
        ecef = (c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2])
        import numpy as np
        enu = ecef_to_enu(np.array(ecef) * 1000.0, self.observer.latitude, self.observer.longitude, obs_ecef)
        e, n, u = float(enu[0]), float(enu[1]), float(enu[2])
        horiz = math.hypot(e, n)
        rng_km = math.sqrt(e * e + n * n + u * u) / 1000.0
        return math.degrees(math.atan2(u, horiz)), math.degrees(math.atan2(e, n)) % 360.0, rng_km, r

    def compute_satellite_passes(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """Computes current Topocentric Alt/Az for sunlit satellites above the horizon."""
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        dt = dt.astimezone(datetime.timezone.utc)

        if self.config.use_simulation:
            sim_v = altaz_to_unit_vector(48.0, 142.0)
            self.cached_visible_sats = [{
                "id": "25544", "name": "ISS (ZARYA) [SIMULATED]", "mag": -1.8,
                "altitude_deg": 48.0, "azimuth_deg": 142.0, "range_km": 420.0,
                "angular_speed_deg_s": 0.85, "unit_vector": sim_v.tolist(), "source": "satellite",
            }]
            return self.cached_visible_sats

        self._maybe_refresh()
        jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
        obs_ecef = geodetic_to_ecef(self.observer.latitude, self.observer.longitude, self.observer.elevation_m)
        sun = _sun_eci_unit(jd + fr)

        with self._lock:
            sats = list(self.satellites)

        visible = []
        for sat in sats:
            res = self._topocentric(sat["satrec"], jd, fr, obs_ecef)
            if res is None:
                continue
            alt, az, rng, r_teme = res
            if alt < 0.0:
                continue
            # Earth-shadow (cylindrical) test
            proj = sum(r_teme[i] * sun[i] for i in range(3))
            perp = math.sqrt(max(0.0, sum(x * x for x in r_teme) - proj * proj))
            sunlit = proj > 0 or perp > EARTH_R_KM
            if not sunlit:
                continue
            mag = STD_MAGNITUDES.get(sat["norad"], DEFAULT_STD_MAG) + 5.0 * math.log10(max(rng, 1.0) / 1000.0)
            if mag > self.config.max_magnitude:
                continue
            res2 = self._topocentric(sat["satrec"], jd, fr + 1.0 / 86400.0, obs_ecef)
            speed = 0.0
            if res2:
                d_alt = res2[0] - alt
                d_az = ((res2[1] - az + 180.0) % 360.0 - 180.0) * math.cos(math.radians(alt))
                speed = math.hypot(d_alt, d_az)
            visible.append({
                "id": str(sat["norad"]),
                "name": sat["name"],
                "mag": round(mag, 1),
                "altitude_deg": round(alt, 2),
                "azimuth_deg": round(az, 2),
                "range_km": round(rng, 1),
                "angular_speed_deg_s": round(speed, 3),
                "unit_vector": altaz_to_unit_vector(alt, az).tolist(),
                "source": "satellite",
            })

        self.cached_visible_sats = visible
        return visible

    def correlate_target(self, target_alt: float, target_az: float, tolerance_deg: float = 3.0) -> Optional[Dict]:
        """
        Correlates an optical target with visible orbiting satellites.
        """
        best_match = None
        min_err = 999.0

        for sat in self.cached_visible_sats:
            d_alt = sat["altitude_deg"] - target_alt
            d_az = (sat["azimuth_deg"] - target_az + 180.0) % 360.0 - 180.0
            ang_err = math.hypot(d_alt, d_az * math.cos(math.radians((sat["altitude_deg"] + target_alt) / 2.0)))

            if ang_err <= tolerance_deg and ang_err < min_err:
                min_err = ang_err
                best_match = {**sat, "angular_error_deg": round(ang_err, 2)}

        return best_match
