"""Paper-only sizing framework for shadow comparison and guarded rollout."""
import copy
import logging
from pathlib import Path

import db

log = logging.getLogger("scanner.paper_sizing")

SETTING_KEY = "paper_sizing_framework"
DEFAULT_REVIEW_NOTE = "reviews/2026-04-02-paper-sizing-rollout-review.md"

DEFAULT_SETTINGS = {
    "enabled": True,
    "paper_only": True,
    "rollout_state": "shadow",
    "active_policy": "fixed",
    "review_note_path": DEFAULT_REVIEW_NOTE,
    "rollback": {
        "default_policy": "fixed",
        "disable_confidence_policy": True,
    },
    "constraints": {
        "max_total_bankroll_utilization_pct": 35.0,
        "round_to_usd": 1.0,
    },
    "strategies": {
        "cointegration": {
            "fixed_size_usd": 20.0,
            "min_size_usd": 10.0,
            "max_size_usd": 30.0,
            "max_strategy_bankroll_utilization_pct": 20.0,
            "max_trade_bankroll_utilization_pct": 2.5,
            "score_weights": {
                "ev_pct": 0.35,
                "kelly_fraction": 0.25,
                "liquidity": 0.20,
                "grade": 0.20,
            },
            "score_targets": {
                "ev_pct": 4.0,
                "kelly_fraction": 0.10,
                "liquidity": 25000.0,
            },
        },
        "weather": {
            "fixed_size_usd": 20.0,
            "min_size_usd": 10.0,
            "max_size_usd": 35.0,
            "max_strategy_bankroll_utilization_pct": 15.0,
            "max_trade_bankroll_utilization_pct": 2.5,
            "score_weights": {
                "combined_edge_pct": 0.45,
                "kelly_fraction": 0.20,
                "sources_agree": 0.20,
                "liquidity": 0.15,
            },
            "score_targets": {
                "combined_edge_pct": 20.0,
                "kelly_fraction": 0.12,
                "liquidity": 1500.0,
            },
        },
    },
}


def _deep_merge(base: dict, updates: dict | None) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _round_size(value: float, round_to: float) -> float:
    round_to = max(0.01, float(round_to or 1.0))
    return round(round(float(value) / round_to) * round_to, 2)


def _normalize_strategy_settings(name: str, settings: dict) -> dict:
    strategy = settings["strategies"][name]
    strategy["fixed_size_usd"] = max(1.0, float(strategy.get("fixed_size_usd", 20.0) or 20.0))
    strategy["min_size_usd"] = max(1.0, float(strategy.get("min_size_usd", 10.0) or 10.0))
    strategy["max_size_usd"] = max(strategy["min_size_usd"], float(strategy.get("max_size_usd", strategy["fixed_size_usd"]) or strategy["fixed_size_usd"]))
    strategy["max_strategy_bankroll_utilization_pct"] = max(
        strategy["max_trade_bankroll_utilization_pct"],
        float(strategy.get("max_strategy_bankroll_utilization_pct", 10.0) or 10.0),
    )
    strategy["max_trade_bankroll_utilization_pct"] = max(
        0.1,
        float(strategy.get("max_trade_bankroll_utilization_pct", 2.0) or 2.0),
    )
    return strategy


def get_sizing_settings() -> dict:
    raw = db.get_setting(SETTING_KEY, default=None) or {}
    settings = _deep_merge(DEFAULT_SETTINGS, raw)
    settings["enabled"] = bool(settings.get("enabled", True))
    settings["paper_only"] = bool(settings.get("paper_only", True))
    settings["rollout_state"] = str(settings.get("rollout_state", "shadow") or "shadow")
    settings["active_policy"] = str(settings.get("active_policy", "fixed") or "fixed")
    settings["review_note_path"] = str(settings.get("review_note_path", DEFAULT_REVIEW_NOTE) or DEFAULT_REVIEW_NOTE)
    settings["constraints"]["max_total_bankroll_utilization_pct"] = max(
        1.0,
        float(settings["constraints"].get("max_total_bankroll_utilization_pct", 35.0) or 35.0),
    )
    settings["constraints"]["round_to_usd"] = max(
        0.01,
        float(settings["constraints"].get("round_to_usd", 1.0) or 1.0),
    )
    for name in list(settings.get("strategies", {})):
        _normalize_strategy_settings(name, settings)
    settings["review_note_exists"] = Path(settings["review_note_path"]).exists()
    settings["setting_key"] = SETTING_KEY
    return settings


def set_sizing_settings(updates: dict | None) -> dict:
    merged = _deep_merge(get_sizing_settings(), updates or {})
    merged.pop("review_note_exists", None)
    merged.pop("setting_key", None)
    db.set_setting(SETTING_KEY, merged)
    return get_sizing_settings()


def _extract_strategy_bucket(account_overview: dict, strategy: str) -> dict:
    strategies = ((account_overview.get("strategy_breakdown") or {}).get("strategies") or [])
    for item in strategies:
        if item.get("strategy") == strategy:
            return item
    return {
        "strategy": strategy,
        "committed_capital": 0.0,
        "bankroll_utilization_pct": 0.0,
    }


def _cointegration_score(opportunity: dict, strategy_settings: dict) -> dict:
    ev_pct = float((opportunity.get("ev") or {}).get("ev_pct") or 0.0)
    kelly_fraction = float((opportunity.get("sizing") or {}).get("kelly_fraction") or 0.0)
    liquidity = float(opportunity.get("liquidity") or 0.0)
    grade = (opportunity.get("grade_label") or "").upper()

    targets = strategy_settings["score_targets"]
    weights = strategy_settings["score_weights"]
    normalized = {
        "ev_pct": _clamp(ev_pct / max(0.01, float(targets["ev_pct"]))),
        "kelly_fraction": _clamp(kelly_fraction / max(0.001, float(targets["kelly_fraction"]))),
        "liquidity": _clamp(liquidity / max(1.0, float(targets["liquidity"]))),
        "grade": 1.0 if grade == "A+" else 0.55 if grade == "A" else 0.25,
    }
    score = sum(normalized[name] * float(weights.get(name, 0.0)) for name in weights)
    return {
        "score": round(_clamp(score), 4),
        "inputs": {
            "ev_pct": ev_pct,
            "kelly_fraction": kelly_fraction,
            "liquidity": liquidity,
            "grade_label": grade or "?",
        },
        "normalized": normalized,
    }


def _weather_score(opportunity: dict, strategy_settings: dict) -> dict:
    edge_pct = float(opportunity.get("combined_edge_pct") or 0.0)
    kelly_fraction = float(opportunity.get("kelly_fraction") or 0.0)
    liquidity = float(opportunity.get("liquidity") or 0.0)
    sources_agree = 1.0 if opportunity.get("sources_agree") else 0.0
    sources_available = int(opportunity.get("sources_available") or 0)

    targets = strategy_settings["score_targets"]
    weights = strategy_settings["score_weights"]
    normalized = {
        "combined_edge_pct": _clamp(edge_pct / max(0.01, float(targets["combined_edge_pct"]))),
        "kelly_fraction": _clamp(kelly_fraction / max(0.001, float(targets["kelly_fraction"]))),
        "sources_agree": 1.0 if sources_available >= 2 and sources_agree else 0.5 if sources_available >= 1 else 0.0,
        "liquidity": _clamp(liquidity / max(1.0, float(targets["liquidity"]))),
    }
    score = sum(normalized[name] * float(weights.get(name, 0.0)) for name in weights)
    return {
        "score": round(_clamp(score), 4),
        "inputs": {
            "combined_edge_pct": edge_pct,
            "kelly_fraction": kelly_fraction,
            "liquidity": liquidity,
            "sources_available": sources_available,
            "sources_agree": bool(opportunity.get("sources_agree")),
        },
        "normalized": normalized,
    }


def _score_strategy(strategy: str, opportunity: dict, strategy_settings: dict) -> dict:
    if strategy == "weather":
        return _weather_score(opportunity, strategy_settings)
    return _cointegration_score(opportunity, strategy_settings)


def build_paper_sizing_decision(
    strategy: str,
    opportunity: dict,
    *,
    baseline_size_usd: float | None = None,
    account_overview: dict | None = None,
    settings: dict | None = None,
    mode: str = "paper",
    source: str = "autonomy",
    signal_id: int | None = None,
    weather_signal_id: int | None = None,
) -> dict:
    settings = settings or get_sizing_settings()
    account_overview = account_overview or db.get_paper_account_overview(refresh_unrealized=False)
    strategy_settings = settings["strategies"].get(strategy, {}) or {
        "fixed_size_usd": baseline_size_usd or 20.0,
        "min_size_usd": baseline_size_usd or 20.0,
        "max_size_usd": baseline_size_usd or 20.0,
        "max_strategy_bankroll_utilization_pct": 100.0,
        "max_trade_bankroll_utilization_pct": 100.0,
        "score_weights": {},
        "score_targets": {},
    }
    if strategy in settings["strategies"]:
        strategy_settings = _normalize_strategy_settings(strategy, settings)

    baseline_size = round(float(baseline_size_usd if baseline_size_usd is not None else strategy_settings["fixed_size_usd"]), 2)
    score = _score_strategy(strategy, opportunity, strategy_settings)
    score_value = float(score["score"])
    confidence_size = strategy_settings["min_size_usd"] + (
        (strategy_settings["max_size_usd"] - strategy_settings["min_size_usd"]) * score_value
    )
    confidence_size = _round_size(confidence_size, settings["constraints"]["round_to_usd"])

    starting_bankroll = float(account_overview.get("starting_bankroll") or 0.0)
    available_cash = float(account_overview.get("available_cash") or 0.0)
    committed_capital = float(account_overview.get("committed_capital") or 0.0)
    total_equity = float(account_overview.get("total_equity") or 0.0)
    current_total_util = float(account_overview.get("bankroll_used_pct") or 0.0)
    strategy_bucket = _extract_strategy_bucket(account_overview, strategy)
    current_strategy_util = float(strategy_bucket.get("bankroll_utilization_pct") or 0.0)

    max_trade_size = starting_bankroll * (strategy_settings["max_trade_bankroll_utilization_pct"] / 100.0) if starting_bankroll > 0 else confidence_size
    max_total_room = max(
        0.0,
        (starting_bankroll * (settings["constraints"]["max_total_bankroll_utilization_pct"] / 100.0)) - committed_capital,
    ) if starting_bankroll > 0 else available_cash
    max_strategy_room = max(
        0.0,
        (starting_bankroll * (strategy_settings["max_strategy_bankroll_utilization_pct"] / 100.0)) - float(strategy_bucket.get("committed_capital") or 0.0),
    ) if starting_bankroll > 0 else available_cash

    capped_confidence_size = min(confidence_size, max_trade_size, max_total_room, max_strategy_room, available_cash)
    capped_confidence_size = _round_size(capped_confidence_size, settings["constraints"]["round_to_usd"])

    constraints = {
        "max_trade_size_usd": round(max_trade_size, 2),
        "max_total_room_usd": round(max_total_room, 2),
        "max_strategy_room_usd": round(max_strategy_room, 2),
        "available_cash_usd": round(available_cash, 2),
        "binding_caps": [
            name for name, value in (
                ("trade_cap", max_trade_size),
                ("total_utilization_cap", max_total_room),
                ("strategy_utilization_cap", max_strategy_room),
                ("available_cash", available_cash),
            )
            if round(capped_confidence_size, 2) == round(_round_size(min(confidence_size, value), settings["constraints"]["round_to_usd"]), 2)
            and confidence_size > value + 1e-9
        ],
    }

    selected_policy = settings["active_policy"]
    if mode != "paper" and settings["paper_only"]:
        selected_policy = settings["rollback"]["default_policy"]
    if not settings["enabled"]:
        selected_policy = settings["rollback"]["default_policy"]
    if settings["rollout_state"] == "shadow":
        selected_policy = "fixed"

    selected_size = baseline_size if selected_policy == "fixed" else capped_confidence_size
    projected_total_util = (
        ((committed_capital + selected_size) / starting_bankroll * 100.0)
        if starting_bankroll > 0 else 0.0
    )
    projected_strategy_util = (
        ((float(strategy_bucket.get("committed_capital") or 0.0) + selected_size) / starting_bankroll * 100.0)
        if starting_bankroll > 0 else 0.0
    )

    return {
        "source": source,
        "strategy": strategy,
        "mode": mode,
        "enabled": settings["enabled"],
        "paper_only": settings["paper_only"],
        "rollout_state": settings["rollout_state"],
        "active_policy": settings["active_policy"],
        "selected_policy": selected_policy,
        "applied": bool(selected_policy == "confidence_aware" and mode == "paper"),
        "signal_id": signal_id,
        "weather_signal_id": weather_signal_id,
        "event": opportunity.get("event") or opportunity.get("market"),
        "baseline_size_usd": baseline_size,
        "confidence_size_usd": round(capped_confidence_size, 2),
        "selected_size_usd": round(selected_size, 2),
        "confidence_score": round(score_value, 4),
        "confidence_inputs": score["inputs"],
        "confidence_components": score["normalized"],
        "account_snapshot": {
            "starting_bankroll": round(starting_bankroll, 2),
            "available_cash": round(available_cash, 2),
            "committed_capital": round(committed_capital, 2),
            "total_equity": round(total_equity, 2),
        },
        "utilization": {
            "current_total_pct": round(current_total_util, 2),
            "projected_total_pct": round(projected_total_util, 2),
            "current_strategy_pct": round(current_strategy_util, 2),
            "projected_strategy_pct": round(projected_strategy_util, 2),
        },
        "constraints": constraints,
        "review_note_path": settings["review_note_path"],
        "review_note_exists": bool(settings.get("review_note_exists")),
        "rollback_policy": settings["rollback"]["default_policy"],
        "compare_only": settings["rollout_state"] == "shadow",
    }


def record_sizing_decision(decision: dict) -> int:
    if not decision:
        return 0
    try:
        return db.record_paper_sizing_decision(
            source=decision.get("source") or "autonomy",
            strategy=decision.get("strategy") or "unknown",
            mode=decision.get("mode") or "paper",
            rollout_state=decision.get("rollout_state"),
            active_policy=decision.get("active_policy"),
            selected_policy=decision.get("selected_policy"),
            applied=bool(decision.get("applied")),
            signal_id=decision.get("signal_id"),
            weather_signal_id=decision.get("weather_signal_id"),
            trade_id=decision.get("trade_id"),
            event=decision.get("event"),
            baseline_size_usd=decision.get("baseline_size_usd"),
            confidence_size_usd=decision.get("confidence_size_usd"),
            selected_size_usd=decision.get("selected_size_usd"),
            confidence_score=decision.get("confidence_score"),
            available_cash=((decision.get("account_snapshot") or {}).get("available_cash")),
            committed_capital=((decision.get("account_snapshot") or {}).get("committed_capital")),
            total_equity=((decision.get("account_snapshot") or {}).get("total_equity")),
            current_total_utilization_pct=((decision.get("utilization") or {}).get("current_total_pct")),
            projected_total_utilization_pct=((decision.get("utilization") or {}).get("projected_total_pct")),
            current_strategy_utilization_pct=((decision.get("utilization") or {}).get("current_strategy_pct")),
            projected_strategy_utilization_pct=((decision.get("utilization") or {}).get("projected_strategy_pct")),
            constraints=decision.get("constraints"),
            details={
                "confidence_inputs": decision.get("confidence_inputs"),
                "confidence_components": decision.get("confidence_components"),
                "review_note_path": decision.get("review_note_path"),
                "compare_only": decision.get("compare_only"),
                "rollback_policy": decision.get("rollback_policy"),
            },
        )
    except Exception as exc:
        log.warning("Paper sizing decision logging failed: %s", exc)
        return 0
