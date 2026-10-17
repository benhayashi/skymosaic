import datetime
import json
import logging
import re
import time
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# In-memory cache: icao -> (timestamp, data)
_METAR_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
CACHE_TTL_SEC = 300.0  # 5 minutes cache


def parse_visibility_miles(visib_raw: Any) -> float:
    """Parses visibility strings like '10+', '10', '1/2', '1 1/2', '0.75' into float statute miles."""
    if visib_raw is None:
        return 10.0
    if isinstance(visib_raw, (int, float)):
        return float(visib_raw)
    
    s = str(visib_raw).strip().rstrip("+").strip()
    try:
        if " " in s:
            parts = s.split()
            whole = float(parts[0])
            frac_parts = parts[1].split("/")
            return whole + (float(frac_parts[0]) / float(frac_parts[1]))
        elif "/" in s:
            frac_parts = s.split("/")
            return float(frac_parts[0]) / float(frac_parts[1])
        else:
            return float(s)
    except Exception:
        return 10.0


def fetch_metar(icao: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    """
    Fetches official METAR aviation weather data from NOAA Aviation Weather Center.
    No API key required.
    """
    if not icao or len(icao.strip()) < 3:
        return None

    code = icao.strip().upper()
    now = time.time()

    # Check cache
    if code in _METAR_CACHE:
        cached_time, cached_data = _METAR_CACHE[code]
        if now - cached_time < CACHE_TTL_SEC:
            return cached_data

    url = f"https://aviationweather.gov/api/data/metar?ids={code}&format=json"
    headers = {"User-Agent": "SkyMosaic/1.0 (AllSky Celestial Platform)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.warning(f"METAR request for {code} returned status {resp.status}")
                return None
            body = resp.read().decode("utf-8")
            data = json.loads(body)

            if not data or not isinstance(data, list):
                logger.warning(f"No METAR reports returned for ICAO {code}")
                return None

            item = data[0]
            visib_miles = parse_visibility_miles(item.get("visib"))
            cover = item.get("cover") or "CLR"
            
            # Find cloud ceiling (lowest BKN or OVC cloud base)
            ceiling_ft = None
            clouds = item.get("clouds", [])
            for layer in clouds:
                c_type = (layer.get("cover") or "").upper()
                base = layer.get("base")
                if c_type in ("BKN", "OVC", "VV") and base is not None:
                    if ceiling_ft is None or base < ceiling_ft:
                        ceiling_ft = float(base)

            parsed = {
                "icao": code,
                "name": item.get("name", code),
                "raw_metar": item.get("rawOb", ""),
                "receipt_time": item.get("receiptTime", ""),
                "report_time": item.get("reportTime", ""),
                "temp_c": item.get("temp"),
                "dewp_c": item.get("dewp"),
                "wind_dir": item.get("wdir"),
                "wind_speed_kt": item.get("wspd"),
                "visib_miles": visib_miles,
                "visib_km": round(visib_miles * 1.60934, 1),
                "cover": cover,
                "clouds": clouds,
                "ceiling_ft": ceiling_ft,
                "flight_category": item.get("fltCat", "VFR"),
                "lat": item.get("lat"),
                "lon": item.get("lon"),
                "elev_m": item.get("elev"),
                "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

            _METAR_CACHE[code] = (now, parsed)
            return parsed

    except Exception as e:
        logger.warning(f"Failed to fetch METAR for {code}: {e}")
        # If cache has older entry, return it on network failure
        if code in _METAR_CACHE:
            return _METAR_CACHE[code][1]
        return None


def evaluate_aircraft_visibility(
    distance_miles: float,
    altitude_ft: Optional[float] = None,
    metar: Optional[Dict[str, Any]] = None,
    mode: str = "adaptive",
) -> Dict[str, Any]:
    """
    Evaluates whether an aircraft is expected to be visually observable from the ground
    given local METAR cloud cover, ceiling, and surface visibility.
    Lenient for scattered/partly cloudy skies.
    """
    dist_round_mi = round(distance_miles, 1)
    dist_round_km = round(distance_miles * 1.60934, 1)

    if mode == "always" or not metar:
        return {
            "visible": True,
            "status": "visible",
            "reason": "All-time display",
            "distance_miles": dist_round_mi,
            "distance_km": dist_round_km,
        }

    cover = (metar.get("cover") or "CLR").upper()
    visib_miles = metar.get("visib_miles", 10.0)
    ceiling_ft = metar.get("ceiling_ft")

    # 1. Check solid cloud ceiling: If overcast or broken and plane is above the cloud deck
    if cover in ("OVC", "VV") and ceiling_ft is not None and altitude_ft is not None:
        if altitude_ft > (ceiling_ft + 300):
            return {
                "visible": False,
                "status": "obscured_ceiling",
                "reason": f"Above overcast ceiling ({int(ceiling_ft)} ft AGL)",
                "distance_miles": dist_round_mi,
                "distance_km": dist_round_km,
            }

    # 2. Check thick overcast horizontal visibility
    if cover == "OVC" and distance_miles > max(visib_miles * 1.25, 4.0):
        return {
            "visible": False,
            "status": "obscured_haze",
            "reason": f"Beyond overcast visibility limit ({visib_miles:.1f} mi)",
            "distance_miles": dist_round_mi,
            "distance_km": dist_round_km,
        }

    # 3. Broken clouds (BKN - 5/8 to 7/8 coverage)
    if cover == "BKN":
        if ceiling_ft is not None and altitude_ft is not None and altitude_ft > (ceiling_ft + 1500) and distance_miles > 12.0:
            return {
                "visible": False,
                "status": "obscured_ceiling",
                "reason": f"Obscured by broken cloud deck ({int(ceiling_ft)} ft)",
                "distance_miles": dist_round_mi,
                "distance_km": dist_round_km,
            }

    # 4. Dense surface fog / low visibility (e.g. < 2 miles)
    if visib_miles < 2.5 and distance_miles > (visib_miles * 1.5):
        return {
            "visible": False,
            "status": "obscured_fog",
            "reason": f"Obscured by ground fog/mist ({visib_miles:.1f} mi vis)",
            "distance_miles": dist_round_mi,
            "distance_km": dist_round_km,
        }

    # 5. Clear or partly cloudy (FEW, SCT) -> Visible!
    status_label = "visual_range"
    reason = f"Within visual range ({dist_round_mi} mi)"
    if cover in ("FEW", "SCT"):
        reason = f"Visible between clouds ({dist_round_mi} mi)"

    return {
        "visible": True,
        "status": status_label,
        "reason": reason,
        "distance_miles": dist_round_mi,
        "distance_km": dist_round_km,
    }


def evaluate_celestial_visibility(
    sun_alt_deg: float,
    obj_type: str,
    metar: Optional[Dict[str, Any]] = None,
    mode: str = "adaptive",
) -> Dict[str, Any]:
    """
    Evaluates whether stars, satellites, Sun, or Moon should be displayed on the sky dome.
    - Sun & Moon are always visible.
    - Stars require night or twilight (Sun alt < -6.0°) and non-overcast conditions.
    """
    if mode == "always":
        return {"visible": True, "reason": "Always show mode"}

    if obj_type.lower() in ("sun", "moon"):
        return {"visible": True, "reason": "Always visible"}

    cover = (metar.get("cover") or "").upper() if metar else "CLR"

    if obj_type.lower() == "star":
        # Stars visible during astronomical night or civil/nautical twilight (Sun < -6.0°)
        if sun_alt_deg > -6.0:
            return {"visible": False, "reason": "Daytime / daylight washes out stars"}
        if cover == "OVC":
            return {"visible": False, "reason": "Overcast cloud cover"}
        return {"visible": True, "reason": "Nighttime / twilight visibility"}

    if obj_type.lower() == "satellite":
        if sun_alt_deg > -3.0:
            return {"visible": False, "reason": "Daylight sky background"}
        if cover == "OVC":
            return {"visible": False, "reason": "Overcast cloud cover"}
        return {"visible": True, "reason": "Visible pass"}

    return {"visible": True, "reason": "Visible"}
