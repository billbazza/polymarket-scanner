"""A-grade cointegration trial guardrails and runtime-parity admission logic."""
import logging

import db
import math_engine

log = logging.getLogger("scanner.cointegration_trial")

TRIAL_SETTING_KEY = "cointegration_a_grade_trial"
TRIAL_NAME = "cointegration_a_grade_parity_trial"

DEFAULT_TRIAL_SETTINGS = {
    "enabled": True,
    "paper_only": False,
    "size_usd": 10.0,
    "min_z_abs": 1.45,
    "min_liquidity": 6000.0,
    "max_slippage_pct": 2.0,
    "max_half_life": 12.0,
    "min_ev_pct": 0.25,
    "grade_weight_min": 0.25,
    "grade_weight_max": 0.65,
    "max_allowed_failed_filters": 1,
    "allowed_failed_filters": [
        "momentum_pass",
        "spread_std_pass",
    ],
    "reversion_exit_z": 0.35,
    "stop_z_buffer": 0.75,
    "max_hold_hours": 36.0,
    "regime_break_z_buffer": 1.0,
}


def get_trial_settings():
    """Return validated settings for the A-grade cointegration trial."""
    raw = db.get_setting(TRIAL_SETTING_KEY, default=None) or {}
    settings = dict(DEFAULT_TRIAL_SETTINGS)
    settings.update(raw)
    settings["enabled"] = bool(settings.get("enabled", True))
    # `paper_only` is retained for backward-compatible API/settings payloads but is
    # now a no-op: penny/book must see the same A-grade admission lane as paper.
    settings["paper_only"] = False
    settings["size_usd"] = max(1.0, float(settings.get("size_usd", 10.0) or 10.0))
    settings["min_z_abs"] = max(0.0, float(settings.get("min_z_abs", 1.6) or 0.0))
    settings["min_liquidity"] = max(
        0.0, float(settings.get("min_liquidity", 10000.0) or 0.0)
    )
    settings["max_slippage_pct"] = max(
        0.1, float(settings.get("max_slippage_pct", 1.25) or 1.25)
    )
    settings["max_half_life"] = max(
        0.1, float(settings.get("max_half_life", 12.0) or 12.0)
    )
    settings["min_ev_pct"] = float(settings.get("min_ev_pct", 0.25) or 0.0)
    raw_max = settings.get("max_allowed_failed_filters", 2) or 2
    try:
        raw_max = int(float(raw_max))
    except (TypeError, ValueError):
        raw_max = 2
    settings["max_allowed_failed_filters"] = max(1, raw_max)
    settings["reversion_exit_z"] = max(
        0.0, float(settings.get("reversion_exit_z", 0.35) or 0.35)
    )
    settings["stop_z_buffer"] = max(
        0.1, float(settings.get("stop_z_buffer", 0.75) or 0.75)
    )
    settings["max_hold_hours"] = max(
        1.0, float(settings.get("max_hold_hours", 36.0) or 36.0)
    )
    settings["regime_break_z_buffer"] = max(
        settings["stop_z_buffer"],
        float(settings.get("regime_break_z_buffer", 1.0) or 1.0),
    )
    failed_filters = settings.get("allowed_failed_filters", ["ev_pass"]) or ["ev_pass"]
    settings["allowed_failed_filters"] = [str(item) for item in failed_filters]
    settings["setting_key"] = TRIAL_SETTING_KEY
    settings["trial_name"] = TRIAL_NAME
    min_weight = max(0.0, min(1.0, float(settings.get("grade_weight_min", 0.25) or 0.25)))
    max_weight = max(
        min_weight,
        min(1.0, float(settings.get("grade_weight_max", 0.65) or min_weight)),
    )
    settings["grade_weight_min"] = min_weight
    settings["grade_weight_max"] = max_weight
    return settings


def set_trial_settings(settings):
    """Persist merged settings for the A-grade cointegration trial."""
    merged = get_trial_settings()
    merged.update(settings or {})
    merged.pop("setting_key", None)
    merged.pop("trial_name", None)
    db.set_setting(TRIAL_SETTING_KEY, merged)
    return get_trial_settings()


def _base_result(opp, settings, mode):
    grade = opp.get("grade_label") or "?"
    ev = opp.get("ev") or {}
    filters = opp.get("filters") or {}
    filters_failed = [name for name, passed in filters.items() if not passed]
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
        "grade_weight": None,
        "slippage": {},
        "guardrails": {},
        "filters_failed": filters_failed,
        "failed_filter_count": len(filters_failed),
        "blocker_context": None,
        "ev_pct": ev.get("ev_pct"),
    }


def _grade_weight_for_signal(grade_value, settings):
    """Return a normalized weight for A-grade entries based on the raw grade score."""
    try:
        normalized = float(grade_value or 0.0) / 8.0
    except (TypeError, ValueError):
        normalized = 0.0
    normalized = max(0.0, min(1.0, normalized))
    min_weight = settings.get("grade_weight_min", 0.25)
    max_weight = settings.get("grade_weight_max", 0.65)
    return max(min_weight, min(max_weight, normalized))


def evaluate_signal(opp, mode="paper", settings=None):
    """Evaluate whether a signal is eligible for the A-grade cointegration trial."""
    settings = settings or get_trial_settings()
    result = _base_result(opp, settings, mode)
    grade = result["grade_label"]
    filters = opp.get("filters") or {}
    failed_filters = result["filters_failed"]

    def _block(code, message, context=None):
        ctx = dict(context or {})
        ctx.setdefault("filters_failed", list(failed_filters))
        ctx.setdefault("failed_filter_count", len(failed_filters))
        result["blocker_context"] = ctx
        result["reason_code"] = code
        result["reason"] = message
        return result

    if grade == "A+":
        result["admit_trade"] = True
        result["paper_tradeable"] = True
        result["grade_weight"] = 1.0
        return result

    if grade != "A":
        return result

    result.update({
        "cohort": "A-trial",
        "experiment_status": "rejected",
        "reason_code": "trial_not_admitted",
        "reason": "A-grade signal did not pass A-trial guardrails.",
        "admission_path": "a_grade_rejected",
    })

    if not settings["enabled"]:
        return _block(
            "trial_disabled",
            "A-grade cointegration trial is disabled.",
            {"type": "mode"},
        )

    if not filters:
        return _block(
            "missing_filters",
            "Signal filters are unavailable, so trial guardrails cannot be checked.",
            {"type": "metadata"},
        )

    allowed_failed = set(settings["allowed_failed_filters"])
    max_allowed = settings["max_allowed_failed_filters"]
    allowed_list = ", ".join(settings["allowed_failed_filters"])

    if not failed_filters:
        return _block(
            "filter_failure_outside_trial",
            "A-grade trial saw no recorded filter failures; cannot evaluate near-misses.",
            {
                "type": "filter",
                "allowed_failed_filters": settings["allowed_failed_filters"],
                "max_allowed_failed_filters": max_allowed,
            },
        )

    if len(failed_filters) > max_allowed:
        return _block(
            "too_many_filter_failures",
            (
                f"A-trial accepts at most {max_allowed} soft miss(es) ({allowed_list}); actual failures: {', '.join(failed_filters)}."
            ),
            {
                "type": "filter",
                "allowed_failed_filters": settings["allowed_failed_filters"],
                "max_allowed_failed_filters": max_allowed,
            },
        )

    disallowed = [name for name in failed_filters if name not in allowed_failed]
    if disallowed:
        return _block(
            "filter_failure_outside_trial",
            (
                f"A-trial only tolerates soft misses in {allowed_list}; disallowed failures: {', '.join(disallowed)}."
            ),
            {
                "type": "filter",
                "allowed_failed_filters": settings["allowed_failed_filters"],
                "max_allowed_failed_filters": max_allowed,
                "disallowed_filters": disallowed,
            },
        )

    z_abs = abs(float(opp.get("z_score") or 0.0))
    if z_abs < settings["min_z_abs"]:
        return _block(
            "z_too_small",
            f"|z| {z_abs:.2f} is below trial minimum {settings['min_z_abs']:.2f}.",
            {
                "type": "threshold",
                "field": "z_score",
                "current": z_abs,
                "required": settings["min_z_abs"],
            },
        )

    half_life = float(opp.get("half_life") or 0.0)
    if half_life <= 0 or half_life > settings["max_half_life"]:
        return _block(
            "half_life_too_slow",
            f"Half-life {half_life:.1f} exceeds trial cap {settings['max_half_life']:.1f}.",
            {
                "type": "threshold",
                "field": "half_life",
                "current": half_life,
                "max_allowed": settings["max_half_life"],
            },
        )

    liquidity = float(opp.get("liquidity") or 0.0)
    if liquidity < settings["min_liquidity"]:
        return _block(
            "liquidity_too_low",
            (
                f"Liquidity ${liquidity:,.0f} is below trial minimum ${settings['min_liquidity']:,.0f}."
            ),
            {
                "type": "threshold",
                "field": "liquidity",
                "current": liquidity,
                "required": settings["min_liquidity"],
            },
        )

    ev_pct = float((opp.get("ev") or {}).get("ev_pct") or 0.0)
    if ev_pct < settings["min_ev_pct"]:
        return _block(
            "ev_too_low",
            f"EV {ev_pct:.2f}% is below trial minimum {settings['min_ev_pct']:.2f}%.",
            {
                "type": "threshold",
                "field": "ev_pct",
                "current": ev_pct,
                "required": settings["min_ev_pct"],
            },
        )

    token_a = opp.get("token_id_a")
    token_b = opp.get("token_id_b")
    if not token_a or not token_b:
        missing = [key for key in ("token_id_a", "token_id_b") if not opp.get(key)]
        return _block(
            "missing_token",
            "Signal is missing token IDs needed for slippage checks.",
            {
                "type": "metadata",
                "missing_tokens": missing,
            },
        )

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
        return _block(
            "slippage_leg_a",
            f"Leg A rejected by slippage guardrail: {slippage_a.get('reason')}",
            {
                "type": "slippage",
                "leg": "A",
                "slippage": slippage_a,
                "max_allowed_slippage_pct": settings["max_slippage_pct"],
            },
        )

    if not slippage_b.get("ok"):
        return _block(
            "slippage_leg_b",
            f"Leg B rejected by slippage guardrail: {slippage_b.get('reason')}",
            {
                "type": "slippage",
                "leg": "B",
                "slippage": slippage_b,
                "max_allowed_slippage_pct": settings["max_slippage_pct"],
            },
        )

    weight_base = _grade_weight_for_signal(float(opp.get("grade") or 0), settings)
    weighted_size_usd = round(settings["size_usd"] * weight_base, 2)
    guardrails = {
        "size_usd": round(settings["size_usd"], 2),
        "weighted_entry_size_usd": weighted_size_usd,
        "grade_weight": round(weight_base, 3),
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
        "admission_path": "a_grade_trial",
        "experiment_status": "eligible",
        "reason_code": "trial_eligible",
        "reason": (
            "A-grade signal admitted to the runtime-parity A-trial with weighted high-risk entries "
            f"(${weighted_size_usd:.2f}, weight {weight_base:.2f}) and explicit stop/hold guardrails."
        ),
        "recommended_size_usd": weighted_size_usd,
        "guardrails": guardrails,
        "grade_weight": weight_base,
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
    opp["experiment_grade_weight"] = evaluation.get("grade_weight")
    opp["trial_slippage"] = evaluation["slippage"]
    opp["trial_recommended_size_usd"] = evaluation["recommended_size_usd"]
    opp["filters_failed"] = evaluation["filters_failed"]
    opp["failed_filter_count"] = evaluation["failed_filter_count"]
    opp["blocker_context"] = evaluation["blocker_context"]
    return evaluation
