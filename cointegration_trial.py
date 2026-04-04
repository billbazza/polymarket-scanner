"""Paper-only A-grade cointegration trial guardrails and admission logic."""
import logging

import db
import math_engine

log = logging.getLogger("scanner.cointegration_trial")

TRIAL_SETTING_KEY = "cointegration_a_grade_trial"
TRIAL_NAME = "cointegration_a_grade_paper_trial"

DEFAULT_TRIAL_SETTINGS = {
    "enabled": True,
    "paper_only": True,
    "size_usd": 10.0,
    "min_z_abs": 1.6,
    "min_liquidity": 12000.0,
    "max_slippage_pct": 1.25,
    "max_half_life": 12.0,
    "min_ev_pct": 0.35,
    "allowed_failed_filters": ["ev_pass", "kelly_pass", "momentum_pass", "spread_std_pass"],
    "max_allowed_failed_filters": 2,
    "reversion_exit_z": 0.35,
    "stop_z_buffer": 0.75,
    "max_hold_hours": 36.0,
    "regime_break_z_buffer": 1.0,
}


def get_trial_settings():
    """Return validated settings for the paper A-grade cointegration trial."""
    raw = db.get_setting(TRIAL_SETTING_KEY, default=None) or {}
    settings = dict(DEFAULT_TRIAL_SETTINGS)
    settings.update(raw)
    settings["enabled"] = bool(settings.get("enabled", True))
    settings["paper_only"] = bool(settings.get("paper_only", True))
    settings["size_usd"] = max(1.0, float(settings.get("size_usd", 10.0) or 10.0))
    settings["min_z_abs"] = max(0.0, float(settings.get("min_z_abs", 1.75) or 0.0))
    settings["min_liquidity"] = max(0.0, float(settings.get("min_liquidity", 12000.0) or 0.0))
    settings["max_slippage_pct"] = max(0.1, float(settings.get("max_slippage_pct", 1.25) or 1.25))
    settings["max_half_life"] = max(0.1, float(settings.get("max_half_life", 12.0) or 12.0))
    settings["min_ev_pct"] = float(settings.get("min_ev_pct", 0.35) or 0.0)
    settings["reversion_exit_z"] = max(0.0, float(settings.get("reversion_exit_z", 0.35) or 0.35))
    settings["stop_z_buffer"] = max(0.1, float(settings.get("stop_z_buffer", 0.75) or 0.75))
    settings["max_hold_hours"] = max(1.0, float(settings.get("max_hold_hours", 36.0) or 36.0))
    settings["regime_break_z_buffer"] = max(
        settings["stop_z_buffer"],
        float(settings.get("regime_break_z_buffer", 1.0) or 1.0),
    )
    failed_filters = settings.get("allowed_failed_filters", ["ev_pass"]) or ["ev_pass"]
    settings["allowed_failed_filters"] = [str(item) for item in failed_filters]
    max_failed = settings.get("max_allowed_failed_filters", 2) or 2
    try:
        max_failed = int(float(max_failed))
    except (TypeError, ValueError):
        max_failed = 2
    settings["max_allowed_failed_filters"] = max(1, max_failed)
    settings["setting_key"] = TRIAL_SETTING_KEY
    settings["trial_name"] = TRIAL_NAME
    return settings


def set_trial_settings(settings):
    """Persist merged settings for the paper A-grade trial."""
    merged = get_trial_settings()
    merged.update(settings or {})
    merged.pop("setting_key", None)
    merged.pop("trial_name", None)
    db.set_setting(TRIAL_SETTING_KEY, merged)
    return get_trial_settings()


def _base_result(opp, settings, mode):
    grade = opp.get("grade_label") or "?"
    ev = opp.get("ev") or {}
    filters_failed = [
        name for name, passed in (opp.get("filters") or {}).items()
        if not passed
    ]
    return {
        "mode": mode,
        "grade_label": grade,
        "experiment_name": TRIAL_NAME,
        "trial_enabled": bool(settings["enabled"]),
        "admit_trade": False,
        "paper_tradeable": bool(grade == "A+"),
        "admission_path": "standard_a_plus" if grade == "A+" else "standard_reject",
        "experiment_status": "control" if grade == "A+" else "not_applicable",
        "reason_code": "a_plus_control" if grade == "A+" else "not_a_candidate",
        "reason": "Standard A+ signal." if grade == "A+" else f"Grade {grade} is outside the A-grade trial.",
        "cohort": "A+" if grade == "A+" else grade,
        "recommended_size_usd": None,
        "slippage": {},
        "guardrails": {},
        "filters_failed": filters_failed,
        "failed_filter_count": len(filters_failed),
        "ev_pct": ev.get("ev_pct"),
    }


def evaluate_signal(opp, mode="paper", settings=None):
    """Evaluate whether a signal is eligible for the A-grade paper trial."""
    settings = settings or get_trial_settings()
    result = _base_result(opp, settings, mode)
    grade = result["grade_label"]
    filters = opp.get("filters") or {}

    if grade == "A+":
        result["admit_trade"] = True
        result["paper_tradeable"] = True
        return result

    if grade != "A":
        return result

    result.update({
        "cohort": "A-trial",
        "experiment_status": "rejected",
        "reason_code": "trial_not_admitted",
        "reason": "A-grade signal did not pass paper trial guardrails.",
        "admission_path": "a_grade_rejected",
    })

    if settings["paper_only"] and mode != "paper":
        result["reason_code"] = "paper_only"
        result["reason"] = "A-grade trial is restricted to paper mode."
        return result

    if not settings["enabled"]:
        result["reason_code"] = "trial_disabled"
        result["reason"] = "A-grade paper trial is disabled."
        return result

    if not filters:
        result["reason_code"] = "missing_filters"
        result["reason"] = "Signal filters are unavailable, so trial guardrails cannot be checked."
        return result

    failed_filters = result["filters_failed"]
    allowed_failed = set(settings["allowed_failed_filters"])
    max_allowed = settings["max_allowed_failed_filters"]
    disallowed = [name for name in failed_filters if name not in allowed_failed]
    if disallowed:
        result["reason_code"] = "filter_failure_disallowed"
        result["reason"] = (
            "A-grade trial only allows misses from "
            + ", ".join(settings["allowed_failed_filters"])
            + f". This signal additionally failed {', '.join(disallowed)}."
        )
        return result
    if len(failed_filters) > max_allowed:
        result["reason_code"] = "filter_failure_limit_reached"
        result["reason"] = (
            f"A-grade trial allows up to {max_allowed} allowed misses, "
            f"but this signal failed {len(failed_filters)} filters ({', '.join(failed_filters)})."
        )
        return result

    z_abs = abs(float(opp.get("z_score") or 0.0))
    if z_abs < settings["min_z_abs"]:
        result["reason_code"] = "z_too_small"
        result["reason"] = f"|z| {z_abs:.2f} is below trial minimum {settings['min_z_abs']:.2f}."
        return result

    half_life = float(opp.get("half_life") or 0.0)
    if half_life <= 0 or half_life > settings["max_half_life"]:
        result["reason_code"] = "half_life_too_slow"
        result["reason"] = f"Half-life {half_life:.1f} exceeds trial cap {settings['max_half_life']:.1f}."
        return result

    liquidity = float(opp.get("liquidity") or 0.0)
    if liquidity < settings["min_liquidity"]:
        result["reason_code"] = "liquidity_too_low"
        result["reason"] = (
            f"Liquidity ${liquidity:,.0f} is below trial minimum ${settings['min_liquidity']:,.0f}."
        )
        return result

    ev_pct = float((opp.get("ev") or {}).get("ev_pct") or 0.0)
    if ev_pct < settings["min_ev_pct"]:
        result["reason_code"] = "ev_too_low"
        result["reason"] = f"EV {ev_pct:.2f}% is below trial minimum {settings['min_ev_pct']:.2f}%."
        return result

    token_a = opp.get("token_id_a")
    token_b = opp.get("token_id_b")
    if not token_a or not token_b:
        result["reason_code"] = "missing_token"
        result["reason"] = "Signal is missing token IDs needed for slippage checks."
        return result

    per_leg_size = settings["size_usd"] / 2
    slippage_a = math_engine.check_slippage(
        token_a,
        trade_size_usd=per_leg_size,
        max_slippage_pct=settings["max_slippage_pct"],
    )
    slippage_b = math_engine.check_slippage(
        token_b,
        trade_size_usd=per_leg_size,
        max_slippage_pct=settings["max_slippage_pct"],
    )
    result["slippage"] = {"leg_a": slippage_a, "leg_b": slippage_b}

    if not slippage_a.get("ok"):
        result["reason_code"] = "slippage_leg_a"
        result["reason"] = f"Leg A rejected by slippage guardrail: {slippage_a.get('reason')}"
        return result

    if not slippage_b.get("ok"):
        result["reason_code"] = "slippage_leg_b"
        result["reason"] = f"Leg B rejected by slippage guardrail: {slippage_b.get('reason')}"
        return result

    guardrails = {
        "size_usd": round(settings["size_usd"], 2),
        "reversion_exit_z": settings["reversion_exit_z"],
        "stop_z_threshold": round(z_abs + settings["stop_z_buffer"], 4),
        "max_hold_hours": settings["max_hold_hours"],
        "regime_break_threshold": round(z_abs + settings["regime_break_z_buffer"], 4),
        "max_slippage_pct": settings["max_slippage_pct"],
        "min_liquidity": settings["min_liquidity"],
        "max_allowed_failed_filters": settings["max_allowed_failed_filters"],
        "allowed_failed_filters": settings["allowed_failed_filters"],
    }
    result.update({
        "admit_trade": True,
        "paper_tradeable": True,
        "admission_path": "paper_a_trial",
        "experiment_status": "eligible",
        "reason_code": "trial_eligible",
        "reason": (
            "A-grade signal admitted to the paper trial with smaller size, "
            "tighter slippage, and explicit stop/hold guardrails."
        ),
        "recommended_size_usd": round(settings["size_usd"], 2),
        "guardrails": guardrails,
    })
    return result


def annotate_opportunity(opp, mode="paper", settings=None):
    """Attach trial-admission metadata to an opportunity dict in place."""
    evaluation = evaluate_signal(opp, mode=mode, settings=settings)
    opp["paper_tradeable"] = evaluation["paper_tradeable"]
    opp["admission_path"] = evaluation["admission_path"]
    opp["experiment_name"] = evaluation["experiment_name"]
    opp["experiment_status"] = evaluation["experiment_status"]
    opp["experiment_reason_code"] = evaluation["reason_code"]
    opp["experiment_reason"] = evaluation["reason"]
    opp["experiment_guardrails"] = evaluation["guardrails"]
    opp["trial_slippage"] = evaluation["slippage"]
    opp["trial_recommended_size_usd"] = evaluation["recommended_size_usd"]
    opp["trial_filters_failed"] = evaluation["filters_failed"]
    opp["trial_failed_filter_count"] = evaluation.get("failed_filter_count")
    return evaluation
