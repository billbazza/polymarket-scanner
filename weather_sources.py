"""Weather data-source registry for forecast and settlement expansion.

This module centralizes provider metadata, caching, and fetch orchestration so
weather sub-strategies can compose multiple forecast and settlement sources
without rewriting scanner-specific logic.

Today it exposes the existing threshold-market forecast defaults:
  - NOAA NWS hourly forecast → target-day high (US coverage only)
  - Open-Meteo daily forecast → target-day high (global)

The current weather scanner remains forecast-only. Settlement-source support is
reserved for future strategies and can coexist through the same registry shape.
"""

import logging
import time

import requests

log = logging.getLogger("scanner.weather_sources")

NWS_BASE = "https://api.weather.gov"
NWS_HEADERS = {
    "User-Agent": "PolymarketWeatherScanner/1.0 (statistical-research)",
    "Accept": "application/geo+json",
}
OPENMETEO_BASE = "https://api.open-meteo.com/v1/forecast"

# Cities covered by NOAA NWS (US only).
US_CITY_KEYS = frozenset([
    "new york city", "new york", "nyc", "los angeles", "chicago", "houston",
    "phoenix", "philadelphia", "san antonio", "san diego", "dallas",
    "san francisco", "seattle", "denver", "boston", "miami", "atlanta",
    "minneapolis", "washington dc", "washington", "las vegas", "portland",
    "nashville", "memphis", "baltimore", "milwaukee", "albuquerque",
    "sacramento", "kansas city", "raleigh", "tampa", "new orleans",
    "cleveland", "pittsburgh", "detroit", "indianapolis", "jacksonville",
    "charlotte", "austin", "orlando", "cincinnati", "st. louis", "st louis",
])

WEATHER_DATA_SOURCES = [
    {
        "id": "noaa",
        "name": "NOAA NWS",
        "source_kind": "forecast",
        "metric": "daily_high",
        "default_for": ("weather_threshold",),
        "scope": "us_only",
        "optional": True,
    },
    {
        "id": "open-meteo",
        "name": "Open-Meteo",
        "source_kind": "forecast",
        "metric": "daily_high",
        "default_for": ("weather_threshold",),
        "scope": "global",
        "optional": True,
    },
]

_nws_gridpoint_cache = {}
_nws_periods_cache = {}
_openmeteo_cache = {}


def list_weather_data_sources(source_kind=None):
    """Return registered weather data sources as structured dicts."""
    sources = WEATHER_DATA_SOURCES
    if source_kind:
        sources = [s for s in sources if s.get("source_kind") == source_kind]
    return [dict(source) for source in sources]


def is_us_city_key(city_key):
    """Return True when the city key is supported by NOAA NWS."""
    return city_key in US_CITY_KEYS


def get_threshold_source_plan(city_key=None):
    """Return the ordered source plan for threshold-weather scans."""
    plan = []
    for source in list_weather_data_sources(source_kind="forecast"):
        item = dict(source)
        if item["id"] == "noaa" and not is_us_city_key(city_key):
            item["applicable"] = False
            item["skip_reason"] = "city_not_supported"
        else:
            item["applicable"] = True
            item["skip_reason"] = None
        plan.append(item)
    return plan


def fetch_threshold_forecasts(lat, lon, target_date_iso, city_key=None):
    """Fetch target-day high forecasts for all default threshold providers."""
    results = []
    for source in get_threshold_source_plan(city_key=city_key):
        source_id = source["id"]
        base_result = {
            "source_id": source_id,
            "source_name": source["name"],
            "source_kind": source["source_kind"],
            "metric": source["metric"],
            "scope": source["scope"],
            "optional": bool(source.get("optional")),
            "attempted": bool(source.get("applicable")),
            "supported": bool(source.get("applicable")),
            "available": False,
            "value_f": None,
            "error": None,
            "meta": {
                "target_date": target_date_iso,
            },
        }
        if not source.get("applicable"):
            base_result["meta"]["skip_reason"] = source.get("skip_reason")
            results.append(base_result)
            continue

        try:
            if source_id == "noaa":
                value_f = _nws_daily_high(lat, lon, target_date_iso)
            elif source_id == "open-meteo":
                value_f = _om_daily_high(lat, lon, target_date_iso)
            else:
                raise ValueError(f"Unknown weather source: {source_id}")
        except Exception as exc:
            base_result["error"] = str(exc)
            base_result["meta"]["failure_reason"] = "fetch_exception"
            results.append(base_result)
            continue

        base_result["value_f"] = value_f
        base_result["available"] = value_f is not None
        if value_f is None:
            base_result["meta"]["failure_reason"] = "no_target_value"
        results.append(base_result)

    return results


def _nws_get(url, retries=2):
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=NWS_HEADERS, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout):
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
            else:
                raise
        except requests.HTTPError as exc:
            log.warning("NWS HTTP %s: %s", url[:60], exc)
            raise


def _nws_hourly_url(lat, lon):
    """Resolve lat/lon to NWS forecastHourly URL. Cached."""
    key = (round(lat, 3), round(lon, 3))
    if key in _nws_gridpoint_cache:
        return _nws_gridpoint_cache[key]
    try:
        data = _nws_get(f"{NWS_BASE}/points/{lat},{lon}")
        url = data["properties"]["forecastHourly"]
        _nws_gridpoint_cache[key] = url
        return url
    except Exception as exc:
        log.warning("NWS gridpoint failed (%.3f,%.3f): %s", lat, lon, exc)
        return None


def _nws_daily_high(lat, lon, target_date_iso):
    """Return NOAA forecast daily high (°F) for target date, or None."""
    hourly_url = _nws_hourly_url(lat, lon)
    if hourly_url is None:
        return None

    if hourly_url not in _nws_periods_cache:
        try:
            data = _nws_get(hourly_url)
            _nws_periods_cache[hourly_url] = data["properties"]["periods"]
        except Exception as exc:
            log.warning("NWS hourly fetch failed: %s", exc)
            return None

    periods = _nws_periods_cache[hourly_url]
    temps = []
    for period in periods:
        if not period.get("startTime", "").startswith(target_date_iso):
            continue
        temp = period.get("temperature")
        if temp is None:
            continue
        temp = float(temp)
        if period.get("temperatureUnit", "F") == "C":
            temp = temp * 9 / 5 + 32
        temps.append(temp)

    return round(max(temps), 1) if temps else None


def _openmeteo_forecasts(lat, lon):
    """Fetch Open-Meteo daily highs/lows for all available dates. Cached."""
    key = (round(lat, 3), round(lon, 3))
    if key in _openmeteo_cache:
        return _openmeteo_cache[key]

    try:
        resp = requests.get(
            OPENMETEO_BASE,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": "auto",
                "forecast_days": 8,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Open-Meteo fetch failed (%.3f,%.3f): %s", lat, lon, exc)
        _openmeteo_cache[key] = {}
        return {}

    daily = data.get("daily", {})
    times = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])

    result = {}
    for i, current_date in enumerate(times):
        result[current_date] = {
            "high": round(float(highs[i]), 1) if i < len(highs) and highs[i] is not None else None,
            "low": round(float(lows[i]), 1) if i < len(lows) and lows[i] is not None else None,
        }

    _openmeteo_cache[key] = result
    log.debug("Open-Meteo: %.3f,%.3f → %d days cached", lat, lon, len(result))
    return result


def _om_daily_high(lat, lon, target_date_iso):
    """Return Open-Meteo forecast daily high (°F) for target date, or None."""
    forecasts = _openmeteo_forecasts(lat, lon)
    entry = forecasts.get(target_date_iso)
    return entry["high"] if entry else None
