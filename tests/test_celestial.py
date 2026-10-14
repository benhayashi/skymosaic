import datetime
import math
import numpy as np
import pytest

from backend.astrometry.celestial import (
    BRIGHT_STARS,
    altaz_to_unit_vector,
    datetime_to_jd,
    get_lst_deg,
    get_visible_stars,
    radec_to_altaz,
    unit_vector_to_altaz,
)


def test_julian_date():
    # J2000 epoch: 2000-01-01 12:00:00 UTC = JD 2451545.0
    dt_j2000 = datetime.datetime(2000, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    jd = datetime_to_jd(dt_j2000)
    assert pytest.approx(jd, abs=1e-4) == 2451545.0


def test_polaris_altitude_matches_observer_latitude():
    # Polaris has declination ~89.26 deg.
    # Its altitude should closely approximate observer latitude regardless of hour angle.
    lat = 40.7128  # New York City
    polaris = next(s for s in BRIGHT_STARS if s["id"] == "polaris")
    
    # Check across 4 different sidereal times (0, 90, 180, 270 deg)
    for lst in [0.0, 90.0, 180.0, 270.0]:
        alt, az = radec_to_altaz(polaris["ra"], polaris["dec"], lat, lst)
        assert abs(alt - lat) < 1.0, f"Polaris altitude {alt} deviated from latitude {lat}"


def test_unit_vector_roundtrip():
    for alt in [0.0, 30.0, 60.0, 85.0]:
        for az in [0.0, 45.0, 90.0, 180.0, 270.0]:
            v = altaz_to_unit_vector(alt, az)
            norm = np.linalg.norm(v)
            assert pytest.approx(norm, abs=1e-6) == 1.0

            calc_alt, calc_az = unit_vector_to_altaz(v)
            assert pytest.approx(calc_alt, abs=1e-3) == alt
            assert pytest.approx(calc_az % 360.0, abs=1e-3) == az % 360.0


def test_get_visible_stars():
    dt = datetime.datetime(2026, 10, 14, 2, 0, 0, tzinfo=datetime.timezone.utc)
    stars = get_visible_stars(dt, lat_deg=40.7, lon_deg=-74.0, min_alt_deg=0.0)
    assert len(stars) > 10
    for s in stars:
        assert s["altitude_deg"] >= 0.0
        assert 0.0 <= s["azimuth_deg"] <= 360.0
        assert len(s["unit_vector"]) == 3
