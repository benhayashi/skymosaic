import datetime
import pytest

from backend.astrometry.celestial import (
    get_all_celestial_objects,
    get_sun_coordinates,
    get_visible_planets,
)


def test_sun_coordinates_and_day_night_detection():
    # Noon in NYC (UTC ~16:00 on June 21) -> Sun should be well above horizon (day)
    dt_noon = datetime.datetime(2026, 6, 21, 16, 0, 0, tzinfo=datetime.timezone.utc)
    sun_noon = get_sun_coordinates(dt_noon, lat_deg=40.71, lon_deg=-74.00)
    assert sun_noon["altitude_deg"] > 30.0
    assert sun_noon["sky_state"] == "day"

    # Midnight in NYC (UTC ~04:00) -> Sun should be below horizon (astronomical night)
    dt_midnight = datetime.datetime(2026, 6, 21, 4, 0, 0, tzinfo=datetime.timezone.utc)
    sun_midnight = get_sun_coordinates(dt_midnight, lat_deg=40.71, lon_deg=-74.00)
    assert sun_midnight["altitude_deg"] < -18.0
    assert sun_midnight["sky_state"] == "astronomical_night"


def test_get_visible_planets():
    dt = datetime.datetime(2026, 10, 14, 2, 0, 0, tzinfo=datetime.timezone.utc)
    planets = get_visible_planets(dt, lat_deg=40.71, lon_deg=-74.00, min_alt_deg=-90.0)
    assert len(planets) >= 4  # Venus, Mars, Jupiter, Saturn, Moon
    names = [p["name"] for p in planets]
    assert "Jupiter" in names
    assert "Venus" in names
    for p in planets:
        assert -90.0 <= p["altitude_deg"] <= 90.0
        assert 0.0 <= p["azimuth_deg"] <= 360.0


def test_all_celestial_objects_combined():
    dt = datetime.datetime(2026, 10, 14, 2, 0, 0, tzinfo=datetime.timezone.utc)
    all_objs = get_all_celestial_objects(dt, lat_deg=40.71, lon_deg=-74.00, min_alt_deg=-10.0)
    assert len(all_objs) > 10
    types = set(o.get("type") for o in all_objs)
    assert "star" in types
    assert "planet" in types
