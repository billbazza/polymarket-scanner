"""Paper trading engine — monitors open trades, updates prices, manages P&L."""
import logging
import time
from datetime import date

import requests

import api
import db

log = logging.getLogger("scanner.tracker")
WEATHER_STOP_LOSS_PCT = 0.20
_WARN_TTL_SECS = 6 * 60 * 60
_LAST_WARNINGS = {}


def refresh_open_trades():
    """Update current prices for all open trades and save snapshots.

    Handles both pairs trades (two-leg spread) and weather trades (single-leg).

    Returns:
        list of dicts with trade_id, current prices, and unrealized P&L.
    """
    trades = db.get_trades(status="open")
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

    if trade_type == "weather":
        signal = _load_weather_signal(trade)
        if signal:
            if signal.get("market_id"):
                market = api.get_market(market_id=signal["market_id"])
                if market:
                    return market
            return api.get_market(token_id=trade.get("token_id_a"))

    if trade_type == "copy" and trade.get("copy_condition_id"):
        market = api.get_market(condition_id=trade["copy_condition_id"])
        if market:
            return market

    return api.get_market(token_id=trade.get("token_id_a"))


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
    token_a = trade.get("token_id_a")
    label = _trade_label(trade)
    if not token_a:
        _log_once("warning", (trade_id, phase, "missing-token"),
                  "%s missing token_id_a; cannot %s", label, phase)
        _set_tracker_note(trade_id, f"Tracker: missing token_id_a; cannot {phase}.")
        return None

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
        _log_once("warning", (trade_id, phase, "gamma-missing"),
                  "%s midpoint 404 and no Gamma market match found during %s",
                  label, phase)
        _set_tracker_note(trade_id, "Tracker: midpoint 404 and no Gamma market match found.")
        return None

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
    _log_once("warning", (trade_id, phase, "unpriceable"),
              "%s midpoint 404 and no usable fallback price during %s; leaving trade open",
              label, phase)
    return {"price": None, "source": "gamma", "resolved": resolved, "unpriceable": True}


def _refresh_weather_trade(trade):
    """Refresh a single-leg trade. Used for weather, copy, and whale positions."""
    trade_id = trade["id"]
    price_state = _resolve_single_leg_price(trade, "refresh")
    if not price_state or price_state.get("price") is None:
        return []
    current_a = price_state["price"]

    entry = trade["entry_price_a"] or 0
    pnl_usd = (current_a - entry) / entry * trade["size_usd"] if entry > 0 else 0

    try:
        db.save_snapshot(trade_id, current_a, None, None, None)
        log.debug("Snapshot weather: trade=%d price=%.4f pnl=$%.2f",
                  trade_id, current_a, pnl_usd)
    except Exception as e:
        log.error("Failed to save snapshot for weather trade %d: %s", trade_id, e)

    return [{
        "trade_id":        trade_id,
        "trade_type":      trade.get("trade_type") or "weather",
        "action":          trade.get("side_a", ""),
        "event":           trade.get("event", ""),
        "entry_price_a":   entry,
        "current_price_a": current_a,
        "price_source":    price_state.get("source"),
        "unrealized_pnl":  {"pnl_usd": round(pnl_usd, 2)},
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

    if current_a <= 0 or current_b <= 0:
        log.warning("Invalid prices for trade %d: a=%.4f b=%.4f",
                    trade_id, current_a, current_b)
        return []

    pnl = _pairs_pnl(trade["entry_price_a"], current_a,
                     trade["entry_price_b"], current_b,
                     trade["side_a"], trade["size_usd"])

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
    trades = db.get_trades(status="open")
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
            log.info("AUTO-CLOSE weather: trade=%d %s pnl=$%.2f event=%s",
                     trade_id, reason, pnl_usd, trade.get("event", "?")[:40])
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


def _auto_close_single_leg(trade):
    """Auto-close a single-leg trade when the underlying market resolves."""
    trade_type = _trade_kind(trade)
    if trade_type == "weather":
        return _auto_close_weather(trade)

    trade_id = trade["id"]
    price_state = _resolve_single_leg_price(trade, "auto-close")
    if not price_state or price_state.get("price") is None or not price_state.get("resolved"):
        return None

    current_a = price_state["price"]
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

    price_resolved  = current_a >= 0.99 or current_a <= 0.01 \
                   or current_b >= 0.99 or current_b <= 0.01
    spread_reverted = abs(z_score) < z_threshold

    if not (spread_reverted or price_resolved):
        log.debug("Trade %d still active: z=%.3f (threshold=%.1f)",
                  trade_id, z_score, z_threshold)
        return None

    reason  = "resolved" if price_resolved else f"z={z_score:.3f} reverted"
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
    return _pairs_pnl(
        trade["entry_price_a"], current_price_a,
        trade["entry_price_b"], current_price_b,
        trade["side_a"], trade["size_usd"],
    )
