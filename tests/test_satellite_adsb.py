import datetime

from backend.config import ADSBConfig, ObserverConfig, SatelliteConfig
from backend.tracking.adsb_tracker import ADSBTracker
from backend.tracking.satellite_tracker import SatelliteTracker, parse_tle_text

ISS_TLE = """ISS (ZARYA)
1 25544U 98067A   08264.51782528 -.00002182  00000-0 -11606-4 0  2927
2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.72125391563537
"""


def test_parse_tle_text():
    sats = parse_tle_text(ISS_TLE)
    assert len(sats) == 1 and sats[0]["norad"] == 25544


def test_sgp4_propagation_is_physical(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tracking.satellite_tracker.TLE_DIR", str(tmp_path))
    t = SatelliteTracker(SatelliteConfig(), ObserverConfig())
    sat = parse_tle_text(ISS_TLE)[0]
    from sgp4.api import jday
    import numpy as np
    from backend.tracking.adsb_tracker import geodetic_to_ecef
    jd, fr = jday(2008, 9, 20, 12, 25, 40)
    res = t._topocentric(sat["satrec"], jd, fr, geodetic_to_ecef(40.7, -74.0, 0))
    alt, az, rng, r = res
    assert -90 <= alt <= 90 and 0 <= az < 360
    # Geocentric radius of the ISS is ~6700-6800 km
    assert 6600 < float(np.linalg.norm(r)) < 6900


def test_no_fake_satellite_when_simulation_off(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tracking.satellite_tracker.TLE_DIR", str(tmp_path))
    monkeypatch.setattr(SatelliteTracker, "_maybe_refresh", lambda self: None)
    t = SatelliteTracker(SatelliteConfig(use_simulation=False), ObserverConfig())
    assert t.compute_satellite_passes(datetime.datetime.now(datetime.timezone.utc)) == []
    t2 = SatelliteTracker(SatelliteConfig(use_simulation=True), ObserverConfig())
    assert "SIMULATED" in t2.compute_satellite_passes()[0]["name"]


def test_defaults_are_not_simulated():
    assert ADSBConfig().use_simulation is False
    assert SatelliteConfig().use_simulation is False


def test_adsb_no_fake_traffic_on_feed_failure(monkeypatch):
    t = ADSBTracker(ADSBConfig(url="http://127.0.0.1:9"), ObserverConfig())
    monkeypatch.setattr(t, "_fetch_local", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert t.poll_aircraft() == []
    assert t.get_status()["state"] in ("error", "pending")


def test_adsb_json_fields(monkeypatch):
    obs = ObserverConfig()
    t = ADSBTracker(ADSBConfig(max_range_km=300), obs)
    ac = {"hex": "abc123", "flight": "TEST1 ", "lat": obs.latitude + 0.1, "lon": obs.longitude,
          "alt_baro": 30000, "gs": 420, "track": 90, "seen_pos": 1.0, "t": "A320"}
    stale = dict(ac, hex="old000", seen_pos=120.0)
    ground = dict(ac, hex="gnd000", alt_baro="ground")
    monkeypatch.setattr(t, "_fetch_local", lambda: [ac, stale, ground])
    out = t.poll_aircraft()
    ids = {a["id"] for a in out}
    assert "abc123" in ids and "old000" not in ids
    a = [x for x in out if x["id"] == "abc123"][0]
    assert a["speed_kts"] == 420 and a["type"] == "A320"


def test_candidate_urls_prefers_8081_for_8080():
    t = ADSBTracker(ADSBConfig(), ObserverConfig())
    urls = t._get_candidate_urls("http://192.168.1.216:8080")
    assert urls[0].startswith("http://192.168.1.216:8081")
    assert t._get_candidate_urls("http://x/data/aircraft.json") == ["http://x/data/aircraft.json"]
