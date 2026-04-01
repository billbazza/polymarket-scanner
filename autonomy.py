#!/usr/bin/env python3
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
from dotenv import load_dotenv
load_dotenv()

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from log_setup import init_logging
init_logging()
log = logging.getLogger("scanner.autonomy")

import asyncio
import db
import scanner
import async_scanner
import tracker
import execution
import math_engine
import cointegration_trial

# --- Configuration ---

STATE_FILE = Path(__file__).parent / "logs" / "autonomy_state.json"
LEGACY_STATE_FILE = Path(__file__).parent / "autonomy_state.json"
JOURNAL_FILE = Path(__file__).parent / "logs" / "journal.jsonl"

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


# --- State Management ---

def default_state():
    """Return the default autonomy state."""
    return {
        "level": "scout",
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
    merged = default_state()
    if isinstance(state, dict):
        merged.update(state)
    return merged


def load_state():
    """Load autonomy state from disk, migrating the legacy repo-root file if needed."""
    state = _read_state_file(STATE_FILE)
    if state is not None:
        return _normalize_state(state)

    legacy_state = _read_state_file(LEGACY_STATE_FILE)
    if legacy_state is not None:
        state = _normalize_state(legacy_state)
        save_state(state)
        log.info("Migrated autonomy state from %s to %s", LEGACY_STATE_FILE, STATE_FILE)
        return state

    return default_state()


def save_state(state):
    """Persist state to disk."""
    STATE_FILE.parent.mkdir(exist_ok=True)
    tmp_path = STATE_FILE.with_suffix(f"{STATE_FILE.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2))
    tmp_path.replace(STATE_FILE)


def journal(entry):
    """Append a decision to the journal (append-only log)."""
    entry["timestamp"] = datetime.now().isoformat()
    JOURNAL_FILE.parent.mkdir(exist_ok=True)
    with open(JOURNAL_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("JOURNAL: %s — %s", entry.get("action", "?"), entry.get("reason", "")[:80])


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
    level = state["level"]
    config = LEVELS[level]

    log.info("=== Autonomy cycle: level=%s (%s) ===", level, config["name"])

    # Step 1: Scan for new signals (use fast async scanner, ~5x faster)
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
            return state

    opportunities = scan_result["opportunities"]
    scan_duration = round(time.time() - scan_started, 1)

    # Save scan run
    db.save_scan_run(pairs_tested=scan_result["pairs_tested"], cointegrated=scan_result["pairs_cointegrated"],
                     opportunities=len(opportunities), duration=scan_duration)

    paper_mode = level == "paper"
    trial_settings = cointegration_trial.get_trial_settings()
    admitted_signals = []
    a_trial_candidates = 0
    a_trial_eligible = 0
    a_trial_rejected = 0
    rejection_counts = {}
    for opp in opportunities:
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
    new_signal_ids = []
    for opp in opportunities:
        try:
            sid = db.save_signal(opp)
            opp["id"] = sid
            new_signal_ids.append(sid)
        except Exception as e:
            log.warning("Failed to save signal: %s", e)

    tradeable = [o for o in opportunities if o.get("tradeable")]
    log.info(
        "Scan found %d signals, %d A+ tradeable, %d admitted for this level",
        len(opportunities),
        len(tradeable),
        len(admitted_signals),
    )
    log.info(
        "Cointegration A-trial status: enabled=%s paper_only=%s candidates=%d eligible=%d rejected=%d",
        trial_settings["enabled"],
        trial_settings["paper_only"],
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
        "signal_ids": new_signal_ids,
    })

    # Step 2: Monitor existing positions
    log.info("Step 2: Monitoring open trades...")
    try:
        updates = tracker.refresh_open_trades()
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
        log.warning("Trade monitoring failed: %s", e)

    # Step 2b: Manage pending maker orders (check fills, cancel expired)
    try:
        order_result = execution.manage_open_orders()
        if order_result["filled"] or order_result["cancelled"]:
            log.info("Step 2b: maker orders — %d filled, %d cancelled",
                     order_result["filled"], order_result["cancelled"])
    except Exception as e:
        log.warning("Maker order management failed: %s", e)

    # Step 3: Auto-close reverted trades
    log.info("Step 3: Checking for auto-closes...")
    try:
        closed = tracker.auto_close_trades(z_threshold=0.5)
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
        log.warning("Auto-close failed: %s", e)

    # Step 4: Open new trades (if allowed at this level)
    if not config["can_trade"]:
        log.info("Step 4: SCOUT mode — not trading")
        journal({"action": "scout_only", "level": level,
                 "reason": "Level does not permit trading"})
        save_state(state)
        return state

    open_trades = db.get_trades(status="open", limit=None)
    open_count = len(open_trades)
    max_open = config["max_open"]

    if max_open is not None and open_count >= max_open:
        log.info("Step 4: At max positions (%d/%d), skipping new trades",
                 open_count, max_open)
        journal({"action": "skip_trade", "level": level,
                 "reason": f"At max positions ({open_count}/{max_open})"})
        save_state(state)
        return state

    # Determine trade size
    if level == "book":
        # Kelly-sized from bankroll
        bankroll = config.get("bankroll", 1000)
    else:
        size_usd = config["size_usd"]

    slots = (max_open - open_count) if max_open is not None else len(admitted_signals)
    traded = 0

    # Build dedup sets from currently open trades — keyed by signal_id and event name
    open_signal_ids = {t.get("signal_id") for t in open_trades if t.get("signal_id")}
    open_events = {t.get("event", "") for t in open_trades}
    # Also track what we open within this cycle so we don't double-open
    this_cycle_signal_ids = set()
    this_cycle_events = set()

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
            continue

        if event_name in open_events or event_name in this_cycle_events:
            log.info("  Skip: already have position in '%s'", event_name[:40])
            journal({"action": "skip_trade", "level": level,
                     "reason": f"Already trading event: {event_name[:40]}"})
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

        if paper_mode and opp.get("admission_path") == "paper_a_trial":
            trial_size = opp.get("trial_recommended_size_usd")
            if trial_size:
                trade_size = min(trade_size, trial_size) if trade_size else trial_size

        # Execute
        mode = "paper" if level in ("paper", "scout") else "live"

        # Step 4.5: Brain validation (if available)
        # Ask Claude + Perplexity if this signal makes sense in the real world
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
                continue
            log.info("  Brain VALIDATED trade: %s", brain_reasoning)
            opp["brain_reasoning"] = brain_reasoning
        except Exception as e:
            log.warning("  Brain validation failed (defaulting to math-only): %s", e)

        log.info("  Opening %s trade: %s | $%.2f", mode, event_name[:40], trade_size)

        try:
            result = execution.execute_trade(opp, size_usd=trade_size, mode=mode)
            if result["ok"]:
                traded += 1
                this_cycle_signal_ids.add(signal_id)
                this_cycle_events.add(event_name)
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
                    "admission_path": opp.get("admission_path"),
                    "experiment_status": opp.get("experiment_status"),
                    "reason": opp.get("experiment_reason") or f"Signal admitted, z={opp.get('z_score', 0):+.2f}",
                })
            else:
                journal({
                    "action": "trade_rejected",
                    "level": level,
                    "event": event_name[:60],
                    "grade": opp.get("grade_label", "?"),
                    "admission_path": opp.get("admission_path"),
                    "reason": result.get("error", "unknown"),
                })
        except Exception as e:
            log.error("  Trade execution failed: %s", e)
            journal({"action": "trade_error", "level": level,
                     "event": event_name[:60], "reason": str(e)})

    log.info("Step 4: Opened %d new trades", traded)

    for code, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0])):
        log.info("A-trial rejection summary: %s=%d", code, count)

    # Step 4b: Open weather trades (if slots remain)
    open_trades = db.get_trades(status="open", limit=None)
    open_count = len(open_trades)
    slots_remaining = (max_open - open_count) if max_open is not None else None

    if slots_remaining is None or slots_remaining > 0:
        try:
            import weather_scanner
            weather_opps, _ = weather_scanner.scan(min_edge=0.06, verbose=False)
            tradeable_weather = [o for o in weather_opps if o.get("tradeable")]
            weather_traded = 0
            candidates = tradeable_weather if slots_remaining is None else tradeable_weather[:slots_remaining]
            for w_opp in candidates:
                try:
                    w_id = db.save_weather_signal(w_opp)
                    trade_size = size_usd if level != "book" else 20
                    decision = db.inspect_weather_trade_open(
                        w_id,
                        size_usd=trade_size,
                        max_total_open=max_open,
                    )
                    if not decision["ok"]:
                        journal({
                            "action": "skip_trade",
                            "level": level,
                            "trade_type": "weather",
                            "event": w_opp.get("event", w_opp.get("market", ""))[:60],
                            "reason": decision["reason"],
                        })
                        continue
                    t_id = db.open_weather_trade(w_id, size_usd=trade_size)
                    if t_id:
                        weather_traded += 1
                        journal({
                            "action": "trade_opened",
                            "level": level,
                            "mode": "paper",
                            "trade_id": t_id,
                            "signal_id": w_id,
                            "trade_type": "weather",
                            "event": w_opp.get("event", w_opp.get("market", ""))[:60],
                            "size_usd": trade_size,
                            "reason": f"Weather edge {w_opp.get('combined_edge_pct', 0):+.1f}%",
                        })
                except Exception as e:
                    log.warning("Weather trade open failed: %s", e)
            if weather_traded:
                log.info("Step 4b: Opened %d weather trades", weather_traded)
        except Exception as e:
            log.debug("Weather scan skipped: %s", e)

    # Step 4c: Longshot bias scanner (scan for NO maker opportunities on 3–15¢ markets)
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
        log.debug("Longshot scan step skipped: %s", e)

    # Step 4d: Near-certainty scanner (85–99¢ YES markets with calibration edge)
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
        log.debug("Near-certainty scan step skipped: %s", e)

    # Step 4e: Auto-mirror copy trader positions
    try:
        import copy_scanner
        copy_trade_settings = db.get_copy_trade_settings()
        max_copy_wallet_open = copy_trade_settings["per_wallet_cap"] if copy_trade_settings["cap_enabled"] else None
        max_copy_total_open = copy_trade_settings["total_open_cap"] if copy_trade_settings["cap_enabled"] else None
        copy_opened = 0
        copy_closed = 0

        # Build index of currently open copy trades: condition_id → trade
        open_copy = {
            t["copy_condition_id"]: t
            for t in db.get_trades(status="open", limit=None)
            if t.get("trade_type") == "copy" and t.get("copy_condition_id")
        }
        # Track which condition_ids are still held by watched wallets this cycle
        live_condition_ids = set()

        for address, label in {r["address"]: r["label"] for r in db.get_watched_wallets(active_only=True)}.items():
            try:
                positions = copy_scanner.get_positions(address)
            except Exception as e:
                log.warning("Copy: failed to fetch positions for %s: %s", label, e)
                continue

            # Forward-only: on first scan after adding a wallet, snapshot all
            # existing positions as baseline and skip them. Only mirror NEW
            # positions that appear in subsequent cycles.
            baseline = db.get_wallet_baseline(address)
            if baseline is None:
                # First scan — record current positions as baseline, don't mirror
                baseline_ids = [p.get("conditionId", "") for p in positions if p.get("conditionId")]
                db.set_wallet_baseline(address, baseline_ids)
                log.info("Step 4e: Baseline set for %s — %d existing positions (skipped)",
                         label, len(baseline_ids))
                # Still track live IDs for close detection on OTHER wallets
                for pos in positions:
                    cid = pos.get("conditionId", "")
                    if cid:
                        live_condition_ids.add(cid)
                continue

            for pos in positions:
                cid = pos.get("conditionId", "")
                if not cid:
                    continue
                live_condition_ids.add(cid)

                # Skip positions that existed before we started watching
                if cid in baseline:
                    continue

                # New position — not yet mirrored
                if cid not in open_copy:
                    decision = db.inspect_copy_trade_open(
                        address,
                        pos,
                        size_usd=20,
                        max_wallet_open=max_copy_wallet_open,
                        max_total_open=max_copy_total_open,
                    )
                    if not decision["ok"]:
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
                    )
                    if t_id:
                        copy_opened += 1
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
                        log.info("Step 4e: Mirrored %s — %s %s @%.3f",
                                 label, pos.get("outcome"), pos.get("title","")[:40], pos.get("curPrice", 0))

        # Watched wallet has exited a position — close our mirror
        for cid, trade in open_copy.items():
            if cid not in live_condition_ids:
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
                log.info("Step 4e: Auto-closed copy trade %d (wallet exited) pnl=$%.2f",
                         trade["id"], pnl or 0)

        if copy_opened or copy_closed:
            log.info("Step 4e: Copy trader — %d opened, %d closed", copy_opened, copy_closed)
    except Exception as e:
        log.debug("Copy trader step skipped: %s", e)

    # Step 4f: Wallet discovery (every 6 hours)
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
        log.debug("Wallet discovery step skipped: %s", e)

    # Step 4g: Whale / insider detection
    try:
        import whale_detector
        whale_alerts, whale_stats = whale_detector.scan(min_score=60, verbose=False)
        new_whale_ids = []
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
        log.debug("Whale scan step skipped: %s", e)

    # Step 5: Check graduation eligibility
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
    log.info("=== Cycle complete: level=%s trades=%d open=%d pnl=$%.2f ===",
             level, perf["total_trades"], open_count + traded, perf["total_pnl"])
    return state


# --- Status Report ---

def print_status(state):
    """Print detailed status report."""
    level = state["level"]
    config = LEVELS[level]
    perf = get_performance(state)
    can_graduate, report = check_graduation(state)

    next_levels = list(LEVELS.keys())
    current_idx = next_levels.index(level)
    next_level = next_levels[current_idx + 1] if current_idx < len(next_levels) - 1 else None

    print(f"""
{'='*60}
  AUTONOMOUS TRADER STATUS
{'='*60}

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
        print(f"  Requires POLYMARKET_PRIVATE_KEY in .env")
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

    state = load_state()

    # Override level if specified
    if args.level:
        state["level"] = args.level
        save_state(state)
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
        save_state(state)
        print(f"Level counters reset for {state['level']}")
        return

    # Run the autonomous cycle
    run_cycle(state)


if __name__ == "__main__":
    main()
