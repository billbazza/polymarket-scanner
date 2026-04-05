#!/usr/bin/env python3
from __future__ import annotations

"""Autonomous trading engine — scans, trades, monitors, learns.

Three levels of autonomy:
    Level 0 (SCOUT):  Scan only, no trades. Default.
    Level 1 (PAPER):  Auto paper-trade A+ signals, auto-close on reversion.
    Level 2 (PENNY):  Real trades, $1-5 per position. Needs POLYMARKET_PRIVATE_KEY.
    Level 3 (BOOK):   Real trades, Kelly-sized from bankroll. Manual promotion only.

Each level graduates to the next by meeting confidence criteria over a
minimum sample of trades. The system never self-promotes to live money —
that requires human confirmation.

Usage:
    python3 autonomy.py                  # run at current level
    python3 autonomy.py --level paper    # force paper trading level
    python3 autonomy.py --status         # show performance & graduation readiness
    python3 autonomy.py --promote        # promote to next level (with confirmation)
    python3 autonomy.py --journal        # show recent decisions and reasoning

Called by LaunchAgent every 30 minutes alongside the scan.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import runtime_config

sys.path.insert(0, str(Path(__file__).parent))

from log_setup import init_logging
init_logging()
log = logging.getLogger("scanner.autonomy")
runtime_config.log_runtime_status("autonomy.py")

import asyncio
import db
import scanner
import async_scanner
import tracker
import execution
import math_engine
import cointegration_trial
import paper_sizing
import trade_monitor
import perplexity
import journal_writer

# --- Configuration ---

STATE_DIR = Path(__file__).parent / "logs"
STATE_FILE = STATE_DIR / "autonomy_state.json"
LEGACY_STATE_FILE = Path(__file__).parent / "autonomy_state.json"
JOURNAL_FILE = Path(__file__).parent / "logs" / "journal.jsonl"
BACKGROUND_SCOPES_ENV = "AUTONOMY_BACKGROUND_SCOPES"

RUNTIME_SCOPE_PAPER = "paper"
RUNTIME_SCOPE_PENNY = "penny"

LEVELS = {
    "scout": {
        "name": "Scout",
        "description": "Scan only, no trades",
        "can_trade": False,
        "size_usd": 0,
        "max_open": 0,
    },
    "paper": {
        "name": "Paper Trader",
        "description": "Auto paper-trade A+ signals",
        "can_trade": True,
        "size_usd": 20,
        "max_open": None,
        "graduation": {
            "min_trades": 50,
            "min_win_rate": 55.0,
            "min_total_pnl": 0,       # must be profitable
            "min_sharpe": 0.5,
        },
    },
    "penny": {
        "name": "Penny Trader",
        "description": "Real trades, $1-5 per position",
        "can_trade": True,
        "size_usd": 3,
        "max_open": 3,
        "graduation": {
            "min_trades": 30,
            "min_win_rate": 50.0,
            "min_total_pnl": 0,
            "min_sharpe": 1.0,
        },
    },
    "book": {
        "name": "Book Trader",
        "description": "Kelly-sized from bankroll",
        "can_trade": True,
        "size_usd": None,  # Kelly-determined
        "max_open": 10,
        "bankroll": 1000,
        "graduation": None,  # top level
    },
}


def get_level_config(level: str | None, runtime_scope: str | None = None) -> dict:
    level_key = str(level or "").strip().lower()
    config = dict(LEVELS.get(level_key, LEVELS["scout"]))
    scope = normalize_runtime_scope(runtime_scope or runtime_scope_for_level(level_key))
    controls = db.get_autonomy_runtime_settings(scope)
    if controls.get("max_open_override") is not None:
        config["max_open"] = controls["max_open_override"]
    if controls.get("size_usd_override") is not None and config.get("size_usd") is not None:
        config["size_usd"] = controls["size_usd_override"]
    config["runtime_controls"] = controls
    config["auto_trade_enabled"] = bool(controls.get("auto_trade_enabled", config.get("can_trade", False)))
    return config


# --- State Management ---

def normalize_runtime_scope(runtime_scope: str | None) -> str:
    if runtime_scope == RUNTIME_SCOPE_PENNY:
        return RUNTIME_SCOPE_PENNY
    return RUNTIME_SCOPE_PAPER


def runtime_scope_for_level(level: str | None) -> str:
    if level in {"penny", "book"}:
        return RUNTIME_SCOPE_PENNY
    return RUNTIME_SCOPE_PAPER


def state_file_for_scope(runtime_scope: str | None) -> Path:
    scope = normalize_runtime_scope(runtime_scope)
    return STATE_DIR / f"autonomy_state.{scope}.json"


def runtime_label(runtime_scope: str | None) -> str:
    return f"autonomy:{normalize_runtime_scope(runtime_scope)}"


def background_runtime_scopes(raw_value: str | None = None) -> list[str]:
    """Return the explicitly configured unattended autonomy scopes.

    Default behavior remains paper-only so existing launchd setups keep working,
    but concurrent paper+penny execution now requires explicit configuration.
    """
    raw = raw_value if raw_value is not None else runtime_config.get_raw(BACKGROUND_SCOPES_ENV)
    if raw is None or not str(raw).strip():
        return [RUNTIME_SCOPE_PAPER]

    normalized_raw = str(raw).strip().lower()
    if normalized_raw in {"0", "off", "false", "disabled", "none"}:
        return []

    scopes = []
    seen = set()
    for token in str(raw).split(","):
        candidate = str(token).strip().lower()
        if candidate not in {RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY}:
            log.warning(
                "Ignoring unsupported autonomy background scope '%s' from %s=%s",
                candidate,
                BACKGROUND_SCOPES_ENV,
                raw,
            )
            continue
        if candidate in seen:
            continue
        scopes.append(candidate)
        seen.add(candidate)
    return scopes or [RUNTIME_SCOPE_PAPER]


def paper_only_runtime(runtime_scope: str | None) -> bool:
    return normalize_runtime_scope(runtime_scope) == RUNTIME_SCOPE_PAPER


def weather_phase_policy(runtime_scope: str | None, runtime_controls: dict | None = None) -> dict:
    scope = normalize_runtime_scope(runtime_scope)
    controls = runtime_controls or db.get_autonomy_runtime_settings(scope)
    # Weather now follows the runtime's primary auto-trade switch in every scope.
    weather_auto_trade_enabled = bool(controls.get("auto_trade_enabled", scope == RUNTIME_SCOPE_PAPER))
    if scope == RUNTIME_SCOPE_PAPER:
        return {
            "scan_enabled": True,
            "auto_trade_enabled": weather_auto_trade_enabled,
            "execution_mode": "paper-auto-trade" if weather_auto_trade_enabled else "scan-only",
            "trade_mode": "paper",
            "skip_reason_code": None,
            "skip_reason": None,
        }
    return {
        "scan_enabled": True,
        "auto_trade_enabled": weather_auto_trade_enabled,
        "execution_mode": "live-auto-trade" if weather_auto_trade_enabled else "scan-only",
        "trade_mode": "live",
        "skip_reason_code": None,
        "skip_reason": None,
    }


def default_state(runtime_scope: str | None = None):
    """Return the default autonomy state."""
    scope = normalize_runtime_scope(runtime_scope)
    return {
        "runtime_scope": scope,
        "level": "paper" if scope == RUNTIME_SCOPE_PAPER else "penny",
        "promoted_at": None,
        "trades_at_level": 0,
        "wins_at_level": 0,
        "losses_at_level": 0,
        "pnl_at_level": 0.0,
        "returns_at_level": [],
    }


def _read_state_file(path):
    """Read a state file, returning None on failure."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as exc:
        log.warning("Failed to read autonomy state from %s: %s", path, exc)
    return None


def _normalize_state(state):
    """Merge persisted state onto defaults so older files remain valid."""
    scope = runtime_scope_for_level((state or {}).get("level")) if isinstance(state, dict) else RUNTIME_SCOPE_PAPER
    merged = default_state(scope)
    if isinstance(state, dict):
        merged.update(state)
    merged["runtime_scope"] = normalize_runtime_scope(
        merged.get("runtime_scope"),
    )
    if merged.get("level") in LEVELS:
        merged["runtime_scope"] = runtime_scope_for_level(merged["level"])
    return merged


def load_state(runtime_scope: str | None = None):
    """Load autonomy state from disk, migrating the legacy repo-root file if needed."""
    scope = normalize_runtime_scope(runtime_scope)
    state_path = state_file_for_scope(scope)
    state = _read_state_file(state_path)
    if state is not None:
        return _normalize_state(state)

    for legacy_path in (STATE_FILE, LEGACY_STATE_FILE):
        legacy_state = _read_state_file(legacy_path)
        if legacy_state is None:
            continue
        state = _normalize_state(legacy_state)
        if runtime_scope_for_level(state.get("level")) != scope:
            continue
        save_state(state, runtime_scope=scope)
        log.info("Migrated autonomy state from %s to %s", legacy_path, state_path)
        return state

    return default_state(scope)


def save_state(state, runtime_scope: str | None = None):
    """Persist state to disk."""
    scope = normalize_runtime_scope(runtime_scope or (state or {}).get("runtime_scope"))
    state = _normalize_state({**(state or {}), "runtime_scope": scope})
    state_path = state_file_for_scope(scope)
    state_path.parent.mkdir(exist_ok=True)
    tmp_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2))
    tmp_path.replace(state_path)


def journal(entry):
    """Append a decision to the journal (append-only log)."""
    payload = dict(entry or {})
    scope = payload.get("runtime_scope")
    if not scope and payload.get("level"):
        scope = runtime_scope_for_level(payload.get("level"))
    if scope:
        normalized_scope = normalize_runtime_scope(scope)
        payload["runtime_scope"] = normalized_scope
        payload.setdefault("runtime_label", runtime_label(normalized_scope))
    final_entry = journal_writer.append_entry(payload)
    log.info(
        "JOURNAL: %s — %s",
        final_entry.get("action", "?"),
        final_entry.get("reason", "")[:80],
    )


def _safe_record_paper_trade_attempt(**kwargs):
    recorder = getattr(db, "record_paper_trade_attempt", None)
    if not callable(recorder):
        log.warning("Paper-trade attempt logging unavailable in db module; skipping event")
        return False
    try:
        recorder(**kwargs)
        return True
    except Exception as exc:
        log.warning("Paper-trade attempt logging failed; continuing autonomy cycle: %s", exc)
        return False


def record_attempt(level, strategy, outcome, reason_code, reason, **kwargs):
    """Persist an operator-facing paper-trade attempt or gating event."""
    _safe_record_paper_trade_attempt(
        source="autonomy",
        strategy=strategy,
        outcome=outcome,
        reason_code=reason_code,
        reason=reason,
        autonomy_level=level,
        runtime_scope=runtime_scope_for_level(level),
        phase=kwargs.pop("phase", None),
        **kwargs,
    )


def _record_wallet_event(**kwargs):
    recorder = getattr(db, "record_wallet_monitor_event", None)
    if not callable(recorder):
        return False
    try:
        recorder(**kwargs)
        return True
    except Exception as exc:
        log.warning("Wallet monitor event logging failed during autonomy cycle: %s", exc)
        return False


# --- Performance Metrics ---

def get_performance(state):
    """Calculate performance metrics for the current level."""
    total = state["trades_at_level"]
    wins = state["wins_at_level"]
    losses = state["losses_at_level"]
    pnl = state["pnl_at_level"]
    rets = state.get("returns_at_level", [])

    win_rate = (wins / total * 100) if total > 0 else 0

    # Sharpe from returns series
    if len(rets) >= 5:
        import numpy as np
        r = np.array(rets)
        sharpe = float(np.mean(r) / np.std(r) * np.sqrt(365)) if np.std(r) > 0 else 0
    else:
        sharpe = 0.0

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(pnl, 2),
        "sharpe": round(sharpe, 2),
        "returns_count": len(rets),
    }


def check_graduation(state):
    """Check if current level's graduation criteria are met."""
    level_config = LEVELS.get(state["level"], {})
    criteria = level_config.get("graduation")
    if not criteria:
        return False, "Already at top level"

    perf = get_performance(state)

    checks = {
        "min_trades": perf["total_trades"] >= criteria["min_trades"],
        "min_win_rate": perf["win_rate"] >= criteria["min_win_rate"],
        "min_total_pnl": perf["total_pnl"] >= criteria["min_total_pnl"],
        "min_sharpe": perf["sharpe"] >= criteria["min_sharpe"],
    }

    all_pass = all(checks.values())
    reasons = []
    for k, passed in checks.items():
        target = criteria[k]
        actual = perf.get(k.replace("min_", ""), perf.get(k.replace("min_", "total_"), 0))
        status = "PASS" if passed else "FAIL"
        reasons.append(f"  {k}: {actual} {'>=':} {target} [{status}]")

    return all_pass, "\n".join(reasons)


# --- Core Autonomous Loop ---

def run_cycle(state):
    """Run one autonomous cycle: scan → trade → monitor → close → learn.

    This is called every 30 minutes by the LaunchAgent.
    """
    state = _normalize_state(state)
    level = state["level"]
    level_key = str(level).strip().lower()
    runtime_scope = normalize_runtime_scope(state.get("runtime_scope") or runtime_scope_for_level(level_key))
    runtime_tag = runtime_label(runtime_scope)
    state["runtime_scope"] = runtime_scope
    state["runtime_label"] = runtime_tag
    config = get_level_config(level, runtime_scope)
    current_stage = "initializing"
    cycle_started = time.time()
    cycle_summary = {
        "runtime_scope": runtime_scope,
        "runtime_label": runtime_tag,
        "level": level,
        "execution_mode": "synchronous",
        "slot_usage": None,
        "pairs_phase": {
            "status": "not_run",
            "strategy": "cointegration",
            "runtime_scope": runtime_scope,
        },
        "weather_phase": {
            "status": "not_run",
            "strategy": "weather",
            "runtime_scope": runtime_scope,
        },
        "phases": [],
    }
    closed_count = 0

    def _finish_cycle():
        cycle_summary["duration_secs"] = round(time.time() - cycle_started, 1)
        cycle_summary["trade_counts"] = {
            "opened": traded if "traded" in locals() else 0,
            "closed": closed_count,
        }
        return {
            "state": state,
            "cycle_summary": cycle_summary,
        }

    try:
        log.info(
            "=== Autonomy cycle: level=%s (%s) scope=%s runtime=%s ===",
            level,
            config["name"],
            runtime_scope,
            runtime_tag,
        )

        # Step 1: Scan for new signals (use fast async scanner, ~5x faster)
        current_stage = "step 1 scan"
        log.info("Step 1: Scanning for signals (fast mode)...")
        scan_started = time.time()
        try:
            scan_result = asyncio.run(async_scanner.scan(
                z_threshold=1.5,
                p_threshold=0.10,
                min_liquidity=5000,
                interval="1w",
                verbose=False,
                include_stats=True,
            ))
        except Exception as e:
            log.error("Fast scan failed, falling back to sync: %s", e)
            try:
                scan_result = scanner.scan(
                    z_threshold=1.5,
                    p_threshold=0.10,
                    min_liquidity=5000,
                    interval="1w",
                    verbose=False,
                    include_stats=True,
                )
            except Exception as e2:
                log.error("Scan failed: %s", e2)
                journal({"action": "scan_failed", "reason": str(e2), "level": level})
                record_attempt(
                    level,
                    "system",
                    "error",
                    "scan_failed",
                    f"Autonomy scan failed: {e2}",
                    event="Autonomy scan",
                    phase=current_stage,
                )
                return _finish_cycle()

        opportunities = scan_result["opportunities"]
        scan_duration = round(time.time() - scan_started, 1)

        # Save scan run
        current_stage = "step 1b persist scan run"
        db.save_scan_run(pairs_tested=scan_result["pairs_tested"], cointegrated=scan_result["pairs_cointegrated"],
                         opportunities=len(opportunities), duration=scan_duration)

        paper_mode = runtime_scope == RUNTIME_SCOPE_PAPER
        trial_settings = cointegration_trial.get_trial_settings()
        admitted_signals = []
        a_trial_candidates = 0
        a_trial_eligible = 0
        a_trial_rejected = 0
        rejection_counts = {}
        for opp in opportunities:
            try:
                perplexity.annotate_profitable_candidate(opp)
            except Exception as exc:
                log.warning("Perplexity annotation failed for '%s': %s", opp.get("event", "?")[:40], exc)
            evaluation = cointegration_trial.annotate_opportunity(
                opp,
                mode="paper" if paper_mode else "live",
                settings=trial_settings,
            )
            if opp.get("grade_label") == "A":
                a_trial_candidates += 1
                if evaluation["admit_trade"]:
                    a_trial_eligible += 1
                else:
                    a_trial_rejected += 1
                    code = evaluation["reason_code"]
                    rejection_counts[code] = rejection_counts.get(code, 0) + 1
            if evaluation["admit_trade"]:
                admitted_signals.append(opp)

        # Save all signals after admission metadata is attached
        current_stage = "step 1c persist signals"
        new_signal_ids = []
        for opp in opportunities:
            try:
                sid = db.save_signal(opp)
                opp["id"] = sid
                new_signal_ids.append(sid)
            except Exception as e:
                log.warning("Failed to save signal: %s", e)

        for opp in opportunities:
            if opp.get("admit_trade"):
                continue
            details = {
                "grade_label": opp.get("grade_label"),
                "grade": opp.get("grade"),
                "tradeable": bool(opp.get("tradeable")),
                "paper_tradeable": bool(opp.get("paper_tradeable")),
                "admission_path": opp.get("admission_path"),
                "filters_failed": opp.get("filters_failed"),
                "failed_filter_count": opp.get("failed_filter_count"),
                "blocker_context": opp.get("blocker_context"),
                "guardrails": opp.get("experiment_guardrails"),
            }
            record_attempt(
                level,
                "pairs",
                "blocked",
                opp.get("experiment_reason_code") or "strategy_not_tradeable",
                opp.get("experiment_reason") or "Signal did not pass autonomy strategy filters.",
                event=opp.get("event"),
                signal_id=opp.get("id"),
                size_usd=config.get("size_usd"),
                phase="step 1 strategy admission",
                details=details,
            )
            if opp.get("grade_label") == "A":
                journal({
                    "action": "cointegration_trial_blocked",
                    "level": level,
                    "event": opp.get("event"),
                    "signal_id": opp.get("id"),
                    "grade_label": opp.get("grade_label"),
                    "grade": opp.get("grade"),
                    "failed_filter_count": opp.get("failed_filter_count"),
                    "filters_failed": opp.get("filters_failed"),
                    "blocker_context": opp.get("blocker_context"),
                    "admission_path": opp.get("admission_path"),
                    "reason_code": opp.get("experiment_reason_code"),
                    "reason": opp.get("experiment_reason"),
                    "allowed_failed_filters": trial_settings["allowed_failed_filters"],
                    "max_allowed_failed_filters": trial_settings["max_allowed_failed_filters"],
                })

        # Perplexity remains observability-only. It can enrich logs/UI context, but it
        # must not silently narrow penny/book admission after paper has admitted a signal.
        stage3_gate_applied = False
        stage3_gate_blocked = 0
        stage3_gate_passed = 0
        perplexity_profitable_candidates = sum(
            1 for opp in admitted_signals if opp.get("profitable_candidate_feature")
        )
        journal({
            "action": "perplexity_observability",
            "level": level,
            "admitted_signals": len(admitted_signals),
            "profitable_candidates": perplexity_profitable_candidates,
            "gate_applied": False,
        })

        tradeable = [o for o in opportunities if o.get("tradeable")]
        log.info(
            "Scan found %d signals, %d A+ tradeable, %d admitted for this level",
            len(opportunities),
            len(tradeable),
            len(admitted_signals),
        )
        log.info(
            "Cointegration A-trial status: enabled=%s live_parity=%s candidates=%d eligible=%d rejected=%d",
            trial_settings["enabled"],
            not trial_settings["paper_only"],
            a_trial_candidates,
            a_trial_eligible,
            a_trial_rejected,
        )

        journal({
            "action": "scan_complete",
            "level": level,
            "total_signals": len(opportunities),
            "tradeable": len(tradeable),
            "admitted_signals": len(admitted_signals),
            "a_trial_candidates": a_trial_candidates,
            "a_trial_eligible": a_trial_eligible,
            "a_trial_rejected": a_trial_rejected,
            "a_trial_rejection_counts": rejection_counts,
            "perplexity_profitable_candidates": perplexity_profitable_candidates,
            "signal_ids": new_signal_ids,
            "stage3_gate_applied": stage3_gate_applied,
            "stage3_gate_passed": stage3_gate_passed,
            "stage3_gate_blocked": stage3_gate_blocked,
        })

        if paper_mode and admitted_signals:
            journal({
                "action": "brain_validation_skipped",
                "level": level,
                "signal_count": len(admitted_signals),
                "signal_ids": [opp.get("id") for opp in admitted_signals if opp.get("id")],
                "reason": "Paper mode trusts math-only filters for cointegration opportunities.",
            })

        # Step 2: Monitor existing positions
        current_stage = "step 2 refresh open trades"
        log.info("Step 2: Monitoring open trades...")
        try:
            updates = tracker.refresh_open_trades(runtime_scope=runtime_scope)
            if updates:
                for u in updates:
                    pnl_info = u.get("unrealized_pnl", {})
                    if u.get("trade_type") == "weather":
                        log.info("  Trade %d [weather]: price=%.4f pnl=$%.2f",
                                 u["trade_id"], u.get("current_price_a", 0),
                                 pnl_info.get("pnl_usd", 0))
                    else:
                        log.info("  Trade %d: z=%.2f pnl=$%.2f",
                                 u["trade_id"], u.get("z_score", 0),
                                 pnl_info.get("pnl_usd", 0))
        except Exception as e:
            log.warning("Trade monitoring failed during %s: %s", current_stage, e)
            record_attempt(
                level,
                "system",
                "error",
                "refresh_open_trades_failed",
                f"Open-trade refresh failed: {e}",
                event="Open trade refresh",
                phase=current_stage,
            )

        # Step 2b: Manage pending maker orders (check fills, cancel expired)
        current_stage = "step 2b manage maker orders"
        try:
            order_result = execution.manage_open_orders()
            if order_result["filled"] or order_result["cancelled"]:
                log.info("Step 2b: maker orders — %d filled, %d cancelled",
                         order_result["filled"], order_result["cancelled"])
        except Exception as e:
            log.warning("Maker order management failed during %s: %s", current_stage, e)
            record_attempt(
                level,
                "system",
                "error",
                "maker_order_management_failed",
                f"Maker-order management failed: {e}",
                event="Maker orders",
                phase=current_stage,
            )

        # Step 2c: Reconcile stuck or contradictory open-trade states
        current_stage = "step 2c reconcile open trades"
        try:
            reconciliation = trade_monitor.reconcile_open_trades(auto_remediate=True, runtime_scope=runtime_scope)
            flagged = reconciliation["counts"].get("resolved", 0)
            flagged += reconciliation["counts"].get("unpriceable-but-identifiable", 0)
            flagged += reconciliation["counts"].get("detached-from-watched-wallet", 0)
            attention = sum(
                1
                for item in reconciliation.get("results", [])
                if item.get("status") == "attention_required"
            )
            if reconciliation["auto_closed_trade_ids"] or flagged or attention:
                log.info(
                    "Step 2c: trade reconciliation auto_closed=%d flagged=%d attention=%d",
                    len(reconciliation["auto_closed_trade_ids"]),
                    flagged,
                    attention,
                )
        except Exception as e:
            log.warning("Trade reconciliation failed during %s: %s", current_stage, e)
            record_attempt(
                level,
                "system",
                "error",
                "trade_reconciliation_failed",
                f"Open-trade reconciliation failed: {e}",
                event="Trade reconciliation",
                phase=current_stage,
            )

        # Step 3: Auto-close reverted trades
        current_stage = "step 3 auto-close"
        log.info("Step 3: Checking for auto-closes...")
        try:
            closed = tracker.auto_close_trades(z_threshold=0.5, runtime_scope=runtime_scope)
            closed_count = len(closed)
            for c in closed:
                pnl = c["pnl_usd"]
                state["trades_at_level"] += 1
                state["pnl_at_level"] += pnl
                if pnl > 0:
                    state["wins_at_level"] += 1
                else:
                    state["losses_at_level"] += 1
                state["returns_at_level"].append(pnl)

                journal({
                    "action": "trade_closed",
                    "level": level,
                    "trade_id": c["trade_id"],
                    "trade_type": c.get("trade_type", "pairs"),
                    "pnl_usd": pnl,
                    "z_score_at_close": c.get("z_score", None),
                    "reason": c.get("reason", "Auto-close"),
                })

                if c.get("trade_type") == "weather":
                    log.info("  Closed weather trade %d: pnl=$%.2f (%s)",
                             c["trade_id"], pnl, c.get("reason", ""))
                else:
                    log.info("  Closed trade %d: pnl=$%.2f (z=%.3f)",
                             c["trade_id"], pnl, c.get("z_score", 0))
        except Exception as e:
            log.warning("Auto-close failed during %s: %s", current_stage, e)
            record_attempt(
                level,
                "system",
                "error",
                "auto_close_failed",
                f"Auto-close failed: {e}",
                event="Auto-close",
                phase=current_stage,
            )

        # Step 4: Open new trades (if allowed at this level)
        current_stage = "step 4 open pairs preflight"
        if not config["can_trade"]:
            log.info("Step 4: SCOUT mode — not trading")
            journal({"action": "scout_only", "level": level,
                     "reason": "Level does not permit trading"})
            save_state(state)
            return _finish_cycle()

        if not config.get("auto_trade_enabled", True):
            log.info("Step 4: Auto-trading disabled for scope=%s level=%s", runtime_scope, level)
            cycle_summary["pairs_phase"] = {
                "status": "skipped",
                "strategy": "cointegration",
                "runtime_scope": runtime_scope,
                "trade_execution_status": "scan_only",
                "reason_code": "runtime_auto_trade_disabled",
                "reason": f"Cointegration auto-trading is disabled for scope={runtime_scope}.",
            }
            journal({
                "action": "auto_trade_disabled",
                "level": level,
                "runtime_scope": runtime_scope,
                "reason": "Runtime auto-trading control is disabled.",
            })
            record_attempt(
                level,
                "pairs",
                "blocked",
                "auto_trade_disabled",
                f"Runtime auto-trading is disabled for scope={runtime_scope}.",
                event="Pairs autonomy preflight",
                phase=current_stage,
                details={"runtime_controls": config.get("runtime_controls")},
            )
            save_state(state)
            return _finish_cycle()

        max_open = config["max_open"]
        slot_usage = db.get_runtime_slot_usage(runtime_scope=runtime_scope, max_open=max_open)
        cycle_summary["slot_usage"] = slot_usage
        open_count = slot_usage["open_positions"]
        open_trades = db.get_trades(status="open", limit=None, runtime_scope=runtime_scope)
        pairs_phase = {
            "status": "running",
            "strategy": "cointegration",
            "runtime_scope": runtime_scope,
            "slot_usage_at_start": slot_usage,
            "result_counts": {
                "admitted": len(admitted_signals),
                "traded": 0,
            },
        }
        cycle_summary["pairs_phase"] = pairs_phase

        if runtime_scope == RUNTIME_SCOPE_PENNY:
            log.info(
                "Step 4: Penny slot usage scope=%s usage=%s available=%s consumers=%s",
                runtime_scope,
                slot_usage.get("max_open_usage") or "uncapped",
                slot_usage.get("slots_remaining"),
                json.dumps(slot_usage.get("consuming_trade_ids") or []),
            )

        if max_open is not None and open_count >= max_open:
            log.info("Step 4: At max positions (%d/%d), skipping new trades",
                     open_count, max_open)
            pairs_phase.update({
                "status": "blocked",
                "trade_execution_status": "slots_full",
                "reason_code": "max_open_reached",
                "reason": f"No cointegration slots remain for scope={runtime_scope}.",
                "blocking_trades": slot_usage.get("consuming_trades") or [],
                "slot_usage_at_end": slot_usage,
            })
            journal({"action": "skip_trade", "level": level,
                     "reason": f"At max positions ({open_count}/{max_open})"})
            record_attempt(
                level,
                "pairs",
                "blocked",
                "max_open_reached",
                f"At max positions ({open_count}/{max_open}).",
                event="Pairs autonomy preflight",
                phase=current_stage,
                details={
                    "open_count": open_count,
                    "max_open": max_open,
                    "slot_usage": slot_usage,
                },
            )
            save_state(state)
            return _finish_cycle()

        # Determine trade size
        if level == "book":
            # Kelly-sized from bankroll
            bankroll = config.get("bankroll", 1000)
        else:
            size_usd = config["size_usd"]

        slots = (max_open - open_count) if max_open is not None else len(admitted_signals)
        traded = 0
        if max_open is None:
            pairs_phase["trade_execution_status"] = "enabled"
        elif slots <= 0:
            pairs_phase["trade_execution_status"] = "slots_full"
        elif len(admitted_signals) > slots:
            pairs_phase["trade_execution_status"] = "limited_by_slots"
        else:
            pairs_phase["trade_execution_status"] = "enabled"
        if pairs_phase.get("trade_execution_status") in {"slots_full", "limited_by_slots"}:
            pairs_phase["blocking_trades"] = slot_usage.get("consuming_trades") or []

        # Build dedup sets from currently open trades — keyed by signal_id and event name
        open_signal_ids = {t.get("signal_id") for t in open_trades if t.get("signal_id")}
        open_events = {t.get("event", "") for t in open_trades}
        # Also track what we open within this cycle so we don't double-open
        this_cycle_signal_ids = set()
        this_cycle_events = set()

        def _stage2_details(result_obj):
            context = (result_obj or {}).get("stage2_context")
            if not context:
                return {}
            return {"stage2_polygon": context}

        for opp in admitted_signals:
            if traded >= slots:
                break

            event_name = opp.get("event", "")

            # Get the signal ID for this opportunity
            signal_id = opp.get("id")
            if not signal_id:
                for sid in new_signal_ids:
                    for s in db.get_signals(limit=50):
                        if s["id"] == sid and s["event"] == event_name:
                            signal_id = sid
                            opp["id"] = sid
                            break
                    if signal_id:
                        break

            if not signal_id:
                log.warning("  Could not find signal ID for '%s'", event_name[:40])
                continue

            # Skip if same signal or same event already open (DB or this cycle)
            if signal_id in open_signal_ids or signal_id in this_cycle_signal_ids:
                log.info("  Skip: signal %d already has an open trade", signal_id)
                journal({"action": "skip_trade", "level": level,
                         "reason": f"Signal {signal_id} already open"})
                record_attempt(
                    level,
                    "pairs",
                    "blocked",
                    "signal_already_open",
                    f"Signal {signal_id} already has an open trade.",
                    event=event_name,
                    signal_id=signal_id,
                    phase=current_stage,
                )
                continue

            if event_name in open_events or event_name in this_cycle_events:
                log.info("  Skip: already have position in '%s'", event_name[:40])
                journal({"action": "skip_trade", "level": level,
                         "reason": f"Already trading event: {event_name[:40]}"})
                record_attempt(
                    level,
                    "pairs",
                    "blocked",
                    "event_already_open",
                    f"Already trading event: {event_name[:60]}",
                    event=event_name,
                    signal_id=signal_id,
                    phase=current_stage,
                )
                continue

            # Determine size for this trade
            if level == "book":
                ev = opp.get("ev", {})
                # correlated_legs=True: pairs trades expose both legs to the same event;
                # Kelly assumes independent bets, so halve fraction to compensate.
                sizing = math_engine.position_size(bankroll, ev, correlated_legs=True) if ev else None
                trade_size = sizing["recommended_size"] if sizing else 50
                trade_size = max(5, min(trade_size, bankroll * 0.25))
            else:
                trade_size = size_usd

            if opp.get("admission_path") in {"a_grade_trial", "paper_a_trial"}:
                trial_size = opp.get("trial_recommended_size_usd")
                if trial_size:
                    trade_size = min(trade_size, trial_size) if trade_size else trial_size

            # Execute
            mode = "paper" if paper_mode else "live"

            # Step 4.5: Brain validation (live-only)
            # Ask Claude + Perplexity if this signal makes sense in the real world
            if mode == "live":
                try:
                    import brain
                    should_trade, brain_reasoning = brain.validate_signal(opp)
                    if not should_trade:
                        log.info("  Brain REJECTED trade: %s", brain_reasoning)
                        journal({
                            "action": "brain_reject",
                            "level": level,
                            "event": event_name[:60],
                            "reason": brain_reasoning,
                        })
                        record_attempt(
                            level,
                            "pairs",
                            "blocked",
                            "brain_rejected",
                            brain_reasoning,
                            event=event_name,
                            signal_id=signal_id,
                            size_usd=trade_size,
                            phase="step 4.5 brain validation",
                            details={"live_only_safeguard": mode == "live"},
                        )
                        continue
                    log.info("  Brain VALIDATED trade: %s", brain_reasoning)
                    opp["brain_reasoning"] = brain_reasoning
                except Exception as e:
                    log.warning("  Brain validation failed (defaulting to math-only): %s", e)
            else:
                log.debug("  Brain validation skipped in paper mode; trusting math-only filters")

            sizing_decision = None
            if mode == "paper":
                sizing_decision = paper_sizing.build_paper_sizing_decision(
                    "cointegration",
                    opp,
                    baseline_size_usd=trade_size,
                    account_overview=db.get_paper_account_overview(refresh_unrealized=False, runtime_scope=runtime_scope),
                    mode=mode,
                    source="autonomy",
                    signal_id=signal_id,
                )
                paper_sizing.record_sizing_decision(sizing_decision)
                trade_size = sizing_decision["selected_size_usd"]
                opp["paper_sizing"] = sizing_decision

            log.info("  Opening %s trade: %s | $%.2f", mode, event_name[:40], trade_size)

            try:
                result = execution.execute_trade(opp, size_usd=trade_size, mode=mode)
                if result["ok"]:
                    traded += 1
                    this_cycle_signal_ids.add(signal_id)
                    this_cycle_events.add(event_name)
                    record_attempt(
                        level,
                        "pairs",
                        "allowed",
                        "opened",
                        f"{'Paper' if mode == 'paper' else 'Penny'} pairs trade opened.",
                        event=event_name,
                        signal_id=signal_id,
                        trade_id=result.get("trade_id"),
                        size_usd=trade_size,
                        phase=current_stage,
                        details={
                            "grade": opp.get("grade_label"),
                            "admission_path": opp.get("admission_path"),
                            "experiment_status": opp.get("experiment_status"),
                            "paper_sizing": sizing_decision,
                            "reason_code": result.get("reason_code"),
                            "entry_execution": result.get("entry_execution"),
                            "pending": result.get("pending"),
                            "order_a": result.get("order_a"),
                            "order_b": result.get("order_b"),
                            **_stage2_details(result),
                        },
                    )
                    if mode == "paper":
                        journal({
                            "action": "trade_opened",
                            "level": level,
                            "mode": mode,
                            "trade_id": result.get("trade_id"),
                            "signal_id": signal_id,
                            "event": event_name[:60],
                            "size_usd": trade_size,
                            "z_score": opp.get("z_score", 0),
                            "grade": opp.get("grade_label", "?"),
                            "ev_pct": opp.get("ev", {}).get("ev_pct", 0),
                            "paper_sizing_policy": (sizing_decision or {}).get("selected_policy"),
                            "paper_gate_rollout_state": (sizing_decision or {}).get("rollout_state"),
                            "paper_gate_applied": (sizing_decision or {}).get("applied"),
                            "paper_gate_blocker_codes": ((sizing_decision or {}).get("activation_status") or {}).get("blocker_codes"),
                            "paper_gate_blockers": ((sizing_decision or {}).get("activation_status") or {}).get("blockers"),
                            "paper_gate_can_apply_confidence": ((sizing_decision or {}).get("activation_status") or {}).get("can_apply_confidence"),
                            "paper_gate_compare_only": (sizing_decision or {}).get("compare_only"),
                            "paper_confidence_size_usd": (sizing_decision or {}).get("confidence_size_usd"),
                            "admission_path": opp.get("admission_path"),
                            "experiment_status": opp.get("experiment_status"),
                            "reason": opp.get("experiment_reason") or f"Signal admitted, z={opp.get('z_score', 0):+.2f}",
                        })
                else:
                    record_attempt(
                        level,
                        "pairs",
                        "blocked",
                        result.get("reason_code") or "execution_rejected",
                        result.get("error", "Trade execution rejected."),
                        event=event_name,
                        signal_id=signal_id,
                        size_usd=trade_size,
                        phase=current_stage,
                        details={
                            "grade": opp.get("grade_label"),
                            "paper_sizing": sizing_decision,
                            "mode": mode,
                            "pending": result.get("pending"),
                            "slippage": result.get("slippage"),
                            "balance": result.get("balance"),
                            **_stage2_details(result),
                        },
                    )
                    journal({
                        "action": "trade_rejected",
                        "level": level,
                        "event": event_name[:60],
                        "grade": opp.get("grade_label", "?"),
                        "admission_path": opp.get("admission_path"),
                        "reason_code": result.get("reason_code"),
                        "reason": result.get("error", "unknown"),
                    })
            except Exception as e:
                log.error("  Trade execution failed: %s", e)
                record_attempt(
                    level,
                    "pairs",
                    "error",
                    "trade_execution_failed",
                    f"Trade execution failed: {e}",
                    event=event_name,
                    signal_id=signal_id,
                    size_usd=trade_size,
                    phase=current_stage,
                    details={"mode": mode},
                )
                journal({"action": "trade_error", "level": level,
                         "event": event_name[:60], "reason": str(e)})

        log.info("Step 4: Opened %d new trades", traded)
        pairs_phase["status"] = "completed"
        pairs_phase["result_counts"]["traded"] = traded
        if pairs_phase.get("trade_execution_status") == "limited_by_slots":
            pairs_phase["reason_code"] = "limited_by_slots"
            pairs_phase["reason"] = (
                f"Cointegration candidates were capped by penny max-open usage "
                f"({slot_usage.get('max_open_usage')})."
            )
        pairs_phase["slot_usage_at_end"] = db.get_runtime_slot_usage(
            runtime_scope=runtime_scope,
            max_open=max_open,
        )

        for code, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0])):
            log.info("A-trial rejection summary: %s=%d", code, count)

        # Step 4b: Open weather trades (if slots remain)
        current_stage = "step 4b open weather preflight"
        weather_slot_usage = db.get_runtime_slot_usage(runtime_scope=runtime_scope, max_open=max_open)
        open_count = weather_slot_usage["open_positions"]
        slots_remaining = weather_slot_usage["slots_remaining"]

        weather_phase_started = time.time()
        weather_phase = {
            "name": "weather_scan",
            "strategy": "weather",
            "runtime_scope": runtime_scope,
            "status": "running",
            "started_at": weather_phase_started,
            "result_counts": {
                "opportunities": 0,
                "tradeable": 0,
                "saved": 0,
                "traded": 0,
                "exact_temp_opportunities": 0,
            },
        }
        weather_phase["slot_usage_at_start"] = weather_slot_usage
        cycle_summary["phases"].append(weather_phase)
        cycle_summary["weather_phase"] = weather_phase
        weather_policy = weather_phase_policy(runtime_scope, config.get("runtime_controls"))
        weather_phase["execution_mode"] = weather_policy["execution_mode"]
        weather_phase["auto_trade_enabled"] = bool(weather_policy["auto_trade_enabled"])
        log.info(
            "Step 4b: Weather phase start scope=%s mode=%s usage=%s available=%s consumers=%s",
            runtime_scope,
            weather_phase["execution_mode"],
            weather_slot_usage.get("max_open_usage") or "uncapped",
            slots_remaining,
            json.dumps(weather_slot_usage.get("consuming_trade_ids") or []),
        )
        try:
            import weather_exact_temp_scanner
            import weather_strategy

            include_exact_temp = weather_exact_temp_scanner.exact_temp_enabled()
            exact_temp_autotrade = weather_exact_temp_scanner.exact_temp_autotrade_enabled()
            weather_opps, _ = weather_strategy.scan_weather_opportunities(
                min_edge=0.06,
                verbose=False,
                include_exact_temp=include_exact_temp,
            )
            tradeable_weather = [o for o in weather_opps if o.get("tradeable")]
            executable_weather = list(tradeable_weather)
            weather_phase["result_counts"]["opportunities"] = len(weather_opps)
            weather_phase["result_counts"]["tradeable"] = len(tradeable_weather)
            weather_phase["exact_temp_enabled"] = bool(include_exact_temp)
            weather_phase["exact_temp_autotrade_enabled"] = bool(exact_temp_autotrade)
            weather_phase["result_counts"]["exact_temp_opportunities"] = sum(
                1
                for opp in weather_opps
                if (opp.get("strategy_name") or opp.get("market_family")) == "weather_exact_temp"
            )
            if include_exact_temp and not exact_temp_autotrade:
                executable_weather = [
                    o for o in executable_weather
                    if (o.get("strategy_name") or o.get("market_family")) != "weather_exact_temp"
                ]
            if weather_policy["trade_mode"] == "live":
                executable_weather = [
                    o for o in executable_weather
                    if (o.get("strategy_name") or o.get("market_family")) != "weather_exact_temp"
                ]

            weather_traded = 0
            weather_phase["trade_candidates"] = len(executable_weather)
            candidates = executable_weather
            if weather_policy["auto_trade_enabled"]:
                if slots_remaining is not None and slots_remaining <= 0:
                    candidates = []
                    weather_phase["trade_execution_status"] = "slots_full"
                    weather_phase["reason_code"] = "max_open_reached_before_weather"
                    weather_phase["reason"] = f"No weather slots remain for scope={runtime_scope}."
                    weather_phase["blocking_trades"] = weather_slot_usage.get("consuming_trades") or []
                elif slots_remaining is not None:
                    candidates = executable_weather[:slots_remaining]
                    weather_phase["trade_execution_status"] = (
                        "limited_by_slots"
                        if len(executable_weather) > max(slots_remaining, 0)
                        else "enabled"
                    )
                    weather_phase["blocking_trades"] = weather_slot_usage.get("consuming_trades") or []
                else:
                    weather_phase["trade_execution_status"] = "enabled"
            else:
                candidates = []
                weather_phase["trade_execution_status"] = "scan_only"
                weather_phase["reason_code"] = "runtime_auto_trade_disabled"
                weather_phase["reason"] = f"Weather scanning completed for scope={runtime_scope}; the primary runtime auto-trade control is disabled."
                journal({
                    "action": "weather_scan_only",
                    "level": level,
                    "runtime_scope": runtime_scope,
                    "strategy": "weather",
                    "reason": weather_phase["reason"],
                })

            for w_opp in executable_weather:
                try:
                    w_id = db.save_weather_signal(w_opp)
                    weather_phase["result_counts"]["saved"] += 1
                    if not weather_policy["auto_trade_enabled"]:
                        continue
                    if slots_remaining is not None and weather_traded >= max(slots_remaining, 0):
                        continue
                    trade_size = size_usd if level != "book" else 20
                    sizing_account_overview = (
                        db.get_runtime_account_overview(refresh_unrealized=False, runtime_scope=runtime_scope)
                        if runtime_scope == RUNTIME_SCOPE_PENNY
                        else db.get_paper_account_overview(refresh_unrealized=False, runtime_scope=runtime_scope)
                    )
                    sizing_decision = paper_sizing.build_paper_sizing_decision(
                        "weather",
                        w_opp,
                        baseline_size_usd=trade_size,
                        account_overview=sizing_account_overview,
                        mode=weather_policy["trade_mode"],
                        source="autonomy",
                        weather_signal_id=w_id,
                    )
                    paper_sizing.record_sizing_decision(sizing_decision)
                    trade_size = sizing_decision["selected_size_usd"]
                    decision = db.inspect_weather_trade_open(
                        w_id,
                        size_usd=trade_size,
                        max_total_open=max_open if weather_policy["auto_trade_enabled"] else None,
                        mode=weather_policy["trade_mode"],
                        runtime_scope=runtime_scope,
                    )
                    if not decision["ok"]:
                        record_attempt(
                            level,
                            "weather",
                            "blocked",
                            decision["reason_code"],
                            decision["reason"],
                            event=w_opp.get("event", w_opp.get("market", "")),
                            weather_signal_id=w_id,
                            token_id=decision.get("entry_token"),
                            size_usd=trade_size,
                            phase=current_stage,
                            details={
                                "paper_sizing": sizing_decision,
                                "execution_mode": weather_phase["execution_mode"],
                                "decision_source": decision.get("decision_source"),
                                "history_source": decision.get("history_source"),
                                "history_runtime_scope": decision.get("history_runtime_scope"),
                                "history_strategy": decision.get("history_strategy"),
                            },
                        )
                        journal({
                            "action": "skip_trade",
                            "level": level,
                            "trade_type": "weather",
                            "event": w_opp.get("event", w_opp.get("market", ""))[:60],
                            "reason": decision["reason"],
                            "runtime_scope": runtime_scope,
                            "decision_source": decision.get("decision_source"),
                            "history_source": decision.get("history_source"),
                        })
                        continue
                    weather_signal_payload = {
                        **w_opp,
                        "id": w_id,
                        "paper_sizing": sizing_decision,
                    }
                    result = execution.execute_weather_trade(
                        weather_signal_payload,
                        size_usd=trade_size,
                        mode=weather_policy["trade_mode"],
                    )
                    if result.get("ok"):
                        record_attempt(
                            level,
                            "weather",
                            "allowed",
                            "opened",
                            f"{weather_policy['trade_mode'].capitalize()} weather trade opened.",
                            event=w_opp.get("event", w_opp.get("market", "")),
                            weather_signal_id=w_id,
                            trade_id=result["trade_id"],
                            token_id=decision.get("entry_token"),
                            size_usd=trade_size,
                            phase=current_stage,
                            details={"paper_sizing": sizing_decision, "execution_mode": weather_phase["execution_mode"]},
                        )
                        weather_traded += 1
                        weather_phase["result_counts"]["traded"] = weather_traded
                        journal({
                            "action": "trade_opened",
                            "level": level,
                            "mode": weather_policy["trade_mode"],
                            "trade_id": result["trade_id"],
                            "signal_id": w_id,
                            "trade_type": "weather",
                            "event": w_opp.get("event", w_opp.get("market", ""))[:60],
                            "size_usd": trade_size,
                            "paper_sizing_policy": sizing_decision.get("selected_policy"),
                            "paper_gate_rollout_state": sizing_decision.get("rollout_state"),
                            "paper_gate_applied": sizing_decision.get("applied"),
                            "paper_gate_blocker_codes": (sizing_decision.get("activation_status") or {}).get("blocker_codes"),
                            "paper_gate_blockers": (sizing_decision.get("activation_status") or {}).get("blockers"),
                            "paper_gate_can_apply_confidence": (sizing_decision.get("activation_status") or {}).get("can_apply_confidence"),
                            "paper_gate_compare_only": sizing_decision.get("compare_only"),
                            "paper_confidence_size_usd": sizing_decision.get("confidence_size_usd"),
                            "reason": f"Weather edge {w_opp.get('combined_edge_pct', 0):+.1f}%",
                            "runtime_scope": runtime_scope,
                        })
                    else:
                        record_attempt(
                            level,
                            "weather",
                            "error",
                            result.get("reason_code", "trade_open_failed"),
                            result.get("error", "Weather trade open failed."),
                            event=w_opp.get("event", w_opp.get("market", "")),
                            weather_signal_id=w_id,
                            token_id=decision.get("entry_token"),
                            size_usd=trade_size,
                            phase=current_stage,
                            details={
                                "paper_sizing": sizing_decision,
                                "execution_mode": weather_phase["execution_mode"],
                                "execution_result": result,
                            },
                        )
                except Exception as e:
                    log.warning("Weather trade open failed: %s", e)
                    record_attempt(
                        level,
                        "weather",
                        "error",
                        "trade_open_failed",
                        f"Weather trade open failed: {e}",
                        event=w_opp.get("event", w_opp.get("market", "")),
                        phase=current_stage,
                    )
            weather_phase["status"] = "completed"
            weather_phase["duration_secs"] = round(time.time() - weather_phase_started, 1)
            weather_phase["slot_usage_at_end"] = db.get_runtime_slot_usage(
                runtime_scope=runtime_scope,
                max_open=max_open,
            )
            if weather_traded:
                log.info("Step 4b: Opened %d weather trades", weather_traded)
            log.info(
                "Step 4b: Weather phase complete scope=%s status=completed duration=%.1fs opportunities=%d tradeable=%d traded=%d mode=%s trade_status=%s reason=%s",
                runtime_scope,
                weather_phase["duration_secs"],
                weather_phase["result_counts"]["opportunities"],
                weather_phase["result_counts"]["tradeable"],
                weather_phase["result_counts"]["traded"],
                weather_phase["execution_mode"],
                weather_phase.get("trade_execution_status"),
                weather_phase.get("reason_code"),
            )
        except Exception as e:
            weather_phase["status"] = "error"
            weather_phase["reason_code"] = "weather_scan_failed"
            weather_phase["reason"] = str(e)
            weather_phase["duration_secs"] = round(time.time() - weather_phase_started, 1)
            log.debug("Weather scan skipped during %s: %s", current_stage, e)
            record_attempt(
                level,
                "weather",
                "error",
                "weather_scan_failed",
                f"Weather scan failed: {e}",
                event="Weather scan",
                phase=current_stage,
            )
            log.info(
                "Step 4b: Weather phase complete scope=%s status=error duration=%.1fs opportunities=%d tradeable=%d traded=%d mode=%s reason=%s",
                runtime_scope,
                weather_phase["duration_secs"],
                weather_phase["result_counts"]["opportunities"],
                weather_phase["result_counts"]["tradeable"],
                weather_phase["result_counts"]["traded"],
                weather_phase["execution_mode"],
                weather_phase["reason_code"],
            )

        # Step 4c: Longshot bias scanner (scan for NO maker opportunities on 3–15¢ markets)
        current_stage = "step 4c longshot scan"
        try:
            import longshot_scanner
            import db as _db
            longshot_opps, ls_stats = longshot_scanner.scan(verbose=False)
            tradeable_longshots = [o for o in longshot_opps if o.get("tradeable")]
            for opp in tradeable_longshots:
                try:
                    _db.save_longshot_signal(opp)
                except Exception:
                    pass
            if tradeable_longshots:
                log.info("Step 4c: Longshot scan — %d tradeable (of %d) | top EV=%.2f%%",
                         len(tradeable_longshots), len(longshot_opps),
                         tradeable_longshots[0].get("ev_pct", 0))
                journal({
                    "action": "longshot_scan",
                    "level": level,
                    "tradeable": len(tradeable_longshots),
                    "total": len(longshot_opps),
                    "top_ev_pct": tradeable_longshots[0].get("ev_pct", 0) if tradeable_longshots else 0,
                })
            else:
                log.debug("Step 4c: Longshot scan — %d candidates, none tradeable", len(longshot_opps))
        except Exception as e:
            log.debug("Longshot scan step skipped during %s: %s", current_stage, e)

        # Step 4d: Near-certainty scanner (85–99¢ YES markets with calibration edge)
        current_stage = "step 4d near-certainty scan"
        try:
            import near_certainty_scanner
            nc_opps, nc_stats = near_certainty_scanner.scan(use_brain=False, verbose=False)
            tradeable_nc = [o for o in nc_opps if o.get("tradeable")]
            for opp in tradeable_nc:
                try:
                    db.save_near_certainty_signal(opp)
                except Exception:
                    pass
            if tradeable_nc:
                log.info("Step 4d: Near-certainty — %d tradeable (of %d) | top EV=%.2f%%",
                         len(tradeable_nc), len(nc_opps),
                         tradeable_nc[0].get("ev_pct", 0))
                journal({
                    "action": "near_certainty_scan",
                    "level": level,
                    "tradeable": len(tradeable_nc),
                    "total": len(nc_opps),
                    "top_ev_pct": tradeable_nc[0].get("ev_pct", 0) if tradeable_nc else 0,
                })
            else:
                log.debug("Step 4d: Near-certainty — %d candidates, none tradeable", len(nc_opps))
        except Exception as e:
            log.debug("Near-certainty scan step skipped during %s: %s", current_stage, e)

        # Step 4e: Auto-mirror copy trader positions
        current_stage = "step 4e copy trader"
        if not paper_only_runtime(runtime_scope):
            log.info("Step 4e: Skipping paper-only copy trader for scope=%s", runtime_scope)
            journal({
                "action": "paper_only_step_skipped",
                "level": level,
                "runtime_scope": runtime_scope,
                "strategy": "copy",
                "reason": f"Copy-trader mirroring is paper-only and is disabled for scope={runtime_scope}.",
            })
        else:
            try:
                import copy_scanner
                copy_trade_settings = db.get_copy_trade_settings()
                max_copy_wallet_open = copy_trade_settings["per_wallet_cap"] if copy_trade_settings["cap_enabled"] else None
                max_copy_total_open = copy_trade_settings["total_open_cap"] if copy_trade_settings["cap_enabled"] else None
                copy_opened = 0
                copy_closed = 0
                now_ts = time.time()

                # Build index of currently open copy trades: (wallet, condition_id) → trade
                open_copy = {
                    ((t.get("copy_wallet") or "").lower(), db.get_trade_reconciliation_key(t)): t
                    for t in db.get_trades(status="open", limit=None, runtime_scope=runtime_scope)
                    if t.get("trade_type") == "copy" and db.get_trade_reconciliation_key(t) and t.get("copy_wallet")
                }
                # Track which (wallet, condition_id) tuples are still held this cycle
                live_position_keys = set()

                for address, label in {r["address"]: r["label"] for r in db.get_watched_wallets(active_only=True)}.items():
                    try:
                        positions = copy_scanner.get_positions(address)
                    except Exception as e:
                        log.warning("Copy: failed to fetch positions for %s: %s", label, e)
                        record_attempt(
                            level,
                            "copy",
                            "error",
                            "copy_positions_fetch_failed",
                            f"Copy positions fetch failed for {label}: {e}",
                            event=label,
                            wallet=address,
                            phase=current_stage,
                        )
                        _record_wallet_event(
                            source="autonomy",
                            wallet=address,
                            label=label,
                            event_type="wallet_polled",
                            status="fetch_failed",
                            reason_code="positions_fetch_failed",
                            reason=f"Positions fetch failed during autonomy cycle: {e}",
                            checked_at=now_ts,
                        )
                        continue

                    db.update_watched_wallet_poll_status(
                        address,
                        checked_at=now_ts,
                        positions_count=len([p for p in positions if p.get("conditionId")]),
                    )

                    # Forward-only: on first scan after adding a wallet, snapshot all
                    # existing positions as baseline and skip them. Only mirror NEW
                    # positions that appear in subsequent cycles.
                    baseline = db.get_wallet_baseline(address)
                    if baseline is None:
                        # First scan — record current positions as baseline, don't mirror
                        baseline_ids = sorted({
                            (db.get_position_identity(p, wallet=address)["canonical_ref"]
                             or db.get_position_identity(p, wallet=address)["condition_id"])
                            for p in positions
                            if p.get("conditionId")
                        })
                        db.set_wallet_baseline(address, baseline_ids)
                        log.info("Step 4e: Baseline set for %s — %d existing positions (skipped)",
                                 label, len(baseline_ids))
                        # Still track live IDs for close detection on OTHER wallets
                        for pos in positions:
                            identity = db.get_position_identity(pos, wallet=address)
                            reconciliation_key = identity["canonical_ref"] or identity["condition_id"]
                            if reconciliation_key:
                                live_position_keys.add((address.lower(), reconciliation_key))
                        _record_wallet_event(
                            source="autonomy",
                            wallet=address,
                            label=label,
                            event_type="baseline_set",
                            status="baseline_skipped",
                            reason=f"Baseline set from {len(baseline_ids)} existing positions during autonomy cycle.",
                            checked_at=now_ts,
                            positions_count=len(baseline_ids),
                            details={"baseline_positions": baseline_ids},
                        )
                        continue

                    wallet_opened = 0
                    for pos in positions:
                        cid = pos.get("conditionId", "")
                        if not cid:
                            continue
                        identity = db.get_position_identity(pos, wallet=address)
                        reconciliation_key = identity["canonical_ref"] or identity["condition_id"]
                        if reconciliation_key:
                            live_position_keys.add((address.lower(), reconciliation_key))

                        # Skip positions that existed before we started watching
                        if any(
                            key in baseline
                            for key in {
                                identity["canonical_ref"],
                                identity["external_position_id"],
                                identity["condition_id"],
                            }
                            if key
                        ):
                            continue

                        # New position — not yet mirrored
                        position_key = (address.lower(), reconciliation_key)
                        if position_key not in open_copy:
                            decision = db.inspect_copy_trade_open(
                                address,
                                pos,
                                size_usd=20,
                                max_wallet_open=max_copy_wallet_open,
                                max_total_open=max_copy_total_open,
                                runtime_scope=runtime_scope,
                            )
                            if not decision["ok"]:
                                record_attempt(
                                    level,
                                    "copy",
                                    "blocked",
                                    decision["reason_code"],
                                    decision["reason"],
                                    event=f"{label}: {pos.get('title','')}",
                                    token_id=pos.get("asset"),
                                    wallet=address,
                                    condition_id=cid,
                                    size_usd=20,
                                    phase=current_stage,
                                )
                                _record_wallet_event(
                                    source="autonomy",
                                    wallet=address,
                                    label=label,
                                    event_type="new_position",
                                    status="blocked",
                                    reason_code=decision["reason_code"],
                                    reason=decision["reason"],
                                    condition_id=cid,
                                    outcome_name=pos.get("outcome"),
                                    market_title=pos.get("title"),
                                    price=pos.get("curPrice"),
                                    position_value_usd=pos.get("currentValue"),
                                    checked_at=now_ts,
                                    positions_count=len([p for p in positions if p.get("conditionId")]),
                                )
                                journal({
                                    "action": "skip_trade",
                                    "level": level,
                                    "trade_type": "copy",
                                    "event": f"{label}: {pos.get('title','')[:50]}",
                                    "reason": decision["reason"],
                                })
                                log.info("Step 4e: Copy skip for %s — %s", label, decision["reason"])
                                continue
                            t_id = db.open_copy_trade(
                                address,
                                label,
                                pos,
                                size_usd=20,
                                max_wallet_open=max_copy_wallet_open,
                                max_total_open=max_copy_total_open,
                                runtime_scope=runtime_scope,
                            )
                            if t_id:
                                record_attempt(
                                    level,
                                    "copy",
                                    "allowed",
                                    "opened",
                                    "Paper copy trade opened.",
                                    event=f"{label}: {pos.get('title','')}",
                                    trade_id=t_id,
                                    token_id=pos.get("asset"),
                                    wallet=address,
                                    condition_id=cid,
                                    size_usd=20,
                                    phase=current_stage,
                                )
                                copy_opened += 1
                                wallet_opened += 1
                                open_copy[position_key] = {
                                    "id": t_id,
                                    "copy_wallet": address,
                                    "copy_condition_id": cid,
                                    "copy_label": label,
                                    "copy_outcome": pos.get("outcome"),
                                    "canonical_ref": identity["canonical_ref"],
                                    "external_position_id": identity["external_position_id"],
                                    "entry_price_a": decision.get("entry_price"),
                                    "size_usd": 20,
                                    "event": pos.get("title"),
                                }
                                journal({
                                    "action": "trade_opened",
                                    "level": level,
                                    "mode": "paper",
                                    "trade_id": t_id,
                                    "trade_type": "copy",
                                    "event": f"{label}: {pos.get('title','')[:50]}",
                                    "size_usd": 20,
                                    "reason": f"Copy {label} — {pos.get('outcome','')} @{pos.get('curPrice',0):.3f}",
                                })
                                _record_wallet_event(
                                    source="autonomy",
                                    wallet=address,
                                    label=label,
                                    event_type="new_position",
                                    status="mirrored",
                                    reason_code="opened",
                                    reason=f"Paper copy trade opened as trade #{t_id} during autonomy cycle.",
                                    condition_id=cid,
                                    outcome_name=pos.get("outcome"),
                                    market_title=pos.get("title"),
                                    price=pos.get("curPrice"),
                                    position_value_usd=pos.get("currentValue"),
                                    checked_at=now_ts,
                                    positions_count=len([p for p in positions if p.get("conditionId")]),
                                    details={"trade_id": t_id, "size_usd": 20},
                                )
                                log.info("Step 4e: Mirrored %s — %s %s @%.3f",
                                         label, pos.get("outcome"), pos.get("title","")[:40], pos.get("curPrice", 0))

                    if wallet_opened == 0:
                        _record_wallet_event(
                            source="autonomy",
                            wallet=address,
                            label=label,
                            event_type="wallet_polled",
                            status="no_change",
                            reason=f"Autonomy poll saw {len([p for p in positions if p.get('conditionId')])} live positions and no new copy actions.",
                            checked_at=now_ts,
                            positions_count=len([p for p in positions if p.get("conditionId")]),
                        )
                    else:
                        _record_wallet_event(
                            source="autonomy",
                            wallet=address,
                            label=label,
                            event_type="wallet_polled",
                            status="changes_seen",
                            reason=f"Autonomy poll mirrored {wallet_opened} new position(s) from {len([p for p in positions if p.get('conditionId')])} live positions.",
                            checked_at=now_ts,
                            positions_count=len([p for p in positions if p.get("conditionId")]),
                            details={"opened": wallet_opened},
                        )

                # Watched wallet has exited a position — close our mirror
                for position_key, trade in open_copy.items():
                    if position_key not in live_position_keys:
                        # Use entry price as exit (neutral P&L) — market may have resolved
                        pnl = db.close_trade(trade["id"], exit_price_a=trade["entry_price_a"],
                                             notes="auto-close: watched wallet exited position")
                        copy_closed += 1
                        journal({
                            "action": "trade_closed",
                            "trade_id": trade["id"],
                            "trade_type": "copy",
                            "pnl": pnl,
                            "reason": f"Watched wallet {trade.get('copy_label','')} exited position",
                        })
                        _record_wallet_event(
                            source="autonomy",
                            wallet=trade.get("copy_wallet"),
                            label=trade.get("copy_label"),
                            event_type="position_closed",
                            status="closed",
                            reason_code="wallet_exited_position",
                            reason=f"Mirrored trade #{trade['id']} closed because watched wallet exited the position.",
                            condition_id=trade.get("copy_condition_id"),
                            outcome_name=trade.get("copy_outcome"),
                            market_title=trade.get("event"),
                            position_value_usd=trade.get("size_usd"),
                            checked_at=now_ts,
                            details={"trade_id": trade["id"], "pnl": pnl},
                        )
                        log.info("Step 4e: Auto-closed copy trade %d (wallet exited) pnl=$%.2f",
                                 trade["id"], pnl or 0)

                if copy_opened or copy_closed:
                    log.info("Step 4e: Copy trader — %d opened, %d closed", copy_opened, copy_closed)
            except Exception as e:
                log.debug("Copy trader step skipped during %s: %s", current_stage, e)
                record_attempt(
                    level,
                    "copy",
                    "error",
                    "copy_trader_step_failed",
                    f"Copy trader step failed: {e}",
                    event="Copy trader",
                    phase=current_stage,
                )

        # Step 4f: Wallet discovery (every 6 hours)
        current_stage = "step 4f wallet discovery"
        if not paper_only_runtime(runtime_scope):
            log.info("Step 4f: Skipping paper-only wallet discovery for scope=%s", runtime_scope)
            journal({
                "action": "paper_only_step_skipped",
                "level": level,
                "runtime_scope": runtime_scope,
                "strategy": "wallet_discovery",
                "reason": f"Wallet discovery is paper-only and is disabled for scope={runtime_scope}.",
            })
        else:
            try:
                import wallet_discovery
                last_discovery = state.get("last_discovery", 0)
                if time.time() - last_discovery > 6 * 3600:
                    log.info("Step 4f: Running wallet discovery...")
                    result = wallet_discovery.run_discovery(auto_add=True, verbose=False)
                    state["last_discovery"] = time.time()
                    log.info("Step 4f: Discovery — %d candidates, %d auto-added",
                             result.get("candidates_pending", 0), result.get("auto_added", 0))
                else:
                    hrs = (time.time() - last_discovery) / 3600
                    log.info("Step 4f: Discovery skipped (ran %.1fh ago, next in %.1fh)",
                             hrs, 6 - hrs)
            except Exception as e:
                log.debug("Wallet discovery step skipped during %s: %s", current_stage, e)

        # Step 4g: Whale / insider detection
        current_stage = "step 4g whale scan"
        whale_alerts = []
        new_whale_ids = []
        try:
            import whale_detector
            whale_alerts, whale_stats = whale_detector.scan(min_score=60, verbose=False)
            for alert in whale_alerts:
                try:
                    row_id = db.save_whale_alert(alert)
                    if row_id:
                        new_whale_ids.append(row_id)
                except Exception:
                    pass
            if new_whale_ids:
                log.info("Step 4g: Whale scan — %d alerts (%d new) from %d markets",
                         len(whale_alerts), len(new_whale_ids), whale_stats["markets_checked"])
                journal({
                    "action": "whale_scan",
                    "level": level,
                    "alerts": len(whale_alerts),
                    "new_saved": len(new_whale_ids),
                    "markets_checked": whale_stats["markets_checked"],
                })
            else:
                log.debug("Step 4g: Whale scan — %d alerts, none new", len(whale_alerts))
        except Exception as e:
            log.debug("Whale scan step skipped during %s: %s", current_stage, e)

        # Step 4h: Whale execution (9x volume ratio trigger)
        current_stage = "step 4h whale execution"
        if config["can_trade"]:
            try:
                WHALE_VOLUME_RATIO_THRESHOLD = 9.0
                WHALE_MIN_SUSPICION_SCORE = 70
                WHALE_MAX_OPEN_TRADES = 2
                open_whale_trades = [t for t in open_trades if t.get("trade_type") == "whale"]
                whale_slots = max(0, WHALE_MAX_OPEN_TRADES - len(open_whale_trades))
                if whale_slots > 0 and new_whale_ids:
                    alerts_by_id = {alert.get("id"): alert for alert in whale_alerts if alert.get("id")}
                    candidates = []
                    for alert_id in new_whale_ids:
                        alert = alerts_by_id.get(alert_id)
                        if not alert:
                            continue
                        try:
                            ratio = float(alert.get("volume_ratio") or 0)
                        except (TypeError, ValueError):
                            ratio = 0
                        score = float(alert.get("suspicion_score") or 0)
                        if ratio >= WHALE_VOLUME_RATIO_THRESHOLD and score >= WHALE_MIN_SUSPICION_SCORE:
                            candidates.append(alert)
                    whale_mode = "paper" if paper_mode else "live"
                    whale_size = config.get("size_usd") or (3 if not paper_mode else 20)
                    for alert in candidates:
                        if whale_slots <= 0:
                            break
                        try:
                            result = execution.execute_whale_trade(
                                alert,
                                size_usd=whale_size,
                                mode=whale_mode,
                            )
                        except Exception as exc:
                            log.warning("Whale execution error: %s", exc)
                            record_attempt(
                                level,
                                "whale",
                                "error",
                                "execution_error",
                                f"Whale execution failed: {exc}",
                                whale_alert_id=alert.get("id"),
                                event=alert.get("event"),
                                phase=current_stage,
                            )
                            continue
                        if result.get("ok"):
                            whale_slots -= 1
                            journal({
                                "action": "whale_trade_opened",
                                "level": level,
                                "alert_id": alert.get("id"),
                                "trade_id": result.get("trade_id"),
                                "volume_ratio": alert.get("volume_ratio"),
                                "token_id": result.get("token_id"),
                            })
                            log.info(
                                "Step 4h: Whale trade opened for alert %s (trade=%s) size=$%.2f",
                                alert.get("id"),
                                result.get("trade_id"),
                                whale_size,
                            )
                        else:
                            reason_code = result.get("reason_code") or (result.get("decision") or {}).get("reason_code")
                            reason = result.get("error") or (result.get("decision") or {}).get("reason")
                            slippage_pct = (result.get("slippage") or {}).get("slippage_pct")
                            record_attempt(
                                level,
                                "whale",
                                "blocked",
                                reason_code or "unknown",
                                reason or "Whale trade blocked",
                                event=alert.get("event"),
                                whale_alert_id=alert.get("id"),
                                token_id=alert.get("token_id"),
                                size_usd=whale_size,
                                phase=current_stage,
                                details={
                                    "volume_ratio": alert.get("volume_ratio"),
                                    "slippage_pct": slippage_pct,
                                    "balance": result.get("balance", {}).get("balance_usd"),
                                },
                            )
                    if whale_slots <= 0 and candidates:
                        log.debug("Whale execution reached max open trades (%d)", WHALE_MAX_OPEN_TRADES)
            except Exception as exc:
                log.debug("Whale execution step failed: %s", exc)
                record_attempt(
                    level,
                    "whale",
                    "error",
                    "whale_execution_failed",
                    f"Whale execution step failed: {exc}",
                    phase=current_stage,
                )

        # Step 5: Check graduation eligibility
        current_stage = "step 5 graduation and state save"
        perf = get_performance(state)
        can_graduate, report = check_graduation(state)
        if can_graduate:
            log.info("GRADUATION ELIGIBLE — %s can advance!", config["name"])
            journal({
                "action": "graduation_eligible",
                "level": level,
                "performance": perf,
                "report": report,
            })

        save_state(state)
        log.info(
            "=== Cycle complete: level=%s scope=%s runtime=%s trades=%d open=%d pnl=$%.2f ===",
            level,
            runtime_scope,
            runtime_tag,
            perf["total_trades"],
            open_count + traded,
            perf["total_pnl"],
        )
        return _finish_cycle()
    except Exception:
        log.exception("Autonomy cycle failed during %s", current_stage)
        record_attempt(
            level,
            "system",
            "error",
            "autonomy_cycle_failed",
            f"Autonomy cycle failed during {current_stage}.",
            event="Autonomy cycle",
            phase=current_stage,
        )
        raise


# --- Status Report ---

def print_status(state):
    """Print detailed status report."""
    level = state["level"]
    config = get_level_config(level, state.get("runtime_scope"))
    perf = get_performance(state)
    can_graduate, report = check_graduation(state)

    next_levels = list(LEVELS.keys())
    current_idx = next_levels.index(level)
    next_level = next_levels[current_idx + 1] if current_idx < len(next_levels) - 1 else None

    print(f"""
{'='*60}
  AUTONOMOUS TRADER STATUS
{'='*60}

  Runtime:      {state.get('runtime_scope', runtime_scope_for_level(level))}
  Level:        {config['name']} ({level})
  Description:  {config['description']}
  Trade Size:   {'$' + str(config['size_usd']) if config.get('size_usd') else 'Kelly-sized'}
  Max Open:     {config.get('max_open') if config.get('max_open') is not None else 'No hard cap (cash-limited)'}

  PERFORMANCE AT THIS LEVEL
  ─────────────────────────
  Trades:       {perf['total_trades']}
  Wins:         {perf['wins']} ({perf['win_rate']}%)
  Losses:       {perf['losses']}
  Total P&L:    ${perf['total_pnl']:.2f}
  Sharpe:       {perf['sharpe']:.2f}
""")

    if next_level:
        next_config = LEVELS[next_level]
        grad = config.get("graduation", {})
        print(f"""  GRADUATION TO {next_config['name'].upper()}
  ─────────────────────────""")
        if grad:
            print(f"  Min trades:   {perf['total_trades']}/{grad['min_trades']}")
            print(f"  Min win rate: {perf['win_rate']}%/{grad['min_win_rate']}%")
            print(f"  Min P&L:      ${perf['total_pnl']:.2f}/${grad['min_total_pnl']}")
            print(f"  Min Sharpe:   {perf['sharpe']:.2f}/{grad['min_sharpe']}")
            print(f"\n  {'READY TO PROMOTE' if can_graduate else 'NOT YET — keep trading'}")
        print()
    else:
        print("  Already at top level.\n")

    # Recent journal entries
    if JOURNAL_FILE.exists():
        lines = JOURNAL_FILE.read_text().strip().split("\n")
        recent = lines[-10:]
        print(f"  RECENT JOURNAL ({len(lines)} total entries)")
        print("  ─────────────────────────")
        for line in recent:
            try:
                e = json.loads(line)
                ts = e.get("timestamp", "?")[:16]
                act = e.get("action", "?")
                reason = e.get("reason", "")[:50]
                print(f"  {ts} | {act:20s} | {reason}")
            except Exception:
                pass
    print(f"\n{'='*60}")


def promote(state):
    """Promote to next level (with safety checks)."""
    level = state["level"]
    next_levels = list(LEVELS.keys())
    current_idx = next_levels.index(level)

    if current_idx >= len(next_levels) - 1:
        print("Already at top level (book).")
        return state

    next_level = next_levels[current_idx + 1]
    next_config = LEVELS[next_level]

    can_graduate, report = check_graduation(state)

    perf = get_performance(state)
    print(f"\nCurrent: {LEVELS[level]['name']} → Next: {next_config['name']}")
    print(f"Performance: {perf['total_trades']} trades, {perf['win_rate']}% win rate, ${perf['total_pnl']:.2f} P&L")
    print(f"\nGraduation check:\n{report}")

    if next_level in ("penny", "book"):
        print(f"\n{'!'*60}")
        print(f"  WARNING: {next_config['name']} uses REAL MONEY (${next_config.get('size_usd', 'Kelly')} per trade)")
        print(f"  Requires POLYMARKET_PRIVATE_KEY in the macOS Keychain or an explicit env override")
        print(f"{'!'*60}")

    if not can_graduate:
        print(f"\nNot ready yet. Meet all graduation criteria first.")
        return state

    confirm = input(f"\nPromote to {next_config['name']}? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Promotion cancelled.")
        return state

    # Reset level counters
    state["level"] = next_level
    state["promoted_at"] = datetime.now().isoformat()
    state["trades_at_level"] = 0
    state["wins_at_level"] = 0
    state["losses_at_level"] = 0
    state["pnl_at_level"] = 0.0
    state["returns_at_level"] = []

    save_state(state)
    journal({
        "action": "promoted",
        "from_level": level,
        "to_level": next_level,
        "reason": f"Graduated: {perf['total_trades']} trades, {perf['win_rate']}% win, ${perf['total_pnl']:.2f} pnl",
        "performance_at_promotion": perf,
    })

    print(f"\nPromoted to {next_config['name']}!")
    print(f"Trade size: ${next_config.get('size_usd', 'Kelly-sized')}")
    next_max_open = next_config['max_open'] if next_config.get('max_open') is not None else 'No hard cap (cash-limited)'
    print(f"Max open positions: {next_max_open}")
    return state


def print_journal(n=20):
    """Print last N journal entries."""
    if not JOURNAL_FILE.exists():
        print("No journal entries yet.")
        return

    lines = JOURNAL_FILE.read_text().strip().split("\n")
    recent = lines[-n:]

    print(f"\n{'='*60}")
    print(f"  TRADING JOURNAL (last {len(recent)} of {len(lines)} entries)")
    print(f"{'='*60}\n")

    for line in recent:
        try:
            e = json.loads(line)
            ts = e.get("timestamp", "?")[:19]
            act = e.get("action", "?")
            level = e.get("level", "")
            reason = e.get("reason", "")

            # Format based on action type
            if act == "trade_opened":
                pnl_str = f"${e.get('size_usd', 0):.0f}"
                print(f"  {ts} [{level:5s}] OPEN  {pnl_str:>6s} | {e.get('event', '')[:40]}")
                print(f"           grade={e.get('grade','?')} z={e.get('z_score',0):+.2f} ev={e.get('ev_pct',0):.1f}%")
            elif act == "trade_closed":
                pnl = e.get("pnl_usd", 0)
                marker = "+" if pnl >= 0 else ""
                print(f"  {ts} [{level:5s}] CLOSE {marker}${pnl:.2f} | {reason}")
            elif act == "promoted":
                print(f"  {ts} PROMOTED: {e.get('from_level','')} → {e.get('to_level','')}")
            elif act == "graduation_eligible":
                print(f"  {ts} READY TO GRADUATE!")
            elif act == "scan_complete":
                print(f"  {ts} [{level:5s}] SCAN  {e.get('total_signals',0)} signals, {e.get('tradeable',0)} tradeable")
            elif act in ("skip_trade", "trade_rejected"):
                print(f"  {ts} [{level:5s}] SKIP  {reason[:50]}")
            else:
                print(f"  {ts} [{level:5s}] {act:12s} {reason[:50]}")
        except Exception:
            pass

    print(f"\n{'='*60}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Autonomous Polymarket trader")
    parser.add_argument("--runtime-scope", choices=[RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY],
                        help="Select isolated runtime scope")
    parser.add_argument("--level", choices=list(LEVELS.keys()),
                        help="Override trading level")
    parser.add_argument("--status", action="store_true",
                        help="Show performance and graduation readiness")
    parser.add_argument("--promote", action="store_true",
                        help="Promote to next level")
    parser.add_argument("--journal", action="store_true",
                        help="Show trading journal")
    parser.add_argument("--journal-n", type=int, default=20,
                        help="Number of journal entries to show")
    parser.add_argument("--reset", action="store_true",
                        help="Reset level counters (keeps level)")
    args = parser.parse_args()

    # Override level if specified
    state = load_state(runtime_scope=args.runtime_scope)
    if args.level:
        state["level"] = args.level
        state["runtime_scope"] = runtime_scope_for_level(args.level)
        save_state(state, runtime_scope=state["runtime_scope"])
        log.info("Level set to: %s", args.level)

    if args.status:
        print_status(state)
        return

    if args.promote:
        state = promote(state)
        return

    if args.journal:
        print_journal(args.journal_n)
        return

    if args.reset:
        state["trades_at_level"] = 0
        state["wins_at_level"] = 0
        state["losses_at_level"] = 0
        state["pnl_at_level"] = 0.0
        state["returns_at_level"] = []
        save_state(state, runtime_scope=state.get("runtime_scope"))
        print(f"Level counters reset for {state['level']}")
        return

    # Run the autonomous cycle
    if args.runtime_scope:
        run_cycle(state)
        return

    scopes = background_runtime_scopes()
    if not scopes:
        log.warning(
            "Autonomy scheduler disabled because %s resolved to no enabled scopes",
            BACKGROUND_SCOPES_ENV,
        )
        return

    log.info(
        "Autonomy scheduler scopes=%s via %s",
        ",".join(scopes),
        BACKGROUND_SCOPES_ENV,
    )
    for scope in scopes:
        scoped_state = load_state(runtime_scope=scope)
        run_cycle(scoped_state)


if __name__ == "__main__":
    main()
