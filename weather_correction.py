"""Intraday weather correction and backtest helpers for threshold markets.

This module layers optional same-day observation context on top of the
baseline forecast-only weather strategy without replacing the baseline model.

Default rollout is compare-only:
  - baseline probability remains the selected output
  - corrected probability is computed when same-day observations are available
  - observability captures whether the correction was applied, confidence, and
    why any fallback path was taken

The correction is intentionally conservative. It uses the target day's forecast
high/low range, a simple local-time warmup curve, and optional warming/cooling
trend from recent observations to adjust the day's forecast confidence.
"""

import json
import logging
import math
from datetime import datetime

from scipy.stats import norm

import math_engine

log = logging.getLogger("scanner.weather_correction")

_SIGMA_BY_HOURS = [(24, 2.5), (48, 3.5), (72, 5.0), (999, 6.5)]
_SUNRISE_HOUR = 6.0
_PEAK_HOUR = 15.0
_MAX_TEMP_SHIFT_F = 6.0
_MAX_TREND_BIAS_F = 2.5
_MIN_SIGMA_MULTIPLIER = 0.72
_MAX_SIGMA_MULTIPLIER = 1.25


def forecast_sigma_for_hours(hours_ahead):
    """Return baseline forecast sigma for the given forecast horizon."""
    for hours, sigma in _SIGMA_BY_HOURS:
        if hours_ahead <= hours:
            return sigma
    return 6.5


def probability_from_point_forecast(forecast_val, threshold_f, direction, sigma):
    """Convert a point forecast into a threshold probability."""
    if forecast_val is None or sigma is None or sigma <= 0:
        return None
    if direction == "above":
        prob = float(1 - norm.cdf((threshold_f - forecast_val) / sigma))
    else:
        prob = float(norm.cdf((threshold_f - forecast_val) / sigma))
    return round(prob, 4)


def normalize_intraday_observations(observations):
    """Normalize optional same-day observations keyed by city name."""
    if not observations:
        return {}

    if isinstance(observations, str):
        try:
            observations = json.loads(observations)
        except json.JSONDecodeError:
            log.warning("Weather correction observations were not valid JSON.")
            return {}

    if isinstance(observations, dict):
        items = []
        for city_key, payload in observations.items():
            entry = dict(payload or {})
            entry.setdefault("city", city_key)
            items.append(entry)
    elif isinstance(observations, list):
        items = list(observations)
    else:
        return {}

    normalized = {}
    for item in items:
        city = (item or {}).get("city") or (item or {}).get("city_key")
        if not city:
            continue
        city_key = str(city).strip().lower()
        temp_f = _to_float(
            item.get("temp_f", item.get("observed_temp_f", item.get("temperature_f")))
        )
        observed_at = item.get("observed_at") or item.get("as_of")
        observed_date, observed_hour = _parse_observation_time(
            observed_at,
            item.get("observed_hour_local"),
        )
        previous_date, previous_hour = _parse_observation_time(
            item.get("previous_observed_at"),
            item.get("previous_hour_local"),
        )
        normalized[city_key] = {
            "city_key": city_key,
            "temp_f": temp_f,
            "observed_at": observed_at,
            "observed_date": observed_date,
            "observed_hour_local": observed_hour,
            "previous_temp_f": _to_float(item.get("previous_temp_f")),
            "previous_observed_at": item.get("previous_observed_at"),
            "previous_observed_date": previous_date,
            "previous_hour_local": previous_hour,
            "trend_f_per_hour": _to_float(item.get("trend_f_per_hour")),
            "source": item.get("source") or "manual",
        }
    return normalized


def apply_intraday_probability_correction(
    *,
    city_key,
    target_date,
    threshold_f,
    direction,
    hours_ahead,
    market_price,
    source_details,
    observation,
    correction_mode="shadow",
):
    """Return baseline/corrected/selected weather probabilities with audit data."""
    baseline_probs = [
        detail.get("baseline_prob")
        for detail in source_details
        if detail.get("baseline_prob") is not None
    ]
    baseline_prob = round(sum(baseline_probs) / len(baseline_probs), 4) if baseline_probs else None
    correction_items = []
    corrected_probs = []

    for detail in source_details:
        correction = _correct_source_probability(
            source_detail=detail,
            observation=observation,
            target_date=target_date,
            threshold_f=threshold_f,
            direction=direction,
            hours_ahead=hours_ahead,
        )
        correction_items.append(correction)
        chosen_prob = correction.get("corrected_prob")
        if chosen_prob is None:
            chosen_prob = detail.get("baseline_prob")
        if chosen_prob is not None:
            corrected_probs.append(chosen_prob)

    corrected_prob = round(sum(corrected_probs) / len(corrected_probs), 4) if corrected_probs else baseline_prob
    applied = [item for item in correction_items if item.get("applied")]
    avg_confidence = round(
        sum(item.get("confidence_weight", 0.0) for item in applied) / len(applied),
        4,
    ) if applied else 0.0
    selected_prob = baseline_prob
    compare_only = True
    selection_reason = "baseline_default"

    if correction_mode == "blend" and baseline_prob is not None and corrected_prob is not None and avg_confidence > 0:
        selected_prob = round(
            baseline_prob * (1 - avg_confidence) + corrected_prob * avg_confidence,
            4,
        )
        compare_only = False
        selection_reason = "confidence_blend"
    elif correction_mode == "corrected" and corrected_prob is not None:
        selected_prob = corrected_prob
        compare_only = False
        selection_reason = "corrected_selected"

    baseline_metrics = build_market_metrics(baseline_prob, market_price) if baseline_prob is not None else {}
    corrected_metrics = build_market_metrics(corrected_prob, market_price) if corrected_prob is not None else {}
    selected_metrics = build_market_metrics(selected_prob, market_price) if selected_prob is not None else {}

    if applied:
        status = "corrected"
        reason = f"Applied {len(applied)} source correction(s)."
    elif observation:
        fallback_reasons = sorted({
            item.get("reason_code")
            for item in correction_items
            if item.get("reason_code") and item.get("reason_code") != "baseline_only"
        })
        status = "fallback"
        reason = ", ".join(fallback_reasons) if fallback_reasons else "baseline_only"
    else:
        status = "no_observation"
        reason = "baseline_only"

    return {
        "city_key": city_key,
        "target_date": target_date,
        "mode": correction_mode,
        "compare_only": compare_only,
        "status": status,
        "reason": reason,
        "selection_reason": selection_reason,
        "applied_sources": len(applied),
        "confidence_weight": avg_confidence,
        "baseline_prob": baseline_prob,
        "corrected_prob": corrected_prob,
        "selected_prob": selected_prob,
        "baseline_metrics": baseline_metrics,
        "corrected_metrics": corrected_metrics,
        "selected_metrics": selected_metrics,
        "observation": observation,
        "source_corrections": correction_items,
    }


def build_market_metrics(probability, market_price):
    """Return consistent action, EV, and Kelly metrics for a weather probability."""
    combined_edge = probability - market_price
    action = "BUY_YES" if combined_edge > 0 else "BUY_NO"
    our_price = market_price if action == "BUY_YES" else (1 - market_price)
    our_prob = probability if action == "BUY_YES" else (1 - probability)
    ev_pct = round(
        (our_prob * (1 - our_price) - (1 - our_prob) * our_price) * 100,
        2,
    )
    return {
        "action": action,
        "edge": round(combined_edge, 4),
        "edge_pct": round(combined_edge * 100, 2),
        "ev_pct": ev_pct,
        "kelly_fraction": math_engine.kelly_fraction(our_prob, 1 - our_price, our_price),
    }


def evaluate_intraday_correction(samples, min_edge=0.06):
    """Backtest baseline vs corrected probabilities on labeled intraday samples."""
    results = []
    baseline_brier = 0.0
    corrected_brier = 0.0
    baseline_log_loss = 0.0
    corrected_log_loss = 0.0
    baseline_edge_realized = 0.0
    corrected_edge_realized = 0.0
    baseline_edge_count = 0
    corrected_edge_count = 0

    for sample in samples:
        source_details = [
            {
                "source_id": source["source_id"],
                "value_f": source.get("forecast_high_f"),
                "low_f": source.get("forecast_low_f"),
                "baseline_prob": probability_from_point_forecast(
                    source.get("forecast_high_f"),
                    sample["threshold_f"],
                    sample["direction"],
                    source.get("sigma_f") or forecast_sigma_for_hours(sample["hours_ahead"]),
                ),
                "baseline_sigma_f": source.get("sigma_f") or forecast_sigma_for_hours(sample["hours_ahead"]),
            }
            for source in sample.get("sources", [])
        ]
        correction = apply_intraday_probability_correction(
            city_key=sample["city"],
            target_date=sample["target_date"],
            threshold_f=sample["threshold_f"],
            direction=sample["direction"],
            hours_ahead=sample["hours_ahead"],
            market_price=sample["market_price"],
            source_details=source_details,
            observation=normalize_intraday_observations([dict(sample["observation"], city=sample["city"])]).get(sample["city"].lower()),
            correction_mode="corrected",
        )
        actual_outcome = 1.0 if _actual_threshold_outcome(sample["actual_high_f"], sample["threshold_f"], sample["direction"]) else 0.0
        baseline_prob = correction["baseline_prob"]
        corrected_prob = correction["corrected_prob"]
        baseline_brier += (baseline_prob - actual_outcome) ** 2
        corrected_brier += (corrected_prob - actual_outcome) ** 2
        baseline_log_loss += _log_loss(actual_outcome, baseline_prob)
        corrected_log_loss += _log_loss(actual_outcome, corrected_prob)

        baseline_metrics = correction["baseline_metrics"]
        corrected_metrics = correction["corrected_metrics"]

        if abs(baseline_metrics.get("edge", 0.0)) >= min_edge:
            baseline_edge_count += 1
            baseline_edge_realized += _realized_trade_value(
                baseline_metrics["action"],
                sample["market_price"],
                actual_outcome,
            )
        if abs(corrected_metrics.get("edge", 0.0)) >= min_edge:
            corrected_edge_count += 1
            corrected_edge_realized += _realized_trade_value(
                corrected_metrics["action"],
                sample["market_price"],
                actual_outcome,
            )

        results.append({
            "sample_id": sample.get("id"),
            "city": sample["city"],
            "baseline_prob": baseline_prob,
            "corrected_prob": corrected_prob,
            "actual_outcome": actual_outcome,
            "baseline_edge": baseline_metrics.get("edge"),
            "corrected_edge": corrected_metrics.get("edge"),
            "baseline_action": baseline_metrics.get("action"),
            "corrected_action": corrected_metrics.get("action"),
        })

    count = len(results) or 1
    return {
        "sample_count": len(results),
        "baseline": {
            "brier": round(baseline_brier / count, 4),
            "log_loss": round(baseline_log_loss / count, 4),
            "edge_realized_avg": round((baseline_edge_realized / baseline_edge_count), 4) if baseline_edge_count else 0.0,
            "edge_trade_count": baseline_edge_count,
        },
        "corrected": {
            "brier": round(corrected_brier / count, 4),
            "log_loss": round(corrected_log_loss / count, 4),
            "edge_realized_avg": round((corrected_edge_realized / corrected_edge_count), 4) if corrected_edge_count else 0.0,
            "edge_trade_count": corrected_edge_count,
        },
        "improvement": {
            "brier_delta": round((baseline_brier - corrected_brier) / count, 4),
            "log_loss_delta": round((baseline_log_loss - corrected_log_loss) / count, 4),
            "edge_realized_delta": round(
                ((corrected_edge_realized / corrected_edge_count) if corrected_edge_count else 0.0)
                - ((baseline_edge_realized / baseline_edge_count) if baseline_edge_count else 0.0),
                4,
            ),
        },
        "results": results,
    }


def _correct_source_probability(
    *,
    source_detail,
    observation,
    target_date,
    threshold_f,
    direction,
    hours_ahead,
):
    baseline_prob = source_detail.get("baseline_prob")
    baseline_sigma = source_detail.get("baseline_sigma_f") or forecast_sigma_for_hours(hours_ahead)
    forecast_high = _to_float(source_detail.get("value_f"))
    forecast_low = _to_float(source_detail.get("low_f"))
    base = {
        "source_id": source_detail.get("source_id"),
        "applied": False,
        "reason_code": "baseline_only",
        "confidence_weight": 0.0,
        "baseline_prob": baseline_prob,
        "baseline_sigma_f": baseline_sigma,
        "corrected_prob": baseline_prob,
        "corrected_sigma_f": baseline_sigma,
        "corrected_forecast_f": forecast_high,
    }

    if baseline_prob is None or forecast_high is None:
        base["reason_code"] = "missing_baseline_forecast"
        return base
    if not observation:
        base["reason_code"] = "no_observation"
        return base
    if forecast_low is None:
        base["reason_code"] = "missing_daily_low"
        return base

    observed_temp = _to_float(observation.get("temp_f"))
    observed_hour = _to_float(observation.get("observed_hour_local"))
    observed_date = observation.get("observed_date")
    if observed_temp is None or observed_hour is None:
        base["reason_code"] = "invalid_observation"
        return base
    if observed_date and observed_date != target_date:
        base["reason_code"] = "different_target_date"
        return base
    if observed_hour > (_PEAK_HOUR + 2):
        base["reason_code"] = "observation_after_peak_window"
        return base

    daily_range = max(1.0, forecast_high - forecast_low)
    expected_progress = _expected_warmup_progress(observed_hour)
    expected_temp = forecast_low + daily_range * expected_progress
    temp_delta = observed_temp - expected_temp
    previous_temp = _to_float(observation.get("previous_temp_f"))
    previous_hour = _to_float(observation.get("previous_hour_local"))
    trend_f_per_hour = _derive_trend(observation, observed_temp, observed_hour, previous_temp, previous_hour)
    remaining_hours = max(0.0, _PEAK_HOUR - observed_hour)

    response_weight = min(0.7, 0.2 + (expected_progress * 0.5))
    trend_bias = 0.0
    if trend_f_per_hour is not None:
        trend_bias = max(-_MAX_TREND_BIAS_F, min(_MAX_TREND_BIAS_F, trend_f_per_hour * remaining_hours * 0.18))

    corrected_forecast = forecast_high + (temp_delta * response_weight) + trend_bias
    corrected_forecast = max(observed_temp, corrected_forecast)
    corrected_forecast = forecast_high + max(-_MAX_TEMP_SHIFT_F, min(_MAX_TEMP_SHIFT_F, corrected_forecast - forecast_high))

    confidence_weight = min(
        0.75,
        0.18 + (expected_progress * 0.42) + (0.12 if trend_f_per_hour is not None else 0.0),
    )
    sigma_multiplier = 1.0 - (confidence_weight * 0.35)
    if abs(temp_delta) >= 4:
        sigma_multiplier += 0.08
    sigma_multiplier = max(_MIN_SIGMA_MULTIPLIER, min(_MAX_SIGMA_MULTIPLIER, sigma_multiplier))
    corrected_sigma = round(baseline_sigma * sigma_multiplier, 4)
    corrected_prob = probability_from_point_forecast(
        corrected_forecast,
        threshold_f,
        direction,
        corrected_sigma,
    )

    base.update({
        "applied": True,
        "reason_code": "corrected",
        "confidence_weight": round(confidence_weight, 4),
        "corrected_prob": corrected_prob,
        "corrected_sigma_f": corrected_sigma,
        "corrected_forecast_f": round(corrected_forecast, 2),
        "expected_temp_f": round(expected_temp, 2),
        "observed_temp_f": round(observed_temp, 2),
        "temp_delta_f": round(temp_delta, 2),
        "trend_f_per_hour": round(trend_f_per_hour, 3) if trend_f_per_hour is not None else None,
    })
    return base


def _derive_trend(observation, observed_temp, observed_hour, previous_temp, previous_hour):
    trend_f_per_hour = _to_float(observation.get("trend_f_per_hour"))
    if trend_f_per_hour is not None:
        return trend_f_per_hour
    if previous_temp is None or previous_hour is None:
        return None
    delta_hours = observed_hour - previous_hour
    if delta_hours <= 0:
        return None
    return (observed_temp - previous_temp) / delta_hours


def _expected_warmup_progress(observed_hour):
    if observed_hour <= _SUNRISE_HOUR:
        return 0.05
    if observed_hour >= _PEAK_HOUR:
        return 1.0
    scaled = (observed_hour - _SUNRISE_HOUR) / (_PEAK_HOUR - _SUNRISE_HOUR)
    curved = 0.08 + (0.92 * math.sqrt(max(0.0, scaled)))
    return max(0.05, min(1.0, curved))


def _parse_observation_time(timestamp_value, explicit_hour):
    explicit = _to_float(explicit_hour)
    if explicit is not None:
        return None, explicit
    if not timestamp_value:
        return None, None
    if isinstance(timestamp_value, (int, float)):
        return None, float(timestamp_value)
    try:
        iso_value = str(timestamp_value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(iso_value)
        hour = parsed.hour + (parsed.minute / 60.0)
        return parsed.date().isoformat(), round(hour, 2)
    except ValueError:
        return None, None


def _actual_threshold_outcome(actual_high_f, threshold_f, direction):
    if direction == "above":
        return actual_high_f >= threshold_f
    return actual_high_f < threshold_f


def _realized_trade_value(action, market_price, actual_outcome):
    if action == "BUY_YES":
        return round(actual_outcome - market_price, 4)
    return round((1.0 - actual_outcome) - (1.0 - market_price), 4)


def _log_loss(outcome, probability):
    prob = min(0.9999, max(0.0001, probability))
    return -((outcome * math.log(prob)) + ((1 - outcome) * math.log(1 - prob)))


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
