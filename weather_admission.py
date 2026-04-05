"""Shared weather threshold-admission helpers."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import weather_guard_state

log = logging.getLogger("scanner.weather_admission")

WEATHER_HORIZON_PRECISION_HOURS = Decimal("0.1")
DEFAULT_MIN_TRADE_EDGE = 0.15
DEFAULT_MIN_TRADE_PRICE = 0.35

FILTER_REASON_CODES = {
    "sources_agree": "sources_disagree",
    "ev_pct": "negative_ev",
    "kelly_fraction": "kelly_zero",
    "trade_edge": "edge_below_trade_min",
    "trade_price": "price_below_trade_min",
    "liquidity": "liquidity_too_low",
    "horizon": "horizon_too_short",
    "disagreement": "source_disagreement_too_wide",
}


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quantize_hours(value, precision: Decimal = WEATHER_HORIZON_PRECISION_HOURS):
    hours = _safe_float(value)
    if hours is None:
        return None
    try:
        quantized = Decimal(str(hours)).quantize(precision, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return float(quantized)


def current_guard_thresholds(guard: dict | None = None) -> dict:
    guard = guard or weather_guard_state.current_guard()
    return {
        "min_liquidity": float(guard["min_liquidity"]),
        "min_hours_ahead": float(guard["min_hours_ahead"]),
        "max_disagreement": float(guard["max_disagreement"]),
        "guard_name": guard.get("name"),
        "guard_tier": guard.get("tier_index"),
    }


def build_threshold_admission(
    *,
    sources_agree,
    ev_pct,
    kelly_fraction,
    combined_edge,
    baseline_price,
    liquidity,
    hours_ahead,
    source_disagreement,
    min_trade_edge,
    min_trade_price,
    guard: dict | None = None,
):
    thresholds = current_guard_thresholds(guard=guard)
    horizon_hours = _safe_float(hours_ahead)
    horizon_hours_cmp = _quantize_hours(horizon_hours)
    min_hours_cmp = _quantize_hours(thresholds["min_hours_ahead"])
    filter_status = {
        "sources_agree": bool(sources_agree),
        "ev_pct": (_safe_float(ev_pct) or 0.0) > 0,
        "kelly_fraction": (_safe_float(kelly_fraction) or 0.0) > 0,
        "trade_edge": abs(_safe_float(combined_edge) or 0.0) >= float(min_trade_edge),
        "trade_price": (_safe_float(baseline_price) or 0.0) >= float(min_trade_price),
        "liquidity": (_safe_float(liquidity) or 0.0) >= thresholds["min_liquidity"],
        "horizon": (
            horizon_hours_cmp is not None
            and min_hours_cmp is not None
            and horizon_hours_cmp >= min_hours_cmp
        ),
        "disagreement": (
            _safe_float(source_disagreement) is not None
            and float(source_disagreement) <= thresholds["max_disagreement"]
        ),
    }
    blocking_filters = [name for name, ok in filter_status.items() if not ok]
    primary_blocker = blocking_filters[0] if blocking_filters else None
    primary_reason_code = FILTER_REASON_CODES.get(primary_blocker) if primary_blocker else "ready"
    if primary_blocker == "horizon":
        primary_reason = (
            f"Signal horizon now {horizon_hours_cmp:.1f}h, below required "
            f"{min_hours_cmp:.1f}h minimum."
            if horizon_hours_cmp is not None and min_hours_cmp is not None
            else "Signal missing horizon metadata."
        )
    elif primary_blocker == "liquidity":
        primary_reason = (
            f"Signal liquidity {_safe_float(liquidity) or 0.0:.0f} is below required "
            f"{thresholds['min_liquidity']:.0f} minimum."
        )
    elif primary_blocker == "disagreement":
        disagreement = _safe_float(source_disagreement)
        if disagreement is None:
            primary_reason = "Signal missing source disagreement metadata."
        else:
            primary_reason = (
                f"Source disagreement {disagreement:.3f} exceeds allowed "
                f"{thresholds['max_disagreement']:.3f}."
            )
    elif primary_blocker == "trade_edge":
        primary_reason = (
            f"Edge {abs(_safe_float(combined_edge) or 0.0) * 100:.1f}% is below required "
            f"{float(min_trade_edge) * 100:.1f}% minimum."
        )
    elif primary_blocker == "trade_price":
        primary_reason = (
            f"Entry price {_safe_float(baseline_price) or 0.0:.3f} is below required "
            f"{float(min_trade_price):.3f} minimum."
        )
    elif primary_blocker == "sources_agree":
        primary_reason = "Forecast sources do not agree on trade direction."
    elif primary_blocker == "ev_pct":
        primary_reason = f"Signal EV {_safe_float(ev_pct) or 0.0:.2f}% is not positive."
    elif primary_blocker == "kelly_fraction":
        primary_reason = f"Signal Kelly fraction {_safe_float(kelly_fraction) or 0.0:.4f} is not positive."
    else:
        primary_reason = "Ready to open weather trade."
    return {
        "tradeable": not blocking_filters,
        "filter_status": filter_status,
        "blocking_filters": blocking_filters,
        "primary_blocker": primary_blocker,
        "primary_reason_code": primary_reason_code,
        "primary_reason": primary_reason,
        "guard_thresholds": thresholds,
        "hours_ahead": horizon_hours,
        "hours_ahead_cmp": horizon_hours_cmp,
        "min_hours_required_cmp": min_hours_cmp,
        "comparison_precision_hours": float(WEATHER_HORIZON_PRECISION_HOURS),
        "source_disagreement": _safe_float(source_disagreement),
        "baseline_price": _safe_float(baseline_price),
        "combined_edge": _safe_float(combined_edge),
        "liquidity": _safe_float(liquidity),
        "ev_pct": _safe_float(ev_pct),
        "kelly_fraction": _safe_float(kelly_fraction),
    }


def evaluate_persisted_threshold_signal(
    signal_row: dict,
    *,
    elapsed_hours=0.0,
    min_trade_edge,
    min_trade_price,
    guard: dict | None = None,
):
    source_meta = signal_row.get("source_meta") or {}
    stored_admission = source_meta.get("threshold_admission") or {}
    stored_thresholds = stored_admission.get("guard_thresholds") or {}
    current_hours = _safe_float(signal_row.get("hours_ahead"))
    if current_hours is not None:
        current_hours -= float(elapsed_hours or 0.0)
    source_disagreement = signal_row.get("source_disagreement")
    if source_disagreement is None:
        source_disagreement = stored_admission.get("source_disagreement")
    if source_disagreement is None:
        noaa_prob = _safe_float(signal_row.get("noaa_prob"))
        om_prob = _safe_float(signal_row.get("om_prob"))
        if noaa_prob is not None and om_prob is not None:
            source_disagreement = abs(noaa_prob - om_prob)
    admission = build_threshold_admission(
        sources_agree=signal_row.get("sources_agree"),
        ev_pct=signal_row.get("ev_pct"),
        kelly_fraction=signal_row.get("kelly_fraction"),
        combined_edge=signal_row.get("combined_edge"),
        baseline_price=(
            signal_row.get("market_price")
            if signal_row.get("action") == "BUY_YES"
            else 1.0 - (_safe_float(signal_row.get("market_price")) or 0.0)
        ),
        liquidity=signal_row.get("liquidity"),
        hours_ahead=current_hours,
        source_disagreement=source_disagreement,
        min_trade_edge=min_trade_edge,
        min_trade_price=min_trade_price,
        guard=guard,
    )
    stored_tradeable = bool(stored_admission.get("tradeable", signal_row.get("tradeable")))
    stored_hours_cmp = _quantize_hours(
        stored_admission.get("hours_ahead_cmp", stored_admission.get("hours_ahead", signal_row.get("hours_ahead")))
    )
    current_hours_cmp = admission.get("hours_ahead_cmp")
    guard_thresholds_changed = bool(stored_thresholds) and stored_thresholds != admission["guard_thresholds"]
    state_change_reason_code = None
    state_change_summary = None
    material_state_change = False
    if stored_tradeable and not admission["tradeable"]:
        if guard_thresholds_changed:
            material_state_change = True
            state_change_reason_code = "guard_thresholds_changed"
            state_change_summary = (
                "Weather guard thresholds changed after scan: "
                f"scan={stored_thresholds} current={admission['guard_thresholds']}."
            )
        elif (
            admission.get("primary_blocker") == "horizon"
            and stored_hours_cmp is not None
            and current_hours_cmp is not None
            and current_hours_cmp < stored_hours_cmp
        ):
            material_state_change = True
            state_change_reason_code = "horizon_aged_below_threshold"
            state_change_summary = (
                f"Signal horizon decayed from {stored_hours_cmp:.1f}h at scan to "
                f"{current_hours_cmp:.1f}h at preflight."
            )
    admission.update({
        "stored_tradeable": stored_tradeable,
        "stored_threshold_admission": stored_admission or None,
        "stored_hours_ahead_cmp": stored_hours_cmp,
        "guard_thresholds_changed": guard_thresholds_changed,
        "material_state_change": material_state_change,
        "state_change_reason_code": state_change_reason_code,
        "state_change_summary": state_change_summary,
    })
    return admission
