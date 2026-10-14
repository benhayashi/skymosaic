import datetime
import math
from typing import Dict, List, Optional, Tuple
import numpy as np


def datetime_to_jd(dt: datetime.datetime) -> float:
    """Converts a UTC datetime object to Julian Date."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc)
    
    year = dt.year
    month = dt.month
    day = dt.day + (dt.hour + (dt.minute + (dt.second + dt.microsecond / 1e6) / 60.0) / 60.0) / 24.0

    if month <= 2:
        year -= 1
        month += 12

    A = math.floor(year / 100.0)
    B = 2 - A + math.floor(A / 4.0)

    jd = math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + B - 1524.5
    return jd


def jd_to_gmst(jd: float) -> float:
    """Computes Greenwich Mean Sidereal Time (GMST) in degrees for a given Julian Date."""
    T = (jd - 2451545.0) / 36525.0
    # IAU formula for GMST
    theta0 = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * T * T - (T * T * T) / 38710000.0
    return theta0 % 360.0


def get_lst_deg(dt: datetime.datetime, longitude_deg: float) -> float:
    """Computes Local Sidereal Time (LST) in degrees for UTC datetime and observer longitude (+ East, - West)."""
    jd = datetime_to_jd(dt)
    gmst = jd_to_gmst(jd)
    lst = (gmst + longitude_deg) % 360.0
    return lst


def radec_to_altaz(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lst_deg: float
) -> Tuple[float, float]:
    """
    Transforms Equatorial coordinates (RA, Dec) to Local Horizontal coordinates (Altitude, Azimuth).
    Azimuth is defined as 0° North, 90° East, 180° South, 270° West.
    Altitude is -90° to +90° (0° at horizon, +90° at Zenith).
    """
    ha_rad = math.radians((lst_deg - ra_deg) % 360.0)
    lat_rad = math.radians(lat_deg)
    dec_rad = math.radians(dec_deg)

    # sin(alt) = sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(ha)
    sin_alt = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)

    # cos(A) and sin(A)
    cos_alt = math.cos(alt_rad)
    if cos_alt < 1e-7:
        # Near Zenith or Nadir
        az_deg = 0.0
    else:
        sin_az = -math.cos(dec_rad) * math.sin(ha_rad) / cos_alt
        cos_az = (math.sin(dec_rad) - math.sin(lat_rad) * sin_alt) / (math.cos(lat_rad) * cos_alt)
        az_rad = math.atan2(sin_az, cos_az)
        az_deg = math.degrees(az_rad) % 360.0

    return math.degrees(alt_rad), az_deg


def altaz_to_unit_vector(alt_deg: float, az_deg: float) -> np.ndarray:
    """
    Returns unit 3D vector in Topocentric Horizon Frame:
    X: East (Az = 90)
    Y: North (Az = 0)
    Z: Up / Zenith (Alt = 90)
    """
    alt_rad = math.radians(alt_deg)
    az_rad = math.radians(az_deg)
    cos_alt = math.cos(alt_rad)
    x = cos_alt * math.sin(az_rad)  # East
    y = cos_alt * math.cos(az_rad)  # North
    z = math.sin(alt_rad)           # Up
    return np.array([x, y, z], dtype=np.float64)


def unit_vector_to_altaz(v: np.ndarray) -> Tuple[float, float]:
    """Converts Topocentric unit vector (X=East, Y=North, Z=Up) to Alt/Az degrees."""
    v = v / np.linalg.norm(v)
    alt_rad = math.asin(max(-1.0, min(1.0, float(v[2]))))
    az_rad = math.atan2(float(v[0]), float(v[1]))
    return math.degrees(alt_rad), math.degrees(az_rad) % 360.0


# Curated catalog of primary bright stars (RA in degrees, Dec in degrees, V_mag)
BRIGHT_STARS = [
    {"id": "polaris", "name": "Polaris", "constellation": "UMi", "ra": 37.95, "dec": 89.26, "mag": 1.98},
    {"id": "sirius", "name": "Sirius", "constellation": "CMa", "ra": 101.29, "dec": -16.72, "mag": -1.46},
    {"id": "canopus", "name": "Canopus", "constellation": "Car", "ra": 95.99, "dec": -52.70, "mag": -0.74},
    {"id": "arcturus", "name": "Arcturus", "constellation": "Boo", "ra": 213.92, "dec": 19.18, "mag": -0.05},
    {"id": "vega", "name": "Vega", "constellation": "Lyr", "ra": 279.23, "dec": 38.78, "mag": 0.03},
    {"id": "capella", "name": "Capella", "constellation": "Aur", "ra": 79.17, "dec": 46.00, "mag": 0.08},
    {"id": "rigel", "name": "Rigel", "constellation": "Ori", "ra": 78.63, "dec": -8.20, "mag": 0.13},
    {"id": "procyon", "name": "Procyon", "constellation": "CMi", "ra": 114.83, "dec": 5.22, "mag": 0.38},
    {"id": "betelgeuse", "name": "Betelgeuse", "constellation": "Ori", "ra": 88.79, "dec": 7.41, "mag": 0.42},
    {"id": "achernar", "name": "Achernar", "constellation": "Eri", "ra": 24.43, "dec": -57.24, "mag": 0.46},
    {"id": "hadar", "name": "Hadar", "constellation": "Cen", "ra": 210.96, "dec": -60.37, "mag": 0.61},
    {"id": "altair", "name": "Altair", "constellation": "Aql", "ra": 297.70, "dec": 8.87, "mag": 0.77},
    {"id": "acrux", "name": "Acrux", "constellation": "Cru", "ra": 186.65, "dec": -63.10, "mag": 0.76},
    {"id": "aldenaran", "name": "Aldebaran", "constellation": "Tau", "ra": 68.98, "dec": 16.51, "mag": 0.86},
    {"id": "antares", "name": "Antares", "constellation": "Sco", "ra": 247.35, "dec": -26.43, "mag": 0.96},
    {"id": "spica", "name": "Spica", "constellation": "Vir", "ra": 201.30, "dec": -11.16, "mag": 0.97},
    {"id": "pollux", "name": "Pollux", "constellation": "Gem", "ra": 116.33, "dec": 28.03, "mag": 1.14},
    {"id": "fomalhaut", "name": "Fomalhaut", "constellation": "PsA", "ra": 344.41, "dec": -29.62, "mag": 1.16},
    {"id": "deneb", "name": "Deneb", "constellation": "Cyg", "ra": 310.36, "dec": 45.28, "mag": 1.25},
    {"id": "mimosa", "name": "Mimosa", "constellation": "Cru", "ra": 191.93, "dec": -59.69, "mag": 1.25},
    {"id": "regulus", "name": "Regulus", "constellation": "Leo", "ra": 152.09, "dec": 11.97, "mag": 1.36},
    {"id": "adhara", "name": "Adhara", "constellation": "CMa", "ra": 104.66, "dec": -28.97, "mag": 1.50},
    {"id": "castor", "name": "Castor", "constellation": "Gem", "ra": 113.65, "dec": 31.89, "mag": 1.58},
    {"id": "gacrux", "name": "Gacrux", "constellation": "Cru", "ra": 187.79, "dec": -57.11, "mag": 1.64},
    {"id": "bellatrix", "name": "Bellatrix", "constellation": "Ori", "ra": 81.28, "dec": 6.35, "mag": 1.64},
    {"id": "elnath", "name": "Elnath", "constellation": "Tau", "ra": 81.57, "dec": 28.61, "mag": 1.65},
    {"id": "alnilam", "name": "Alnilam", "constellation": "Ori", "ra": 84.05, "dec": -1.20, "mag": 1.69},
    {"id": "alnitak", "name": "Alnitak", "constellation": "Ori", "ra": 85.19, "dec": -1.94, "mag": 1.74},
    {"id": "alioth", "name": "Alioth", "constellation": "UMa", "ra": 193.51, "dec": 55.96, "mag": 1.77},
    {"id": "dubhe", "name": "Dubhe", "constellation": "UMa", "ra": 165.93, "dec": 61.75, "mag": 1.79},
    {"id": "mirfak", "name": "Mirfak", "constellation": "Per", "ra": 51.08, "dec": 49.86, "mag": 1.80},
    {"id": "wezen", "name": "Wezen", "constellation": "CMa", "ra": 107.10, "dec": -26.39, "mag": 1.83},
    {"id": "benetnasch", "name": "Alkaid", "constellation": "UMa", "ra": 206.89, "dec": 49.31, "mag": 1.86},
    {"id": "sargas", "name": "Sargas", "constellation": "Sco", "ra": 264.33, "dec": -43.00, "mag": 1.87},
    {"id": "saiph", "name": "Saiph", "constellation": "Ori", "ra": 86.94, "dec": -9.67, "mag": 2.06},
    {"id": "merak", "name": "Merak", "constellation": "UMa", "ra": 165.46, "dec": 56.38, "mag": 2.37},
    {"id": "phecda", "name": "Phecda", "constellation": "UMa", "ra": 178.46, "dec": 53.69, "mag": 2.44},
    {"id": "megrez", "name": "Megrez", "constellation": "UMa", "ra": 183.86, "dec": 57.03, "mag": 3.31},
    {"id": "mizar", "name": "Mizar", "constellation": "UMa", "ra": 200.98, "dec": 54.92, "mag": 2.23},
    {"id": "schedar", "name": "Schedar", "constellation": "Cas", "ra": 10.13, "dec": 56.54, "mag": 2.24},
    {"id": "caph", "name": "Caph", "constellation": "Cas", "ra": 2.29, "dec": 59.15, "mag": 2.28},
    {"id": "gamma_cas", "name": "Navi", "constellation": "Cas", "ra": 14.18, "dec": 60.72, "mag": 2.47},
    {"id": "ruchbah", "name": "Ruchbah", "constellation": "Cas", "ra": 19.58, "dec": 60.23, "mag": 2.68},
    {"id": "segin", "name": "Segin", "constellation": "Cas", "ra": 26.60, "dec": 63.67, "mag": 3.35},
    {"id": "algenib", "name": "Algenib", "constellation": "Peg", "ra": 3.31, "dec": 15.18, "mag": 2.83},
    {"id": "markab", "name": "Markab", "constellation": "Peg", "ra": 346.19, "dec": 15.21, "mag": 2.49},
    {"id": "scheat", "name": "Scheat", "constellation": "Peg", "ra": 345.94, "dec": 28.08, "mag": 2.42},
    {"id": "alpheratz", "name": "Alpheratz", "constellation": "And", "ra": 2.10, "dec": 29.09, "mag": 2.06},
    {"id": "hamal", "name": "Hamal", "constellation": "Ari", "ra": 31.79, "dec": 23.46, "mag": 2.00},
    {"id": "menkar", "name": "Menkar", "constellation": "Cet", "ra": 45.57, "dec": 4.09, "mag": 2.53},
    {"id": "algol", "name": "Algol", "constellation": "Per", "ra": 47.04, "dec": 40.96, "mag": 2.12},
]

# Prominent constellation stick figure definitions (pairs of star IDs)
CONSTELLATION_LINES = [
    # Ursa Major (Big Dipper)
    ("dubhe", "merak"),
    ("merak", "phecda"),
    ("phecda", "megrez"),
    ("megrez", "dubhe"),
    ("megrez", "alioth"),
    ("alioth", "mizar"),
    ("mizar", "benetnasch"),
    # Orion
    ("betelgeuse", "bellatrix"),
    ("bellatrix", "rigel"),
    ("rigel", "saiph"),
    ("saiph", "betelgeuse"),
    ("alnitak", "alnilam"),
    # Cassiopeia (W shape)
    ("caph", "schedar"),
    ("schedar", "gamma_cas"),
    ("gamma_cas", "ruchbah"),
    ("ruchbah", "segin"),
    # Summer Triangle
    ("vega", "deneb"),
    ("deneb", "altair"),
    ("altair", "vega"),
    # Gemini
    ("castor", "pollux"),
    # Pegasus Great Square
    ("markab", "scheat"),
    ("scheat", "alpheratz"),
    ("alpheratz", "algenib"),
    ("algenib", "markab"),
]


def get_visible_stars(
    dt: datetime.datetime,
    lat_deg: float,
    lon_deg: float,
    min_alt_deg: float = 0.0
) -> List[Dict]:
    """
    Computes Alt/Az for all catalog stars for the given datetime and observer coordinates.
    Filters by minimum altitude.
    """
    lst_deg = get_lst_deg(dt, lon_deg)
    results = []
    for star in BRIGHT_STARS:
        alt, az = radec_to_altaz(star["ra"], star["dec"], lat_deg, lst_deg)
        if alt >= min_alt_deg:
            v = altaz_to_unit_vector(alt, az)
            results.append({
                **star,
                "type": "star",
                "altitude_deg": round(alt, 3),
                "azimuth_deg": round(az, 3),
                "unit_vector": v.tolist()
            })
    return results


def get_constellation_segments(visible_stars_dict: Dict[str, Dict]) -> List[Tuple[Dict, Dict]]:
    """Returns pairs of visible star dictionaries connected by constellation lines."""
    segments = []
    for id1, id2 in CONSTELLATION_LINES:
        if id1 in visible_stars_dict and id2 in visible_stars_dict:
            segments.append((visible_stars_dict[id1], visible_stars_dict[id2]))
    return segments


def get_sun_coordinates(dt: datetime.datetime, lat_deg: float, lon_deg: float) -> Dict:
    """
    Computes Sun coordinates (RA, Dec, Alt, Az) and twilight/day state.
    """
    jd = datetime_to_jd(dt)
    n = jd - 2451545.0  # Days since J2000.0

    # Mean longitude and anomaly
    L = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)

    # Ecliptic longitude
    lambda_deg = (L + 1.915 * math.sin(g) + 0.020 * math.sin(2.0 * g)) % 360.0
    eps_deg = 23.439 - 0.0000004 * n
    eps_rad = math.radians(eps_deg)
    lambda_rad = math.radians(lambda_deg)

    # RA and Dec
    sin_dec = math.sin(eps_rad) * math.sin(lambda_rad)
    dec_rad = math.asin(sin_dec)
    ra_rad = math.atan2(math.cos(eps_rad) * math.sin(lambda_rad), math.cos(lambda_rad))

    ra_deg = math.degrees(ra_rad) % 360.0
    dec_deg = math.degrees(dec_rad)

    lst_deg = get_lst_deg(dt, lon_deg)
    alt, az = radec_to_altaz(ra_deg, dec_deg, lat_deg, lst_deg)

    # Twilight classification
    if alt > 0.0:
        state = "day"
    elif alt > -6.0:
        state = "civil_twilight"
    elif alt > -12.0:
        state = "nautical_twilight"
    elif alt > -18.0:
        state = "astronomical_twilight"
    else:
        state = "astronomical_night"

    v = altaz_to_unit_vector(alt, az)

    return {
        "id": "sun",
        "name": "Sun",
        "type": "star",
        "ra": round(ra_deg, 3),
        "dec": round(dec_deg, 3),
        "altitude_deg": round(alt, 3),
        "azimuth_deg": round(az, 3),
        "mag": -26.7,
        "sky_state": state,
        "unit_vector": v.tolist()
    }


def get_visible_planets(
    dt: datetime.datetime,
    lat_deg: float,
    lon_deg: float,
    min_alt_deg: float = -2.0
) -> List[Dict]:
    """
    Computes approximate positions for visible planets (Venus, Jupiter, Saturn, Mars, Moon).
    """
    jd = datetime_to_jd(dt)
    d = jd - 2451545.0
    lst_deg = get_lst_deg(dt, lon_deg)

    planets_data = [
        # Mean orbital elements (rough Keplerian approximations)
        {"id": "moon", "name": "Moon", "mag": -12.0, "a": 1.0, "period": 27.32166, "L0": 218.316, "daily": 13.176396, "incl": 5.14},
        {"id": "venus", "name": "Venus", "mag": -4.2, "a": 0.723, "period": 224.7, "L0": 181.98, "daily": 1.60213, "incl": 3.39},
        {"id": "mars", "name": "Mars", "mag": -1.5, "a": 1.524, "period": 687.0, "L0": 355.43, "daily": 0.52403, "incl": 1.85},
        {"id": "jupiter", "name": "Jupiter", "mag": -2.6, "a": 5.204, "period": 4332.6, "L0": 34.35, "daily": 0.08308, "incl": 1.30},
        {"id": "saturn", "name": "Saturn", "mag": 0.5, "a": 9.582, "period": 10759.2, "L0": 50.08, "daily": 0.03344, "incl": 2.49},
    ]

    results = []
    # Sun ecliptic longitude for geocentric projection
    sun_L = (280.460 + 0.9856474 * d) % 360.0

    for p in planets_data:
        # Heliocentric / geocentric ecliptic longitude approximation
        L_ecl = (p["L0"] + p["daily"] * d) % 360.0
        beta = p["incl"] * math.sin(math.radians(L_ecl))  # Ecliptic latitude

        # Transform ecliptic (L, beta) to equatorial (RA, Dec)
        eps = math.radians(23.439)
        L_rad = math.radians(L_ecl)
        beta_rad = math.radians(beta)

        sin_dec = math.sin(beta_rad) * math.cos(eps) + math.cos(beta_rad) * math.sin(eps) * math.sin(L_rad)
        dec_rad = math.asin(max(-1.0, min(1.0, sin_dec)))
        ra_rad = math.atan2(
            math.sin(L_rad) * math.cos(eps) - math.tan(beta_rad) * math.sin(eps),
            math.cos(L_rad)
        )

        ra_deg = math.degrees(ra_rad) % 360.0
        dec_deg = math.degrees(dec_rad)

        alt, az = radec_to_altaz(ra_deg, dec_deg, lat_deg, lst_deg)
        if alt >= min_alt_deg:
            v = altaz_to_unit_vector(alt, az)
            results.append({
                "id": p["id"],
                "name": p["name"],
                "type": "planet" if p["id"] != "moon" else "moon",
                "ra": round(ra_deg, 3),
                "dec": round(dec_deg, 3),
                "mag": p["mag"],
                "altitude_deg": round(alt, 3),
                "azimuth_deg": round(az, 3),
                "unit_vector": v.tolist()
            })

    return results


def get_all_celestial_objects(
    dt: datetime.datetime,
    lat_deg: float,
    lon_deg: float,
    min_alt_deg: float = 0.0
) -> List[Dict]:
    """Returns combined visible stars, planets, and Sun."""
    stars = get_visible_stars(dt, lat_deg, lon_deg, min_alt_deg)
    planets = get_visible_planets(dt, lat_deg, lon_deg, min_alt_deg)
    sun = get_sun_coordinates(dt, lat_deg, lon_deg)

    all_objs = []
    if sun["altitude_deg"] >= min_alt_deg:
        all_objs.append(sun)
    all_objs.extend(planets)
    all_objs.extend(stars)
    return all_objs

