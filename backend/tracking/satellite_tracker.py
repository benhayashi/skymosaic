import datetime
import math
import time
from typing import Dict, List, Optional, Tuple
import numpy as np

from backend.astrometry.celestial import altaz_to_unit_vector, datetime_to_jd, get_lst_deg
from backend.config import ObserverConfig, SatelliteConfig


class SatelliteTracker:
    """
    Computes topocentric coordinates for artificial satellites (ISS, Tiangong, Starlink)
    and correlates optical tracks with orbital passes.
    """

    def __init__(self, sat_cfg: SatelliteConfig, obs_cfg: ObserverConfig):
        self.config = sat_cfg
        self.observer = obs_cfg
        # Prominent satellite orbit templates (semi-major axis km, inclination deg, RAAN deg, mean motion revs/day)
        self.satellites = [
            {"id": "25544", "name": "ISS (ZARYA)", "mag": -1.8, "alt_km": 420.0, "incl_deg": 51.64, "period_min": 92.9, "phase_offset": 0.0},
            {"id": "48274", "name": "CSS (TIANGONG)", "mag": 0.5, "alt_km": 390.0, "incl_deg": 41.47, "period_min": 92.2, "phase_offset": 45.0},
            {"id": "20580", "name": "HST (HUBBLE)", "mag": 1.5, "alt_km": 535.0, "incl_deg": 28.47, "period_min": 95.3, "phase_offset": 110.0},
            {"id": "starlink_lead", "name": "Starlink Train", "mag": 2.2, "alt_km": 550.0, "incl_deg": 53.05, "period_min": 95.6, "phase_offset": 190.0},
        ]
        self.cached_visible_sats: List[Dict] = []

    def update_observer(self, obs_cfg: ObserverConfig) -> None:
        self.observer = obs_cfg

    def compute_satellite_passes(self, dt: Optional[datetime.datetime] = None) -> List[Dict]:
        """
        Computes current Topocentric Alt/Az for satellites above the horizon.
        """
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)

        jd = datetime_to_jd(dt)
        # Epoch time in minutes
        t_min = (jd - 2451545.0) * 1440.0
        lst_deg = get_lst_deg(dt, self.observer.longitude)
        obs_lat = self.observer.latitude

        visible = []
        earth_r = 6378.137

        for sat in self.satellites:
            orbit_period = sat["period_min"]
            phase_deg = ((t_min / orbit_period) * 360.0 + sat["phase_offset"]) % 360.0
            phase_rad = math.radians(phase_deg)
            incl_rad = math.radians(sat["incl_deg"])

            # Orbital position in geocentric equatorial frame
            r_sat = earth_r + sat["alt_km"]
            # Latitude: sin(lat_sat) = sin(incl) * sin(phase)
            sin_lat = math.sin(incl_rad) * math.sin(phase_rad)
            lat_sat_rad = math.asin(max(-1.0, min(1.0, sin_lat)))
            lat_sat_deg = math.degrees(lat_sat_rad)

            # Longitude: phase + node drift
            ra_sat_deg = (math.degrees(math.atan2(math.cos(incl_rad) * math.sin(phase_rad), math.cos(phase_rad))) - (t_min * 0.25)) % 360.0

            # Sub-satellite point relative to observer
            d_lon = math.radians((ra_sat_deg - self.observer.longitude + 180.0) % 360.0 - 180.0)
            lat_obs_rad = math.radians(obs_lat)

            # Angular distance from observer center
            cos_c = math.sin(lat_obs_rad) * math.sin(lat_sat_rad) + math.cos(lat_obs_rad) * math.cos(lat_sat_rad) * math.cos(d_lon)
            cos_c = max(-1.0, min(1.0, cos_c))
            c = math.acos(cos_c)

            # Slant range to satellite
            d_slant = math.sqrt(earth_r**2 + r_sat**2 - 2 * earth_r * r_sat * cos_c)

            # Elevation/Altitude
            sin_alt = (r_sat * cos_c - earth_r) / d_slant
            alt_rad = math.asin(max(-1.0, min(1.0, sin_alt)))
            alt_deg = math.degrees(alt_rad)

            if alt_deg >= -1.0:
                # Azimuth
                y = math.sin(d_lon) * math.cos(lat_sat_rad)
                x = math.cos(lat_obs_rad) * math.sin(lat_sat_rad) - math.sin(lat_obs_rad) * math.cos(lat_sat_rad) * math.cos(d_lon)
                az_rad = math.atan2(y, x)
                az_deg = math.degrees(az_rad) % 360.0

                # Angular velocity across sky (~0.4 - 1.2 deg/sec for LEO)
                ang_speed = round(7.5 / (d_slant / 400.0), 2)  # deg/sec

                v = altaz_to_unit_vector(alt_deg, az_deg)

                visible.append({
                    "id": sat["id"],
                    "name": sat["name"],
                    "mag": sat["mag"],
                    "altitude_deg": round(alt_deg, 2),
                    "azimuth_deg": round(az_deg, 2),
                    "range_km": round(d_slant, 1),
                    "angular_speed_deg_s": ang_speed,
                    "unit_vector": v.tolist(),
                    "source": "satellite"
                })

        # If simulation enabled and no orbit is currently passing, synthesize active pass
        if self.config.use_simulation and len(visible) == 0:
            sim_v = altaz_to_unit_vector(48.0, 142.0)
            visible.append({
                "id": "25544",
                "name": "ISS (ZARYA)",
                "mag": -1.8,
                "altitude_deg": 48.0,
                "azimuth_deg": 142.0,
                "range_km": 420.0,
                "angular_speed_deg_s": 0.85,
                "unit_vector": sim_v.tolist(),
                "source": "satellite"
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
