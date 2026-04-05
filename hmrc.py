from __future__ import annotations

"""HMRC compliance module — required before any real-money trading.

UK tax context:
  - Prediction market profits may be gambling (tax-free) or trading income / CGT
    depending on HMRC's view of your activity. Systematic algorithmic trading
    with an automated scanner is more likely to be treated as a trade.
  - Each USDC disposal (trade entry/exit) may be a CGT event under crypto asset rules.
  - Self-assessment required if gains exceed the annual CGT exempt amount (£3,000 in 2025/26).
  - Keep records for at least 5 years after the relevant Self Assessment deadline.

This module:
  - Fetches live USD/GBP rate at trade time (Frankfurter API — ECB data, free, no key)
  - Stamps every real trade with GBP values for HMRC record-keeping
  - Gates live trading: if GBP rate is unavailable, trade is BLOCKED
  - Appends each real trade to logs/hmrc_audit.jsonl (immutable append-only log)

DO NOT delete or modify hmrc_audit.jsonl — it is your HMRC audit trail.
"""
import json
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger("scanner.hmrc")

_FRANKFURTER = "https://api.frankfurter.app/latest?from=USD&to=GBP"
_AUDIT_LOG = Path(__file__).parent / "logs" / "hmrc_audit.jsonl"
_TIMEOUT = 8

# Cache rate for up to 5 minutes — don't hammer the API
_rate_cache: dict = {"rate": None, "fetched_at": 0}
_CACHE_TTL = 300


def fetch_gbp_rate() -> float | None:
    """Return current USD→GBP rate, or None if unavailable.

    Caches for 5 minutes. Returns None on any network/parse error.
    """
    now = time.time()
    if _rate_cache["rate"] and (now - _rate_cache["fetched_at"]) < _CACHE_TTL:
        return _rate_cache["rate"]
    try:
        resp = requests.get(_FRANKFURTER, timeout=_TIMEOUT)
        resp.raise_for_status()
        rate = resp.json()["rates"]["GBP"]
        _rate_cache["rate"] = rate
        _rate_cache["fetched_at"] = now
        log.debug("GBP rate refreshed: 1 USD = %.5f GBP", rate)
        return rate
    except Exception as e:
        log.error("HMRC: GBP rate fetch failed: %s", e)
        return None


def stamp_trade(trade: dict) -> dict:
    """Add GBP fields to a trade dict. Returns trade unchanged if rate unavailable.

    Adds:
      gbp_rate       — USD/GBP exchange rate at trade time
      size_gbp       — position size in GBP
      entry_value_gbp — entry cost in GBP (size_usd * entry_price * rate)
    """
    rate = fetch_gbp_rate()
    if rate is None:
        return trade
    trade = dict(trade)
    trade["gbp_rate"] = round(rate, 6)
    trade["size_gbp"] = round(trade.get("size_usd", 0) * rate, 2)
    entry = trade.get("entry_price_a") or 0
    trade["entry_value_gbp"] = round(trade.get("size_usd", 0) * entry * rate, 2)
    return trade


def require_gbp_rate() -> float:
    """Fetch GBP rate or raise RuntimeError — call before any live trade.

    This is the HMRC gate: if we can't record the GBP value at trade time,
    we do not execute the trade.
    """
    rate = fetch_gbp_rate()
    if rate is None:
        raise RuntimeError(
            "HMRC compliance blocked: GBP rate unavailable. "
            "Real trade NOT executed. Check network connectivity."
        )
    return rate


def log_real_trade(trade: dict, action: str = "opened") -> None:
    """Append a real trade to the HMRC audit log (hmrc_audit.jsonl).

    Call this for every live trade open and close.
    The file is append-only — never truncate or overwrite it.
    """
    _AUDIT_LOG.parent.mkdir(exist_ok=True)
    entry = {
        "hmrc_timestamp": time.time(),
        "hmrc_datetime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,          # "opened" | "closed"
        "mode": "live",
        "gbp_rate": trade.get("gbp_rate"),
        "size_usd": trade.get("size_usd"),
        "size_gbp": trade.get("size_gbp"),
        "entry_value_gbp": trade.get("entry_value_gbp"),
        "pnl_usd": trade.get("pnl"),
        "pnl_gbp": round(trade.get("pnl", 0) * trade.get("gbp_rate", 0), 2) if trade.get("pnl") else None,
        "fees_usd": trade.get("fee_total_usd") or trade.get("fees_usd"),
        "fees_gbp": round((trade.get("fee_total_usd") or trade.get("fees_usd") or 0) * (trade.get("gbp_rate") or 0), 2) if trade.get("gbp_rate") else None,
        "trade_id": trade.get("id") or trade.get("trade_id"),
        "runtime_scope": trade.get("runtime_scope") or "penny",
        "trade_type": trade.get("trade_type", "pairs"),
        "market_a": trade.get("market_a") or trade.get("event"),
        "market_b": trade.get("market_b"),
        "entry_price_a": trade.get("entry_price_a"),
        "entry_price_b": trade.get("entry_price_b"),
        "exit_price_a": trade.get("exit_price_a"),
        "exit_price_b": trade.get("exit_price_b"),
        "side_a": trade.get("side_a"),
        "side_b": trade.get("side_b"),
        "external_order_id_a": trade.get("external_order_id_a") or ((trade.get("entry_execution") or {}).get("orders") or {}).get("a", {}).get("order_id"),
        "external_order_id_b": trade.get("external_order_id_b") or ((trade.get("entry_execution") or {}).get("orders") or {}).get("b", {}).get("order_id"),
        "entry_tx_hash_a": ((trade.get("entry_execution") or {}).get("orders") or {}).get("a", {}).get("tx_hash"),
        "entry_tx_hash_b": ((trade.get("entry_execution") or {}).get("orders") or {}).get("b", {}).get("tx_hash"),
        "exit_tx_hash_a": ((trade.get("exit_execution") or {}).get("orders") or {}).get("a", {}).get("tx_hash"),
        "exit_tx_hash_b": ((trade.get("exit_execution") or {}).get("orders") or {}).get("b", {}).get("tx_hash"),
        "notes": trade.get("notes", ""),
    }
    with open(_AUDIT_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("HMRC audit logged: trade %s %s (GBP rate %.5f)",
             entry["trade_id"], action, entry["gbp_rate"] or 0)


def audit_log_path() -> Path:
    return _AUDIT_LOG


def is_ready() -> tuple[bool, str]:
    """Check HMRC logging is ready. Returns (ok, message).

    Call before enabling live trading mode.
    """
    rate = fetch_gbp_rate()
    if rate is None:
        return False, "GBP rate API unavailable — cannot record trade values in GBP"
    _AUDIT_LOG.parent.mkdir(exist_ok=True)
    try:
        with open(_AUDIT_LOG, "a"):
            pass
    except OSError as e:
        return False, f"Cannot write to HMRC audit log {_AUDIT_LOG}: {e}"
    return True, f"HMRC ready — 1 USD = {rate:.5f} GBP — audit log: {_AUDIT_LOG}"
