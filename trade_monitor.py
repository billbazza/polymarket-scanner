"""Open-trade reconciliation and stuck-trade monitoring.

Classifies every open trade into one of four operator-facing states:
  - resolved
  - unpriceable-but-identifiable
  - detached-from-watched-wallet
  - genuinely-still-open

When confidence is high, the monitor also performs safe paper-trade remediation
and records an immutable audit trail in SQLite.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import requests

import api
import db
from copy_scanner import get_positions

log = logging.getLogger("scanner.trade_monitor")

_PAST_END_ALERT_GRACE_SECS = 6 * 60 * 60
_LATEST_EVENT_TTL_SECS = 6 * 60 * 60
_TEST_MARKERS = ("test", "mock", "placeholder", "dummy", "sample")


def _parse_iso8601(value: str | None) -> float | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _market_is_resolved(market: dict | None) -> bool:
    if not market:
        return False
    status = (market.get("umaResolutionStatus") or "").lower()
    winner = market.get("winner")
    if status in {"resolved", "settled"}:
        return True
    if market.get("closed") and not market.get("acceptingOrders", True):
        return True
    if winner not in (None, "", "null"):
        return True
    return False


def _extract_token_price(market: dict | None, token_id: str | None) -> float | None:
    token = api.normalize_token_id(token_id)
    if not market or not token:
        return None
    price = api.extract_market_price(market, token)
    return db._normalize_probability_price(price)


def _load_trade_market(trade: dict) -> tuple[dict | None, str | None]:
    try:
        if trade.get("trade_type") == "weather" and trade.get("weather_signal_id"):
            signal = db.get_weather_signal_by_id(trade["weather_signal_id"])
            if signal and signal.get("market_id"):
                market = api.get_market(market_id=signal["market_id"])
                if market:
                    return market, "market_id"
            token = api.normalize_token_id(trade.get("token_id_a"))
            if token:
                return api.get_market(token_id=token), "token_id"
            return None, "missing-token"

        if trade.get("trade_type") == "copy" and trade.get("copy_condition_id"):
            return api.get_market(condition_id=trade["copy_condition_id"]), "condition_id"

        token = api.normalize_token_id(trade.get("token_id_a"))
        if token:
            return api.get_market(token_id=token), "token_id"
        return None, "missing-token"
    except Exception as exc:
        log.warning("Trade %s market lookup failed: %s", trade.get("id"), exc)
        return None, str(exc)


def _price_state_for_trade(trade: dict, market: dict | None) -> dict:
    token = api.normalize_token_id(trade.get("token_id_a"))
    if not trade.get("token_id_a") or not token:
        return {
            "ok": False,
            "price": None,
            "source": "invalid_token",
            "reason_code": "invalid_token_id",
        }

    try:
        midpoint = db._normalize_probability_price(api.get_midpoint(token))
        if midpoint is not None:
            return {"ok": True, "price": midpoint, "source": "midpoint"}
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code != 404:
            return {
                "ok": False,
                "price": None,
                "source": "midpoint",
                "reason_code": f"midpoint_http_{status_code or 'error'}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "price": None,
            "source": "midpoint",
            "reason_code": f"midpoint_error:{exc}",
        }

    gamma_price = _extract_token_price(market, token)
    if gamma_price is not None:
        return {"ok": True, "price": gamma_price, "source": "gamma"}
    return {
        "ok": False,
        "price": None,
        "source": "gamma",
        "reason_code": "gamma_price_missing",
    }


def _wallet_position_state(trade: dict, cache: dict[str, list[dict]]) -> dict | None:
    if trade.get("trade_type") != "copy" or not trade.get("copy_wallet"):
        return None
    wallet = (trade.get("copy_wallet") or "").lower()
    if wallet not in cache:
        cache[wallet] = get_positions(wallet)
    positions = cache.get(wallet) or []
    for pos in positions:
        if pos.get("conditionId") != trade.get("copy_condition_id"):
            continue
        if trade.get("copy_outcome") and pos.get("outcome") and pos.get("outcome") != trade.get("copy_outcome"):
            continue
        return {
            "wallet": wallet,
            "attached": True,
            "position": pos,
        }
    return {
        "wallet": wallet,
        "attached": False,
        "position": None,
    }


def _is_obvious_placeholder(trade: dict) -> bool:
    haystack = " ".join(
        str(trade.get(key) or "")
        for key in ("event", "market_a", "notes", "token_id_a")
    ).lower()
    return any(marker in haystack for marker in _TEST_MARKERS)


def _latest_monitor_state(trade_id: int) -> dict | None:
    events = db.get_trade_monitor_events(limit=1, trade_id=trade_id)
    return events[0] if events else None


def _record_if_changed(result: dict) -> None:
    latest = _latest_monitor_state(result["trade_id"])
    details = result.get("details") or {}
    if latest:
        same_core_state = (
            latest.get("classification") == result.get("classification")
            and latest.get("status") == result.get("status")
            and latest.get("reason_code") == result.get("reason_code")
            and latest.get("remediation_action") == result.get("remediation_action")
        )
        if same_core_state and (time.time() - float(latest.get("timestamp") or 0)) < _LATEST_EVENT_TTL_SECS:
            return

    db.record_trade_monitor_event(
        source="trade_monitor",
        trade_id=result["trade_id"],
        trade_status=result.get("trade_status"),
        event_type="open_trade_reconciled",
        status=result.get("status") or "unknown",
        classification=result.get("classification"),
        reason_code=result.get("reason_code"),
        reason=result.get("reason"),
        remediation_action=result.get("remediation_action"),
        details=details,
    )


def _close_trade_with_audit(
    trade: dict,
    *,
    exit_price: float,
    notes: str,
    action: str,
    classification: str,
) -> dict:
    pnl = db.close_trade(trade["id"], exit_price_a=exit_price, notes=notes)
    log.info("Trade monitor auto-remediation: trade=%s action=%s exit=%.4f pnl=%s",
             trade["id"], action, exit_price, pnl)
    return {
        "trade_id": trade["id"],
        "trade_status": "closed",
        "classification": classification,
        "status": "auto_closed",
        "reason_code": action,
        "reason": notes,
        "remediation_action": action,
        "details": {
            "exit_price_a": exit_price,
            "pnl_usd": pnl,
        },
    }


def classify_trade(trade: dict, wallet_positions_cache: dict[str, list[dict]] | None = None) -> dict:
    wallet_positions_cache = wallet_positions_cache or {}
    market, market_lookup = _load_trade_market(trade)
    price_state = _price_state_for_trade(trade, market)
    wallet_state = _wallet_position_state(trade, wallet_positions_cache)

    now = time.time()
    end_ts = _parse_iso8601(market.get("endDate") if market else None)
    target_ts = _parse_iso8601(f"{trade['target_date']}T23:59:59+00:00") if trade.get("target_date") else None
    expected_close_ts = min(v for v in (end_ts, target_ts) if v is not None) if any(v is not None for v in (end_ts, target_ts)) else None
    past_expected_close = expected_close_ts is not None and now > expected_close_ts + _PAST_END_ALERT_GRACE_SECS
    market_resolved = _market_is_resolved(market)

    details = {
        "market_found": bool(market),
        "market_lookup": market_lookup,
        "market_end_date": market.get("endDate") if market else None,
        "market_closed": market.get("closed") if market else None,
        "market_active": market.get("active") if market else None,
        "market_accepting_orders": market.get("acceptingOrders") if market else None,
        "market_resolution_status": market.get("umaResolutionStatus") if market else None,
        "price_source": price_state.get("source"),
        "price_ok": price_state.get("ok", False),
        "price": price_state.get("price"),
        "expected_close_ts": expected_close_ts,
        "past_expected_close": past_expected_close,
    }
    if wallet_state is not None:
        details["wallet_attached"] = wallet_state["attached"]
        details["wallet_active"] = trade.get("copy_wallet_active")
        details["wallet_reason"] = trade.get("copy_wallet_reason")
        if wallet_state["position"]:
            details["wallet_position"] = {
                "outcome": wallet_state["position"].get("outcome"),
                "curPrice": wallet_state["position"].get("curPrice"),
                "currentValue": wallet_state["position"].get("currentValue"),
            }

    if market_resolved:
        final_price = _extract_token_price(market, trade.get("token_id_a"))
        if final_price is not None:
            return {
                "trade_id": trade["id"],
                "trade_status": trade.get("status"),
                "classification": "resolved",
                "status": "actionable",
                "reason_code": "market_resolved",
                "reason": "Market resolved on Gamma; safe to close at final outcome price.",
                "remediation_action": "auto_close_resolved",
                "details": details,
                "exit_price_a": final_price,
            }

    if wallet_state is not None and (trade.get("copy_wallet_active") != 1 or not wallet_state["attached"]):
        reason_code = "wallet_unwatched" if trade.get("copy_wallet_active") != 1 else "wallet_position_missing"
        reason = (
            "Copy trade source wallet is no longer actively watched."
            if trade.get("copy_wallet_active") != 1
            else "Copy trade no longer matches an open source-wallet position."
        )
        if price_state.get("ok"):
            return {
                "trade_id": trade["id"],
                "trade_status": trade.get("status"),
                "classification": "detached-from-watched-wallet",
                "status": "actionable",
                "reason_code": reason_code,
                "reason": reason,
                "remediation_action": "auto_close_detached",
                "details": details,
                "exit_price_a": price_state["price"],
            }
        return {
            "trade_id": trade["id"],
            "trade_status": trade.get("status"),
            "classification": "detached-from-watched-wallet",
            "status": "manual_review",
            "reason_code": f"{reason_code}_unpriceable",
            "reason": f"{reason} A usable exit price is not currently available.",
            "remediation_action": "flag_detached_unpriceable",
            "details": details,
        }

    if not price_state.get("ok"):
        if _is_obvious_placeholder(trade):
            return {
                "trade_id": trade["id"],
                "trade_status": trade.get("status"),
                "classification": "unpriceable-but-identifiable",
                "status": "actionable",
                "reason_code": "synthetic_placeholder_trade",
                "reason": "Synthetic placeholder/test trade cannot be priced and should be administratively closed flat.",
                "remediation_action": "auto_close_placeholder_flat",
                "details": details,
                "exit_price_a": db._normalize_probability_price(trade.get("entry_price_a")) or 0.5,
            }
        return {
            "trade_id": trade["id"],
            "trade_status": trade.get("status"),
            "classification": "unpriceable-but-identifiable",
            "status": "manual_review",
            "reason_code": price_state.get("reason_code") or "unpriceable",
            "reason": "Trade can be identified, but no reliable live or final price is available.",
            "remediation_action": "flag_unpriceable",
            "details": details,
        }

    if past_expected_close:
        return {
            "trade_id": trade["id"],
            "trade_status": trade.get("status"),
            "classification": "genuinely-still-open",
            "status": "attention_required",
            "reason_code": "past_expected_close_still_active",
            "reason": "Nominal market end/target date passed, but Polymarket still shows the market active or unresolved.",
            "remediation_action": "flag_past_end_open",
            "details": details,
        }

    return {
        "trade_id": trade["id"],
        "trade_status": trade.get("status"),
        "classification": "genuinely-still-open",
        "status": "open_ok",
        "reason_code": "still_open",
        "reason": "Trade is still open on current source-of-truth market data.",
        "remediation_action": "none",
        "details": details,
    }


def reconcile_open_trades(auto_remediate: bool = True) -> dict:
    open_trades = db.get_trades(status="open", limit=None)
    wallet_positions_cache: dict[str, list[dict]] = {}
    results = []
    counts = {
        "resolved": 0,
        "unpriceable-but-identifiable": 0,
        "detached-from-watched-wallet": 0,
        "genuinely-still-open": 0,
    }
    auto_closed = []

    for trade in sorted(open_trades, key=lambda item: item["id"]):
        result = classify_trade(trade, wallet_positions_cache=wallet_positions_cache)
        counts[result["classification"]] = counts.get(result["classification"], 0) + 1

        if auto_remediate and result.get("status") == "actionable":
            if result.get("remediation_action") == "auto_close_resolved":
                details = result.get("details") or {}
                notes = (
                    "Trade monitor auto-close: resolved via Gamma market status; "
                    f"exit_price={result['exit_price_a']:.4f}; "
                    f"resolution_status={details.get('market_resolution_status') or 'resolved'}."
                )
                result = _close_trade_with_audit(
                    trade,
                    exit_price=result["exit_price_a"],
                    notes=notes,
                    action="auto_closed_resolved",
                    classification="resolved",
                )
                auto_closed.append(result["trade_id"])
            elif result.get("remediation_action") == "auto_close_detached":
                notes = (
                    "Trade monitor auto-close: detached from watched wallet; "
                    f"exit_price={result['exit_price_a']:.4f}; "
                    f"reason_code={result.get('reason_code')}."
                )
                result = _close_trade_with_audit(
                    trade,
                    exit_price=result["exit_price_a"],
                    notes=notes,
                    action="auto_closed_detached",
                    classification="detached-from-watched-wallet",
                )
                auto_closed.append(result["trade_id"])
            elif result.get("remediation_action") == "auto_close_placeholder_flat":
                notes = (
                    "Trade monitor administrative close: synthetic placeholder/test trade "
                    f"closed flat at entry price {result['exit_price_a']:.4f} because no real market exists."
                )
                result = _close_trade_with_audit(
                    trade,
                    exit_price=result["exit_price_a"],
                    notes=notes,
                    action="auto_closed_placeholder_flat",
                    classification="unpriceable-but-identifiable",
                )
                auto_closed.append(result["trade_id"])

        _record_if_changed(result)
        results.append(result)

    summary = {
        "timestamp": time.time(),
        "open_trades_scanned": len(open_trades),
        "auto_remediate": auto_remediate,
        "auto_closed_trade_ids": auto_closed,
        "counts": counts,
        "results": results,
    }
    log.info(
        "Trade reconciliation complete: scanned=%d auto_closed=%d counts=%s",
        summary["open_trades_scanned"],
        len(auto_closed),
        json.dumps(counts, sort_keys=True),
    )
    return summary


def get_flagged_open_trades() -> dict:
    trades = {trade["id"]: trade for trade in db.get_trades(status="open", limit=None)}
    latest = db.get_latest_trade_monitor_states(open_only=True)
    flagged = []
    for item in latest:
        if item.get("status") in {"open_ok", "no_issue"}:
            continue
        trade = trades.get(item["trade_id"])
        if not trade:
            continue
        flagged.append({
            "trade_id": trade["id"],
            "trade_type": trade.get("trade_type"),
            "event": trade.get("event"),
            "market_a": trade.get("market_a"),
            "opened_at": trade.get("opened_at"),
            "classification": item.get("classification"),
            "status": item.get("status"),
            "reason_code": item.get("reason_code"),
            "reason": item.get("reason"),
            "remediation_action": item.get("remediation_action"),
            "details": item.get("details"),
        })
    return {
        "summary": db.get_trade_monitor_summary(open_only=True),
        "trades": flagged,
    }
