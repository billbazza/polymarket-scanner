"""Paper trading engine — monitors open trades, updates prices, manages P&L."""
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

import requests

import api
import db
import journal_writer
import weather_guard_state

log = logging.getLogger("scanner.tracker")
WEATHER_STOP_LOSS_PCT = 0.18
WEATHER_MAX_HOLD_HOURS = 72
_WARN_TTL_SECS = 6 * 60 * 60
_LAST_WARNINGS = {}
WHALE_MAX_DRAWNDOWN_USD = 15.0
WHALE_MAX_HOLD_SECS = 48 * 3600
WHALE_VOLATILITY_DROP_PCT = 0.15
WHALE_AGGREGATE_ALERT_DRAWDOWN_USD = 50.0
WHALE_AGGREGATE_ALERT_DRAWDOWN_USD = 50.0
_STOP_CONTEXTS_FILE = Path(__file__).resolve().parent / "reports" / "diagnostics" / "weather-stop-contexts.jsonl"


def refresh_open_trades():
    """Update current prices for all open trades and save snapshots.

    Handles both pairs trades (two-leg spread) and weather trades (single-leg).

    Returns:
        list of dicts with trade_id, current prices, and unrealized P&L.
    """
    trades = db.get_trades(status="open", limit=None)
    if not trades:
        log.debug("No open trades to refresh")
        return []

    updates = []
    for trade in trades:
        trade_id   = trade["id"]
        trade_type = trade.get("trade_type") or "pairs"

        if trade_type in {"weather", "copy", "whale"}:
            updates += _refresh_weather_trade(trade)
        else:
            updates += _refresh_pairs_trade(trade)

    log.info("Refreshed %d/%d open trades", len(updates), len(trades))
    return updates


def _trade_kind(trade):
    return trade.get("trade_type") or "weather"


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_weather_stop_context(trade, signal, entry_price, current_price, stop_floor):
    observation = {}
    if signal and signal.get("correction"):
        observation = signal["correction"].get("observation") or {}

    observed_temp = _safe_float(observation.get("temp_f"))
    previous_temp = _safe_float(observation.get("previous_temp_f"))
    observed_hour = _safe_float(observation.get("observed_hour_local"))
    previous_hour = _safe_float(observation.get("previous_hour_local"))
    trend_f_per_hour = _safe_float(observation.get("trend_f_per_hour"))
    lookback_hours = (
        round(observed_hour - previous_hour, 2)
        if observed_hour is not None and previous_hour is not None
        else None
    )

    context = {
        "signal_id": trade.get("weather_signal_id"),
        "entry_price": entry_price,
        "stop_floor": stop_floor,
        "current_price": current_price,
        "hours_ahead": signal.get("hours_ahead") if signal else None,
        "edge_pct": signal.get("combined_edge_pct") if signal else None,
        "liquidity": signal.get("liquidity") if signal else None,
        "observation": {
            "source": observation.get("source"),
            "observed_at": observation.get("observed_at"),
            "observed_temp": observed_temp,
            "previous_temp": previous_temp,
            "observed_hour": observed_hour,
            "previous_hour": previous_hour,
            "lookback_hours": lookback_hours,
            "trend_f_per_hour": trend_f_per_hour,
        },
    }
    return context


def _record_weather_stop_context(trade, context, reason):
    token_id = trade.get("token_id_a")
    if not token_id:
        return
    payload = {
        "token_id": token_id,
        "trade_id": trade.get("id"),
        "signal_id": trade.get("weather_signal_id"),
        "reason": reason,
        "context": context,
        "logged_at": datetime.now().isoformat(),
    }
    try:
        _STOP_CONTEXTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_STOP_CONTEXTS_FILE, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception as exc:
        log.warning("Failed to persist weather stop context for %s: %s", token_id, exc)
    journal_writer.append_entry({
        "action": "weather_stop_loss",
        "token_id": token_id,
        "trade_id": trade.get("id"),
        "signal_id": trade.get("weather_signal_id"),
        "reason": reason,
        "context": context,
    })


def _trade_label(trade):
    return f"{_trade_kind(trade)} trade {trade['id']}"


def _log_once(level, key, message, *args, ttl_secs=_WARN_TTL_SECS):
    now = time.time()
    if now - _LAST_WARNINGS.get(key, 0) < ttl_secs:
        return
    _LAST_WARNINGS[key] = now
    getattr(log, level)(message, *args)


def _set_tracker_note(trade_id, note):
    trade = db.get_trade(trade_id)
    if trade and trade.get("notes") != note:
        db.update_trade_notes(trade_id, note)


def _load_weather_signal(trade):
    sig_id = trade.get("weather_signal_id")
    if not sig_id:
        return None
    return db.get_weather_signal_by_id(sig_id)


def _get_single_leg_market(trade):
    trade_type = _trade_kind(trade)
    token_id = api.normalize_token_id(trade.get("token_id_a"))

    if trade_type == "weather":
        signal = _load_weather_signal(trade)
        if signal:
            if signal.get("market_id"):
                market = api.get_market(market_id=signal["market_id"])
                if market:
                    return market
            if token_id:
                return api.get_market(token_id=token_id)
            return None

    if trade_type == "copy" and trade.get("copy_condition_id"):
        market = api.get_market(condition_id=trade["copy_condition_id"])
        if market:
            return market

    if token_id:
        return api.get_market(token_id=token_id)
    return None


def _market_is_resolved(market):
    if not market:
        return False
    status = (market.get("umaResolutionStatus") or "").lower()
    return status in {"resolved", "settled"} or (
        market.get("closed") and not market.get("acceptingOrders", True)
    )


def _weather_target_passed(trade):
    signal = _load_weather_signal(trade)
    target_date = signal.get("target_date") if signal else None
    if not target_date:
        return False
    try:
        return date.fromisoformat(target_date) < date.today()
    except ValueError:
        return False


def _resolve_single_leg_price(trade, phase):
    trade_id = trade["id"]
    raw_token_a = trade.get("token_id_a")
    token_a = api.normalize_token_id(raw_token_a)
    label = _trade_label(trade)
    if not raw_token_a:
        _log_once("warning", (trade_id, phase, "missing-token"),
                  "%s missing token_id_a; cannot %s", label, phase)
        _set_tracker_note(trade_id, f"Tracker: missing token_id_a; cannot {phase}.")
        return {"price": None, "source": "missing-token", "resolved": False, "unpriceable": True}
    if not token_a:
        _log_once("info", (trade_id, phase, "invalid-token"),
                  "%s has invalid token_id_a; skipping pricing during %s",
                  label, phase)
        _set_tracker_note(
            trade_id,
            "Tracker: invalid token_id_a placeholder; skipping midpoint/Gamma pricing and leaving trade open.",
        )
        return {
            "price": None,
            "source": "invalid-token",
            "resolved": False,
            "unpriceable": True,
            "unpriceable_reason": "invalid_token_id",
        }

    try:
        current_a = api.get_midpoint(token_a)
        if current_a > 0:
            return {"price": current_a, "source": "midpoint", "resolved": False}
    except requests.HTTPError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code != 404:
            _log_once("warning", (trade_id, phase, f"http-{status_code}"),
                      "Failed to fetch price for %s during %s: %s", label, phase, e)
            return None
    except Exception as e:
        _log_once("warning", (trade_id, phase, "midpoint-error"),
                  "Failed to fetch price for %s during %s: %s", label, phase, e)
        return None

    try:
        market = _get_single_leg_market(trade)
    except Exception as e:
        _log_once("warning", (trade_id, phase, "gamma-lookup-error"),
                  "%s midpoint 404 and Gamma lookup failed during %s: %s",
                  label, phase, e)
        return None

    if not market:
        _log_once("info", (trade_id, phase, "gamma-missing"),
                  "%s midpoint 404 and no Gamma market match found during %s",
                  label, phase)
        _set_tracker_note(
            trade_id,
            "Tracker: midpoint 404 and no Gamma market match found; trade remains open and unpriceable.",
        )
        return {
            "price": None,
            "source": "gamma",
            "resolved": False,
            "unpriceable": True,
            "unpriceable_reason": "gamma_market_missing",
        }

    gamma_price = api.extract_market_price(market, token_a)
    resolved = _market_is_resolved(market)
    if gamma_price is not None:
        if resolved:
            _set_tracker_note(
                trade_id,
                f"Tracker: used Gamma final price {gamma_price:.4f} after midpoint 404 (market resolved).",
            )
            _log_once("info", (trade_id, phase, "gamma-resolved"),
                      "%s midpoint 404; using Gamma final price %.4f during %s",
                      label, gamma_price, phase)
            return {"price": gamma_price, "source": "gamma", "resolved": True}
        if gamma_price > 0:
            _log_once("info", (trade_id, phase, "gamma-live"),
                      "%s midpoint 404; using Gamma fallback price %.4f during %s",
                      label, gamma_price, phase)
            return {"price": gamma_price, "source": "gamma", "resolved": False}

    if _trade_kind(trade) == "weather" and (_weather_target_passed(trade) or resolved):
        _set_tracker_note(
            trade_id,
            "Tracker: weather market unpriceable after target date; awaiting final resolution.",
        )
        _log_once("info", (trade_id, phase, "awaiting-resolution"),
                  "%s midpoint 404/unpriceable during %s; weather market is past target date or awaiting settlement",
                  label, phase)
        return {"price": None, "source": "gamma", "resolved": False, "awaiting_resolution": True}

    _set_tracker_note(trade_id, "Tracker: midpoint 404 and no usable fallback price; leaving trade open.")
    _log_once("info", (trade_id, phase, "unpriceable"),
              "%s midpoint 404 and no usable fallback price during %s; leaving trade open",
              label, phase)
    return {
        "price": None,
        "source": "gamma",
        "resolved": resolved,
        "unpriceable": True,
        "unpriceable_reason": "gamma_price_missing",
    }


def _refresh_weather_trade(trade):
    """Refresh a single-leg trade. Used for weather, copy, and whale positions."""
    trade_id = trade["id"]
    price_state = _resolve_single_leg_price(trade, "refresh")
    if not price_state or price_state.get("price") is None:
        return []
    current_a = db._normalize_probability_price(price_state["price"])
    if current_a is None:
        log.warning("Ignoring invalid single-leg price for trade %d: %r", trade_id, price_state["price"])
        return []

    valuation = db.calculate_single_leg_mark_to_market(
        trade["size_usd"],
        trade["entry_price_a"],
        current_a,
    )
    if not valuation["ok"]:
        log.warning(
            "Cannot mark single-leg trade %d: entry=%r current=%r size=%r",
            trade_id,
            trade.get("entry_price_a"),
            current_a,
            trade.get("size_usd"),
        )
        return []

    try:
        db.save_snapshot(trade_id, current_a, None, None, None)
        log.debug("Snapshot weather: trade=%d price=%.4f pnl=$%.2f",
                  trade_id, current_a, valuation["pnl_usd"])
    except Exception as e:
        log.error("Failed to save snapshot for weather trade %d: %s", trade_id, e)

    return [{
        "trade_id":        trade_id,
        "trade_type":      trade.get("trade_type") or "weather",
        "action":          trade.get("side_a", ""),
        "event":           trade.get("event", ""),
        "entry_price_a":   trade["entry_price_a"] or 0,
        "current_price_a": current_a,
        "price_source":    price_state.get("source"),
        "unrealized_pnl":  valuation,
    }]


def _pairs_pnl(entry_a, exit_a, entry_b, exit_b, side_a, size_usd):
    """Shares-based P&L for a two-leg pairs trade.

    size_usd is split equally (size/2 per leg). Each leg's dollar P&L is:
        shares = (size/2) / entry_price
        pnl    = shares × price_change_in_our_favour

    This correctly handles cheap tokens (e.g. 4¢ entry → 250 shares × price move)
    vs expensive ones (e.g. 90¢ entry → 5.5 shares × price move). The old
    arithmetic formula (price_diff × size_usd) under/over-stated P&L by up to
    10× when the two legs had very different entry prices.
    """
    if entry_a <= 0 or entry_b <= 0:
        return {"pnl_usd": 0.0, "pnl_pct": 0.0}

    half = size_usd / 2
    shares_a = half / entry_a
    shares_b = half / entry_b

    if side_a == "BUY":
        pnl_a = shares_a * (exit_a - entry_a)   # long A: profit when A rises
        pnl_b = shares_b * (entry_b - exit_b)   # short B: profit when B falls
    else:
        pnl_a = shares_a * (entry_a - exit_a)   # short A: profit when A falls
        pnl_b = shares_b * (exit_b - entry_b)   # long B: profit when B rises

    pnl_usd = pnl_a + pnl_b
    pnl_pct  = pnl_usd / size_usd * 100 if size_usd > 0 else 0

    return {"pnl_usd": round(pnl_usd, 2), "pnl_pct": round(pnl_pct, 2)}


def _refresh_pairs_trade(trade):
    """Refresh a two-leg cointegration trade. Returns list of 0 or 1 update dicts."""
    trade_id  = trade["id"]
    signal_id = trade["signal_id"]

    signal = db.get_signal_by_id(signal_id)
    if not signal:
        log.warning("Signal %s not found for trade %d, skipping", signal_id, trade_id)
        return []

    token_a = signal.get("token_id_a")
    token_b = signal.get("token_id_b")
    if not token_a or not token_b:
        log.warning("Trade %d: signal %s missing token IDs — cannot fetch prices",
                    trade_id, signal_id)
        return []

    try:
        current_a = api.get_midpoint(token_a)
        current_b = api.get_midpoint(token_b)
    except Exception as e:
        log.warning("Failed to fetch prices for trade %d: %s", trade_id, e)
        return []

    current_a = db._normalize_probability_price(current_a)
    current_b = db._normalize_probability_price(current_b)
    if current_a is None or current_b is None or current_a <= 0 or current_b <= 0:
        log.warning("Invalid prices for trade %d: a=%.4f b=%.4f",
                    trade_id, current_a or -1, current_b or -1)
        return []

    pnl = db.calculate_pairs_mark_to_market(
        trade["size_usd"],
        trade["entry_price_a"],
        current_a,
        trade["entry_price_b"],
        current_b,
        trade["side_a"],
    )
    if not pnl["ok"]:
        log.warning(
            "Cannot mark pairs trade %d: entry_a=%r entry_b=%r current_a=%r current_b=%r size=%r",
            trade_id,
            trade.get("entry_price_a"),
            trade.get("entry_price_b"),
            current_a,
            current_b,
            trade.get("size_usd"),
        )
        return []

    beta        = signal.get("beta", 1.0) or 1.0
    spread      = current_a - beta * current_b
    spread_mean = signal.get("spread_mean", 0) or 0
    spread_std  = signal.get("spread_std", 1) or 1
    z_score     = (spread - spread_mean) / spread_std if spread_std > 0 else 0

    try:
        db.save_snapshot(trade_id, current_a, current_b, spread, z_score)
        log.debug("Snapshot: trade=%d a=%.4f b=%.4f z=%.2f pnl=$%.2f",
                  trade_id, current_a, current_b, z_score, pnl["pnl_usd"])
    except Exception as e:
        log.error("Failed to save snapshot for trade %d: %s", trade_id, e)

    regime_break_threshold = trade.get("regime_break_threshold")
    regime_break = bool(regime_break_threshold and abs(z_score) >= regime_break_threshold)
    if regime_break:
        log.warning(
            "Regime-break flag: trade=%d | |z|=%.2f threshold=%.2f event=%s",
            trade_id,
            abs(z_score),
            regime_break_threshold,
            trade.get("event", "?")[:40],
        )
    db.update_pairs_trade_metrics(
        trade_id,
        current_pnl=pnl["pnl_usd"],
        current_z_score=round(z_score, 4),
        regime_break=regime_break,
        regime_break_note=(
            f"|z| reached {abs(z_score):.2f} against threshold {regime_break_threshold:.2f}"
            if regime_break else None
        ),
    )

    return [{
        "trade_id":        trade_id,
        "trade_type":      "pairs",
        "event":           trade.get("event", ""),
        "side_a":          trade["side_a"],
        "side_b":          trade["side_b"],
        "entry_price_a":   trade["entry_price_a"],
        "entry_price_b":   trade["entry_price_b"],
        "current_price_a": current_a,
        "current_price_b": current_b,
        "z_score":         round(z_score, 4),
        "unrealized_pnl":  pnl,
    }]


def auto_close_trades(z_threshold=0.5):
    """Close trades where the exit condition is met.

    Pairs: close when |z-score| < z_threshold (spread reverted) or price resolved.
    Weather: close when token price resolves (>= 0.99 = win, <= 0.01 = loss).

    Returns:
        list of dicts with closed trade info and realized P&L.
    """
    trades = db.get_trades(status="open", limit=None)
    if not trades:
        log.debug("No open trades to check for auto-close")
        return []

    closed = []
    for trade in trades:
        trade_type = trade.get("trade_type") or "pairs"
        if trade_type in {"weather", "copy", "whale"}:
            result = _auto_close_single_leg(trade)
        else:
            result = _auto_close_pairs(trade, z_threshold)
        if result:
            closed.append(result)

    if closed:
        log.info("Auto-closed %d trades", len(closed))
    return closed


def _auto_close_weather(trade):
    """Auto-close a weather trade on resolution. Returns close dict or None."""
    trade_id = trade["id"]
    price_state = _resolve_single_leg_price(trade, "auto-close")
    if not price_state:
        return None
    current_a = price_state.get("price")
    if current_a is None:
        return None

    entry_price = trade.get("entry_price_a") or 0
    price_resolved = price_state.get("resolved") or current_a >= 0.99 or current_a <= 0.01
    if price_resolved:
        outcome = "WIN" if current_a >= 0.99 else "LOSS"
        reason  = f"resolved ({outcome})"
        pnl_usd = db.close_trade(trade_id, current_a, notes=f"Auto-closed: {reason}")
        if pnl_usd is not None:
            log.info("AUTO-CLOSE weather: trade=%d %s pnl=$%.2f event=%s",
                     trade_id, reason, pnl_usd, trade.get("event", "?")[:40])
            return {
                "trade_id":      trade_id,
                "trade_type":    "weather",
                "exit_price_a":  current_a,
                "pnl_usd":       round(pnl_usd, 2),
                "price_source":  price_state.get("source"),
                "reason":        reason,
            }
        return None

    stop_loss_floor = entry_price * (1 - WEATHER_STOP_LOSS_PCT) if entry_price > 0 else 0
    if entry_price > 0 and current_a <= stop_loss_floor:
        reason = f"stop-loss hit ({current_a:.3f} <= {stop_loss_floor:.3f})"
        pnl_usd = db.close_trade(trade_id, current_a, notes=f"Auto-closed: {reason}")
        if pnl_usd is not None:
            signal = _load_weather_signal(trade)
            context = _build_weather_stop_context(trade, signal, entry_price, current_a, stop_loss_floor)
            event_label = (trade.get("event") or "?")[:40]
            log.info(
                "AUTO-CLOSE weather: trade=%d %s pnl=$%.2f event=%s context=%s",
                trade_id, reason, pnl_usd, event_label, context,
            )
            _record_weather_stop_context(trade, context, reason)
            weather_guard_state.register_failure(reason)
            return {
                "trade_id": trade_id,
                "trade_type": "weather",
                "exit_price_a": current_a,
                "pnl_usd": round(pnl_usd, 2),
                "reason": reason,
            }
        return None

    age_secs = time.time() - (trade.get("opened_at") or time.time())
    if age_secs >= WEATHER_MAX_HOLD_HOURS * 3600:
        hours = age_secs / 3600
        reason = f"max hold time exceeded ({hours:.1f}h >= {WEATHER_MAX_HOLD_HOURS}h)"
        pnl_usd = db.close_trade(trade_id, current_a, notes=f"Auto-closed: {reason}")
        if pnl_usd is not None:
            event_label = (trade.get("event") or "?")[:40]
            log.info(
                "AUTO-CLOSE weather: trade=%d %s pnl=$%.2f event=%s",
                trade_id, reason, pnl_usd, event_label,
            )
            return {
                "trade_id": trade_id,
                "trade_type": "weather",
                "exit_price_a": current_a,
                "pnl_usd": round(pnl_usd, 2),
                "reason": reason,
            }
        return None

    log.debug("Weather trade %d still active: price=%.4f", trade_id, current_a)
    return None


def _auto_close_whale_guardrails(trade, current_price, price_source):
    """Exit whale trades that hit loss, hold-time, or volatility limits."""
    trade_id = trade["id"]
    size_usd = float(trade.get("size_usd") or 0.0)
    entry_price = trade.get("entry_price_a")
    if entry_price is None or size_usd <= 0:
        return None

    valuation = db.calculate_single_leg_mark_to_market(size_usd, entry_price, current_price)
    if not valuation.get("ok"):
        return None

    pnl_usd = valuation["pnl_usd"]
    now = time.time()
    age_secs = now - (trade.get("opened_at") or now)
    reason = None

    if pnl_usd <= -WHALE_MAX_DRAWNDOWN_USD:
        reason = f"loss guardrail hit (${pnl_usd:.2f} <= -${WHALE_MAX_DRAWNDOWN_USD:.2f})"
    elif age_secs >= WHALE_MAX_HOLD_SECS:
        hours = age_secs / 3600
        reason = f"max hold time exceeded ({hours:.1f}h >= {WHALE_MAX_HOLD_SECS/3600:.0f}h)"
    else:
        side = (trade.get("side_a") or "").upper()
        entry = float(entry_price or 0)
        if entry > 0:
            adverse_pct = 0.0
            if "YES" in side:
                adverse_pct = max(0.0, (entry - current_price) / entry)
            elif "NO" in side:
                adverse_pct = max(0.0, (current_price - entry) / entry)
            if adverse_pct >= WHALE_VOLATILITY_DROP_PCT:
                reason = f"volatility stop triggered ({adverse_pct*100:.1f}% adverse move)"

    if not reason:
        return None

    pnl_tracks = db.close_trade(trade_id, current_price, notes=f"Auto-closed: {reason}")
    if pnl_tracks is None:
        return None

    log.info(
        "AUTO-CLOSE whale guardrail: trade=%d reason=%s pnl=$%.2f event=%s",
        trade_id,
        reason,
        pnl_tracks,
        trade.get("event", "?")[:40],
    )

    aggregate = db.get_whale_open_drawdown_snapshot()
    if aggregate.get("open_trades") and aggregate.get("pnl_usd", 0.0) <= -WHALE_AGGREGATE_ALERT_DRAWDOWN_USD:
        log.warning(
            "Whale aggregate drawdown alert: pnl=$%.2f across %d open whale trades (threshold -$%.2f) after closing trade=%d (%s).",
            aggregate["pnl_usd"],
            aggregate["open_trades"],
            WHALE_AGGREGATE_ALERT_DRAWDOWN_USD,
            trade_id,
            "captures the $-54.52 case" if aggregate["pnl_usd"] <= -54.52 else "guardrails running",
        )
    return {
        "trade_id": trade_id,
        "trade_type": "whale",
        "exit_price_a": current_price,
        "pnl_usd": round(pnl_tracks, 2),
        "reason": reason,
        "price_source": price_source or "guardrail",
    }


def _auto_close_single_leg(trade):
    """Auto-close a single-leg trade when the underlying market resolves."""
    trade_type = _trade_kind(trade)
    if trade_type == "weather":
        return _auto_close_weather(trade)

    trade_id = trade["id"]
    price_state = _resolve_single_leg_price(trade, "auto-close")
    if not price_state or price_state.get("price") is None:
        return None

    current_a = price_state["price"]
    if price_state.get("resolved"):
        reason = "resolved"
        pnl_usd = db.close_trade(trade_id, current_a, notes=f"Auto-closed: {reason}")
        if pnl_usd is None:
            return None

        log.info("AUTO-CLOSE %s: trade=%d %s pnl=$%.2f event=%s",
                 trade_type, trade_id, reason, pnl_usd, trade.get("event", "?")[:40])
        return {
            "trade_id": trade_id,
            "trade_type": trade_type,
            "exit_price_a": current_a,
            "pnl_usd": round(pnl_usd, 2),
            "price_source": price_state.get("source"),
            "reason": reason,
        }

    if trade_type == "whale":
        guardrail_result = _auto_close_whale_guardrails(trade, current_a, price_state.get("source"))
        if guardrail_result:
            return guardrail_result

    return None


def _auto_close_pairs(trade, z_threshold):
    """Auto-close a pairs trade on reversion or resolution. Returns close dict or None."""
    trade_id  = trade["id"]
    signal_id = trade["signal_id"]

    signal = db.get_signal_by_id(signal_id)
    if not signal:
        log.warning("Signal %s not found for trade %d, skipping auto-close",
                    signal_id, trade_id)
        return None

    token_a = signal.get("token_id_a")
    token_b = signal.get("token_id_b")
    if not token_a or not token_b:
        log.warning("Trade %d: signal %s missing token IDs — cannot auto-close",
                    trade_id, signal_id)
        return None

    try:
        current_a = api.get_midpoint(token_a)
        current_b = api.get_midpoint(token_b)
    except Exception as e:
        log.warning("Failed to fetch prices for auto-close trade %d: %s", trade_id, e)
        return None

    if current_a <= 0 or current_b <= 0:
        return None

    beta        = signal.get("beta", 1.0) or 1.0
    spread      = current_a - beta * current_b
    spread_mean = signal.get("spread_mean", 0) or 0
    spread_std  = signal.get("spread_std", 1) or 1
    z_score     = (spread - spread_mean) / spread_std if spread_std > 0 else 0

    price_resolved = current_a >= 0.99 or current_a <= 0.01 or current_b >= 0.99 or current_b <= 0.01
    reversion_exit_z = trade.get("reversion_exit_z")
    effective_reversion_z = (
        float(reversion_exit_z)
        if reversion_exit_z is not None
        else z_threshold
    )
    spread_reverted = abs(z_score) < effective_reversion_z
    stop_z_threshold = trade.get("stop_z_threshold")
    stop_hit = bool(stop_z_threshold and abs(z_score) >= stop_z_threshold)
    max_hold_hours = trade.get("max_hold_hours")
    hold_hours = ((time.time() - trade["opened_at"]) / 3600) if trade.get("opened_at") else 0
    timed_out = bool(max_hold_hours and hold_hours >= max_hold_hours)

    if not (spread_reverted or price_resolved or stop_hit or timed_out):
        log.debug("Trade %d still active: z=%.3f (threshold=%.1f)",
                  trade_id, z_score, effective_reversion_z)
        return None

    if price_resolved:
        reason = "resolved"
    elif stop_hit:
        reason = f"trial stop hit (|z|={abs(z_score):.3f} >= {float(stop_z_threshold):.3f})"
    elif timed_out:
        reason = f"trial max hold reached ({hold_hours:.1f}h >= {float(max_hold_hours):.1f}h)"
    else:
        reason = f"z={z_score:.3f} reverted"
    pnl_usd = db.close_trade(trade_id, current_a, current_b,
                              notes=f"Auto-closed: {reason}")
    if pnl_usd is not None:
        log.info("AUTO-CLOSE: trade=%d %s pnl=$%.2f event=%s",
                 trade_id, reason, pnl_usd, trade.get("event", "?")[:40])
        return {
            "trade_id":      trade_id,
            "trade_type":    "pairs",
            "z_score":       round(z_score, 4),
            "exit_price_a":  current_a,
            "exit_price_b":  current_b,
            "pnl_usd":       round(pnl_usd, 2),
            "reason":        reason,
        }
    return None


def calculate_unrealized_pnl(trade, current_price_a, current_price_b):
    """Calculate unrealized P&L for an open pairs trade."""
    return db.calculate_pairs_mark_to_market(
        trade["size_usd"],
        trade["entry_price_a"],
        current_price_a,
        trade["entry_price_b"],
        current_price_b,
        trade["side_a"],
    )
