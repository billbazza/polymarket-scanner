"""Settlement specs for station-settled weather sub-strategies.

The current threshold scanner remains city-forecast oriented. This module adds
read-only metadata for station-settled exact-temperature markets so new weather
sub-strategies can preserve settlement-specific units, precision, and station
identity without changing the existing threshold path.
"""

import logging

log = logging.getLogger("scanner.weather_settlement")


WEATHER_SETTLEMENT_SPECS = {
    "shanghai": {
        "city_key": "shanghai",
        "aliases": ("shanghai",),
        "market_family": "weather_exact_temp",
        "source_kind": "wunderground_history",
        "resolution_source": "wunderground_history",
        "source_url_template": "https://www.wunderground.com/history/daily/cn/shanghai/ZSPD/date/{date}",
        "station_id": "ZSPD",
        "station_label": "Shanghai Pudong International Airport",
        "timezone": "Asia/Shanghai",
        "settlement_unit": "C",
        "settlement_precision": 1.0,
        "settlement_metric": "daily_high",
        "finalization_rule": "Wait for source finalization; ignore later revisions after Polymarket finalizes.",
        "forecast_lat": 31.1434,
        "forecast_lon": 121.8052,
    },
    "new york city": {
        "city_key": "new york city",
        "aliases": ("new york city", "new york", "nyc"),
        "market_family": "weather_exact_temp",
        "source_kind": "wunderground_history",
        "resolution_source": "wunderground_history",
        "source_url_template": "https://www.wunderground.com/history/daily/us/ny/queens/KLGA/date/{date}",
        "station_id": "KLGA",
        "station_label": "LaGuardia Airport",
        "timezone": "America/New_York",
        "settlement_unit": "F",
        "settlement_precision": 1.0,
        "settlement_metric": "daily_high",
        "finalization_rule": "Use finalized station history; ignore revisions after finalization.",
        "forecast_lat": 40.7769,
        "forecast_lon": -73.8740,
    },
    "seoul": {
        "city_key": "seoul",
        "aliases": ("seoul",),
        "market_family": "weather_exact_temp",
        "source_kind": "wunderground_history",
        "resolution_source": "wunderground_history",
        "source_url_template": "https://www.wunderground.com/history/daily/kr/incheon/RKSI/date/{date}",
        "station_id": "RKSI",
        "station_label": "Incheon International Airport",
        "timezone": "Asia/Seoul",
        "settlement_unit": "C",
        "settlement_precision": 1.0,
        "settlement_metric": "daily_high",
        "finalization_rule": "Use finalized station history; wait for source finalization before validation.",
        "forecast_lat": 37.4602,
        "forecast_lon": 126.4407,
    },
    "hong kong": {
        "city_key": "hong kong",
        "aliases": ("hong kong",),
        "market_family": "weather_exact_temp",
        "source_kind": "hong_kong_observatory",
        "resolution_source": "hong_kong_observatory",
        "source_url_template": "https://www.hko.gov.hk/en/wxinfo/pastwx/metob.htm",
        "station_id": "HKO-HQ",
        "station_label": "Hong Kong Observatory Headquarters",
        "timezone": "Asia/Hong_Kong",
        "settlement_unit": "C",
        "settlement_precision": 0.1,
        "settlement_metric": "daily_high",
        "finalization_rule": "Use finalized HKO daily extract absolute maximum.",
        "forecast_lat": 22.3019,
        "forecast_lon": 114.1742,
    },
}


def get_station_settlement_spec(city_key):
    """Return a settlement spec by canonical city key or alias."""
    if not city_key:
        return None
    probe = str(city_key).strip().lower()
    for spec in WEATHER_SETTLEMENT_SPECS.values():
        if probe == spec["city_key"] or probe in spec.get("aliases", ()):
            return dict(spec)
    return None


def match_station_settlement_spec(text):
    """Return the first settlement spec matched from free-form text."""
    if not text:
        return None
    probe = str(text).strip().lower()
    matches = []
    for spec in WEATHER_SETTLEMENT_SPECS.values():
        for alias in spec.get("aliases", ()):
            if alias in probe:
                matches.append((len(alias), spec))
                break
    if not matches:
        return None
    _, spec = max(matches, key=lambda item: item[0])
    return dict(spec)
