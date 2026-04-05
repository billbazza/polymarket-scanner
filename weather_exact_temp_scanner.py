"""Scanner for station-settled exact-temperature weather markets.

This is an opt-in sub-strategy for markets that settle to a named station and
unit/precision regime. It does not alter the existing threshold scanner.
"""

import json
import logging
import math
import time
from datetime import date

import api
import runtime_config
import weather_scanner
import weather_sources
import weather_settlement

log = logging.getLogger("scanner.weather_exact_temp")

MIN_EDGE = weather_scanner.MIN_EDGE
MIN_TRADE_EDGE = weather_scanner.MIN_TRADE_EDGE
MIN_TRADE_PRICE = weather_scanner.MIN_TRADE_PRICE
MIN_LIQUIDITY = weather_scanner.MIN_LIQUIDITY

ENABLE_ENV = "WEATHER_EXACT_TEMP_ENABLED"
AUTOTRADE_ENV = "WEATHER_EXACT_TEMP_AUTOTRADE"


def exact_temp_enabled(default=False):
    """Return True when exact-temperature scans are explicitly enabled."""
    raw = runtime_config.get_raw(ENABLE_ENV)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def exact_temp_autotrade_enabled(default=False):
    """Return True when autonomy may paper-trade exact-temperature signals."""
    raw = runtime_config.get_raw(AUTOTRADE_ENV)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _fahrenheit_to_celsius(value_f):
    return (float(value_f) - 32.0) * 5.0 / 9.0


def _convert_from_fahrenheit(value_f, unit):
    if value_f is None:
        return None
    return round(_fahrenheit_to_celsius(value_f), 3) if unit == "C" else round(float(value_f), 3)


def _convert_sigma_from_fahrenheit(sigma_f, unit):
    if unit == "C":
        return sigma_f * 5.0 / 9.0
    return sigma_f


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _parse_json_list(raw):
    if isinstance(raw, list):
        return raw
    if raw in (None, ""):
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _parse_exact_temp_event(event_title, market_title=""):
    combined = " ".join(part for part in [event_title, market_title] if part).lower()
    spec = weather_settlement.match_station_settlement_spec(combined)
    if not spec:
        return None

    target = weather_scanner._parse_date(combined)
    if target is None:
        return None

    days_ahead = (target - date.today()).days
    if days_ahead < 0 or days_ahead > 7:
        return None

    return {
        "city": spec["city_key"],
        "city_key": spec["city_key"],
        "lat": spec["forecast_lat"],
        "lon": spec["forecast_lon"],
        "target_date": target.isoformat(),
        "days_ahead": days_ahead,
        "settlement_spec": spec,
    }


def _parse_outcome_bin(label, settlement_unit, precision):
    text = str(label or "").strip()
    lower = text.lower().replace("degrees", "").replace("degree", "").strip()
    compact = lower.replace(" ", "")

    unit = settlement_unit
    if "°c" in lower or lower.endswith("c") or " c" in lower:
        unit = "C"
    elif "°f" in lower or lower.endswith("f") or " f" in lower:
        unit = "F"

    numeric = compact.replace("°", "")
    numeric = numeric.replace("c", "").replace("f", "")

    if "orhigher" in numeric:
        value = float(numeric.split("orhigher", 1)[0])
        return {
            "label": text,
            "kind": "gte",
            "unit": unit,
            "low": value - (precision / 2.0),
            "high": None,
            "target_value": value,
        }
    if "orlower" in numeric:
        value = float(numeric.split("orlower", 1)[0])
        return {
            "label": text,
            "kind": "lte",
            "unit": unit,
            "low": None,
            "high": value + (precision / 2.0),
            "target_value": value,
        }
    if "-" in numeric:
        left, right = numeric.split("-", 1)
        low = float(left)
        high = float(right)
        return {
            "label": text,
            "kind": "range",
            "unit": unit,
            "low": low - (precision / 2.0),
            "high": high + (precision / 2.0),
            "target_value": round((low + high) / 2.0, 3),
        }

    value = float(numeric)
    return {
        "label": text,
        "kind": "exact",
        "unit": unit,
        "low": value - (precision / 2.0),
        "high": value + (precision / 2.0),
        "target_value": value,
    }


def _probability_for_bin(mu, sigma, outcome_bin):
    if mu is None or sigma is None or sigma <= 0:
        return None
    low = outcome_bin.get("low")
    high = outcome_bin.get("high")
    if low is None and high is None:
        return None
    if low is None:
        return round(_normal_cdf((high - mu) / sigma), 4)
    if high is None:
        return round(1.0 - _normal_cdf((low - mu) / sigma), 4)
    return round(max(0.0, _normal_cdf((high - mu) / sigma) - _normal_cdf((low - mu) / sigma)), 4)


def _build_yes_only_metrics(probability, market_price):
    edge = round(float(probability) - float(market_price), 4)
    ev_pct = round((float(probability) - float(market_price)) * 100, 2)
    kelly_fraction = 0.0
    if probability > market_price and 0 < market_price < 1:
        try:
            import math_engine

            kelly_fraction = math_engine.kelly_fraction(probability, 1 - market_price, market_price)
        except Exception:
            kelly_fraction = 0.0
    return {
        "action": "BUY_YES",
        "edge": edge,
        "edge_pct": round(edge * 100, 2),
        "ev_pct": ev_pct,
        "kelly_fraction": kelly_fraction,
    }


def scan(min_edge=MIN_EDGE, min_liquidity=MIN_LIQUIDITY, verbose=True):
    """Scan exact-temperature weather ladders when explicitly enabled."""
    if not exact_temp_enabled():
        return [], {
            "enabled": False,
            "markets_checked": 0,
            "exact_temp_events": 0,
            "tradeable": 0,
        }

    t0 = time.time()
    try:
        all_weather = []
        for page in range(5):
            batch = api.get_events(limit=100, offset=page * 100, tag_slug="weather")
            if not batch:
                break
            all_weather.extend(batch)
            if len(batch) < 100:
                break
        events = all_weather
    except Exception as exc:
        log.error("Failed to fetch weather events for exact-temp scan: %s", exc)
        return [], {
            "enabled": True,
            "markets_checked": 0,
            "exact_temp_events": 0,
            "tradeable": 0,
            "error": str(exc),
        }

    opportunities = []
    markets_checked = 0
    exact_temp_events = 0

    for event in events:
        liq = float(event.get("liquidity", 0) or 0)
        if liq < min_liquidity:
            continue

        event_title = event.get("title", "")
        for market in event.get("markets", []):
            question = market.get("question", market.get("groupItemTitle", ""))
            parsed_event = _parse_exact_temp_event(event_title, question)
            if parsed_event is None:
                continue

            markets_checked += 1
            exact_temp_events += 1
            spec = parsed_event["settlement_spec"]
            settlement_unit = spec["settlement_unit"]
            precision = float(spec["settlement_precision"])
            prices = _parse_json_list(market.get("outcomePrices"))
            tokens = _parse_json_list(market.get("clobTokenIds"))
            labels = _parse_json_list(market.get("outcomes")) or _parse_json_list(market.get("outcomeNames"))
            if not prices or not tokens or not labels or len(prices) != len(tokens) or len(prices) != len(labels):
                continue

            hours_ahead = parsed_event["days_ahead"] * 24
            sigma = _convert_sigma_from_fahrenheit(
                weather_scanner._forecast_sigma(hours_ahead),
                settlement_unit,
            )
            source_results = weather_sources.fetch_threshold_forecasts(
                parsed_event["lat"],
                parsed_event["lon"],
                parsed_event["target_date"],
                city_key=parsed_event["city_key"],
            )
            available_sources = []
            for result in source_results:
                converted_value = _convert_from_fahrenheit(result.get("value_f"), settlement_unit)
                if converted_value is None:
                    continue
                per_outcome = []
                for idx, label in enumerate(labels):
                    try:
                        outcome_bin = _parse_outcome_bin(label, settlement_unit, precision)
                    except (TypeError, ValueError):
                        per_outcome = []
                        break
                    probability = _probability_for_bin(converted_value, sigma, outcome_bin)
                    per_outcome.append({
                        "index": idx,
                        "label": label,
                        "probability": probability,
                    })
                if not per_outcome:
                    continue
                top = max(per_outcome, key=lambda item: item["probability"] or 0)
                available_sources.append({
                    **result,
                    "converted_value": converted_value,
                    "settlement_unit": settlement_unit,
                    "settlement_precision": precision,
                    "per_outcome": per_outcome,
                    "top_outcome_index": top["index"],
                    "top_outcome_label": top["label"],
                })

            if not available_sources:
                continue

            for idx, label in enumerate(labels):
                try:
                    market_price = float(prices[idx])
                except (TypeError, ValueError):
                    continue
                if not (0.01 <= market_price <= 0.99):
                    continue
                token_id = tokens[idx] if idx < len(tokens) else None
                if not token_id:
                    continue

                source_probabilities = []
                for source in available_sources:
                    probability = next(
                        (item["probability"] for item in source["per_outcome"] if item["index"] == idx),
                        None,
                    )
                    if probability is not None:
                        source_probabilities.append((source, probability))
                if not source_probabilities:
                    continue

                combined_prob = round(
                    sum(probability for _, probability in source_probabilities) / len(source_probabilities),
                    4,
                )
                metrics = _build_yes_only_metrics(combined_prob, market_price)
                if abs(metrics["edge"]) < min_edge:
                    continue

                top_source_labels = {source["top_outcome_label"] for source, _ in source_probabilities}
                sources_agree = len(source_probabilities) >= 2 and len(top_source_labels) == 1 and label in top_source_labels
                tradeable = (
                    sources_agree
                    and metrics["ev_pct"] > 0
                    and metrics["kelly_fraction"] > 0
                    and metrics["edge"] >= MIN_TRADE_EDGE
                    and market_price >= MIN_TRADE_PRICE
                )

                source_meta = {
                    "strategy_name": "weather_exact_temp",
                    "outcome_index": idx,
                    "outcome_label": label,
                    "per_source": [
                        {
                            "source_id": source.get("source_id"),
                            "source_name": source.get("source_name"),
                            "converted_value": source.get("converted_value"),
                            "top_outcome_label": source.get("top_outcome_label"),
                        }
                        for source, _ in source_probabilities
                    ],
                    "settlement_spec": spec,
                }
                opp = {
                    "strategy_name": "weather_exact_temp",
                    "market_family": spec["market_family"],
                    "event": event_title,
                    "market": f"{question or event_title} [{label}]",
                    "market_id": f"{market.get('id', '')}:{idx}",
                    "yes_token": token_id,
                    "no_token": None,
                    "city": parsed_event["city"],
                    "lat": parsed_event["lat"],
                    "lon": parsed_event["lon"],
                    "target_date": parsed_event["target_date"],
                    "threshold_f": None,
                    "direction": "exact",
                    "market_price": round(market_price, 4),
                    "noaa_forecast_f": next((source.get("value_f") for source, _ in source_probabilities if source.get("source_id") == "noaa"), None),
                    "noaa_prob": next((prob for source, prob in source_probabilities if source.get("source_id") == "noaa"), None),
                    "noaa_sigma_f": weather_scanner._forecast_sigma(hours_ahead),
                    "om_forecast_f": next((source.get("value_f") for source, _ in source_probabilities if source.get("source_id") == "open-meteo"), None),
                    "om_prob": next((prob for source, prob in source_probabilities if source.get("source_id") == "open-meteo"), None),
                    "combined_prob": combined_prob,
                    "combined_edge": metrics["edge"],
                    "combined_edge_pct": metrics["edge_pct"],
                    "selected_prob": combined_prob,
                    "selected_edge": metrics["edge"],
                    "selected_edge_pct": metrics["edge_pct"],
                    "sources_agree": sources_agree,
                    "sources_available": len(source_probabilities),
                    "hours_ahead": hours_ahead,
                    "ev_pct": metrics["ev_pct"],
                    "kelly_fraction": metrics["kelly_fraction"],
                    "action": "BUY_YES",
                    "tradeable": tradeable,
                    "liquidity": liq,
                    "resolution_source": spec["resolution_source"],
                    "station_id": spec["station_id"],
                    "station_label": spec["station_label"],
                    "settlement_unit": settlement_unit,
                    "settlement_precision": precision,
                    "station_timezone": spec["timezone"],
                    "outcome_label": label,
                    "source_meta": source_meta,
                }
                opportunities.append(opp)

    opportunities.sort(key=lambda item: (not item.get("tradeable"), -abs(item.get("combined_edge", 0))))
    duration = round(time.time() - t0, 1)
    tradeable_count = sum(1 for item in opportunities if item.get("tradeable"))
    log.info(
        "Exact-temp weather scan: %d markets, %d candidates (%d tradeable) in %.1fs",
        markets_checked,
        len(opportunities),
        tradeable_count,
        duration,
    )
    if verbose:
        print(
            f"Exact-temp weather: {markets_checked} markets -> {len(opportunities)} "
            f"candidates ({tradeable_count} tradeable) in {duration}s"
        )
    return opportunities, {
        "enabled": True,
        "markets_checked": markets_checked,
        "exact_temp_events": exact_temp_events,
        "tradeable": tradeable_count,
        "duration_secs": duration,
    }
