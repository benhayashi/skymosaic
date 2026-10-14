import datetime
import math
import numpy as np
import pytest

from backend.config import ADSBConfig, ObserverConfig, SatelliteConfig, SkyViewConfig, get_default_config
from backend.tracking.adsb_tracker import ADSBTracker, aircraft_to_altaz, geodetic_to_ecef
from backend.tracking.optical_tracker import OpticalTrackerEngine
from backend.tracking.satellite_tracker import SatelliteTracker


def test_aircraft_to_altaz_zenith():
    # Aircraft directly above observer at 10,000m
    obs_lat, obs_lon, obs_alt = 40.0, -75.0, 100.0
    ac_lat, ac_lon, ac_alt = 40.0, -75.0, 10100.0

    alt, az, dist_km = aircraft_to_altaz(ac_lat, ac_lon, ac_alt, obs_lat, obs_lon, obs_alt)
    assert pytest.approx(alt, abs=1.0) == 90.0  # Directly overhead
    assert pytest.approx(dist_km, abs=0.1) == 10.0


def test_adsb_tracker_correlation():
    obs = ObserverConfig(latitude=40.71, longitude=-74.00, elevation_m=10.0)
    adsb_cfg = ADSBConfig(enabled=True, use_simulation=True)
    tracker = ADSBTracker(adsb_cfg, obs)

    aircraft = tracker.poll_aircraft()
    assert len(aircraft) > 0

    first_plane = aircraft[0]
    # Correlate with target at same Alt/Az
    match = tracker.correlate_target(first_plane["altitude_deg"], first_plane["azimuth_deg"], tolerance_deg=2.0)
    assert match is not None
    assert match["flight"] == first_plane["flight"]


def test_satellite_tracker_orbit_passes():
    obs = ObserverConfig(latitude=40.71, longitude=-74.00, elevation_m=10.0)
    sat_cfg = SatelliteConfig(enabled=True, use_simulation=True)
    sat_tracker = SatelliteTracker(sat_cfg, obs)

    sats = sat_tracker.compute_satellite_passes()
    assert len(sats) > 0
    for s in sats:
        assert s["altitude_deg"] >= -1.0
        assert 0.0 <= s["azimuth_deg"] <= 360.0
        assert s["angular_speed_deg_s"] > 0.1


def test_optical_tracker_engine_classification():
    cfg = get_default_config()
    engine = OpticalTrackerEngine(cfg)

    # Create dummy frame
    frame = np.zeros((400, 400, 3), dtype=np.uint8)
    res = engine.process_stitched_frame(frame)

    assert "targets" in res
    assert "weather" in res
    assert "mode" in res
    assert res["mode"] == "feed"

    # Should have identified celestial targets (stars/planets) and ADS-B aircraft
    categories = set(t["category"] for t in res["targets"])
    assert "celestial" in categories or "airplane" in categories
