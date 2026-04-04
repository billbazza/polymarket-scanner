"""Manage the weather guard thresholds and failure-driven tiering."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("scanner.weather_guard_state")

_STATE_FILE = Path(__file__).resolve().parent / "reports" / "diagnostics" / "weather-guard-state.json"

_GUARD_TIERS = [
    {
        "name": "minimal",
        "min_liquidity": 0,
        "min_hours_ahead": 0,
        "max_disagreement": 1.0,
        "description": "Audit-only, unlock everything until we see a failure.",
    },
    {
        "name": "relaxed",
        "min_liquidity": 5_000,
        "min_hours_ahead": 48,
        "max_disagreement": 0.18,
        "description": "Reintroduce a modest liquidity/horizon/consensus gate.",
    },
    {
        "name": "legacy",
        "min_liquidity": 10_000,
        "min_hours_ahead": 60,
        "max_disagreement": 0.12,
        "description": "Original safety guardcodex (for audits/rollout checkpoints).",
    },
]

_DEFAULT_FAILURE_THRESHOLD = 3


def _default_state() -> dict[str, Any]:
    return {
        "tier": 0,
        "failures": 0,
        "failure_threshold": _DEFAULT_FAILURE_THRESHOLD,
        "last_failure_at": None,
        "last_tier_transition_at": None,
    }


def _ensure_state_dir() -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict[str, Any]:
    if not _STATE_FILE.exists():
        return _default_state()
    try:
        raw = json.loads(_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to read weather guard state, resetting: %s", exc)
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    state = _default_state()
    state.update({k: raw.get(k) for k in state if raw.get(k) is not None})
    try:
        state["tier"] = int(state.get("tier"))
    except (TypeError, ValueError):
        state["tier"] = 0
    try:
        state["failures"] = int(state.get("failures"))
    except (TypeError, ValueError):
        state["failures"] = 0
    try:
        state["failure_threshold"] = int(state.get("failure_threshold"))
    except (TypeError, ValueError):
        state["failure_threshold"] = _DEFAULT_FAILURE_THRESHOLD
    for key in ("last_failure_at", "last_tier_transition_at"):
        value = state.get(key)
        try:
            state[key] = float(value) if value is not None else None
        except (TypeError, ValueError):
            state[key] = None
    return state


def _write_state(state: dict[str, Any]) -> None:
    _ensure_state_dir()
    try:
        _STATE_FILE.write_text(json.dumps(state))
    except OSError as exc:
        log.warning("Failed to persist weather guard state: %s", exc)


def _guard_for_tier(tier_index: int) -> dict[str, Any]:
    idx = max(0, min(tier_index, len(_GUARD_TIERS) - 1))
    guard = dict(_GUARD_TIERS[idx])
    guard["tier_index"] = idx
    return guard


def current_guard() -> dict[str, Any]:
    state = _load_state()
    guard = _guard_for_tier(state["tier"])
    guard["failures"] = state["failures"]
    guard["failure_threshold"] = state["failure_threshold"]
    guard["last_failure_at"] = state["last_failure_at"]
    guard["last_tier_transition_at"] = state.get("last_tier_transition_at")
    guard["next_guard"] = (
        _guard_for_tier(state["tier"] + 1)
        if state["tier"] < len(_GUARD_TIERS) - 1
        else None
    )
    return guard


def register_failure(reason: str | None = None) -> None:
    state = _load_state()
    state["failures"] += 1
    state["last_failure_at"] = time.time()
    log.info(
        "Weather guard failure #%d/%d%s",
        state["failures"],
        state["failure_threshold"],
        f" ({reason})" if reason else "",
    )
    if state["failures"] >= state["failure_threshold"] and state["tier"] < len(_GUARD_TIERS) - 1:
        state["tier"] += 1
        state["failures"] = 0
        state["last_tier_transition_at"] = time.time()
        log.warning(
            "Escalating weather guard to tier %d (%s)",
            state["tier"],
            _GUARD_TIERS[state["tier"]]["name"],
        )
    _write_state(state)


def reset_to_low_guard(reason: str | None = None) -> None:
    state = _default_state()
    state["last_tier_transition_at"] = time.time()
    log.info("Resetting weather guard to minimal tier%s",
             f" ({reason})" if reason else "")
    _write_state(state)


def state_snapshot() -> dict[str, Any]:
    guard = current_guard()
    return {
        "tier": guard["tier_index"],
        "name": guard["name"],
        "failures": guard["failures"],
        "failure_threshold": guard["failure_threshold"],
        "next": guard.get("next_guard") and guard["next_guard"]["name"],
    }
