"""Wallet monitor — background service for copy trading.

Two jobs:
  1. SCORING: classify each watched wallet (informed / neutral / bot / skip)
  2. POLLING: check positions every 2 min, open copy trades on new positions,
              close copy trades when wallet exits

WebSocket price feed: subscribes to markets where we have open copy trades
and updates prices in near-real-time (separate async thread).

Usage (standalone):
    arch -arm64 python3 wallet_monitor.py

Started automatically by server.py on startup.
"""
import asyncio
import json
import logging
import math
import threading
import time
from collections import defaultdict
from pathlib import Path

import requests

import db
from copy_scanner import WATCHED_WALLETS as _LEGACY_WALLETS, get_activity, get_positions, _categorise


def _get_active_wallets() -> dict:
    """Return {address: label} for all active watched wallets from DB."""
    rows = db.get_watched_wallets(active_only=True)
    return {r["address"]: r["label"] for r in rows}

log = logging.getLogger("scanner.wallet_monitor")

# ── Config ─────────────────────────────────────────────────────────────────────

POLL_INTERVAL   = 120        # seconds between position checks
SCORE_INTERVAL  = 3600       # rescore wallets every hour
MIN_TRADES      = 50         # skip wallets with fewer trades
BOT_TRADES_MONTH = 150       # above this = bot, skip
MIN_AVG_SIZE    = 500        # minimum avg trade size USD to copy
MIN_SCORE       = 60         # only auto-copy wallets scoring >= this
COPY_SIZE_USD   = 20         # paper trade size per copy
STATE_FILE      = Path(__file__).parent / "logs" / "wallet_state.json"

WS_URI = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ── Module-level state (shared between threads) ────────────────────────────────

_status: dict = {
    "running": False,
    "last_poll": None,
    "last_score": None,
    "polls_run": 0,
    "new_trades_found": 0,
    "wallets": {},    # address → {score, classification, last_checked, ...}
}

_known_positions: dict = {}   # address → set of conditionIds seen last poll
_ws_thread: threading.Thread | None = None
_poll_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _update_running_status():
    global _poll_thread, _ws_thread
    _status["running"] = any(
        thread and thread.is_alive()
        for thread in (_poll_thread, _ws_thread)
    )


def _record_event(
    *,
    source: str,
    wallet: str,
    label: str,
    event_type: str,
    status: str,
    reason_code: str | None = None,
    reason: str | None = None,
    condition_id: str | None = None,
    outcome_name: str | None = None,
    market_title: str | None = None,
    price: float | None = None,
    position_value_usd: float | None = None,
    details: dict | None = None,
    checked_at: float | None = None,
    positions_count: int | None = None,
) -> None:
    recorder = getattr(db, "record_wallet_monitor_event", None)
    if not callable(recorder):
        return
    try:
        recorder(
            source=source,
            wallet=wallet,
            label=label,
            event_type=event_type,
            status=status,
            reason_code=reason_code,
            reason=reason,
            condition_id=condition_id,
            outcome_name=outcome_name,
            market_title=market_title,
            price=price,
            position_value_usd=position_value_usd,
            details=details,
            checked_at=checked_at,
            positions_count=positions_count,
        )
    except Exception as exc:
        log.warning("Monitor: wallet event log failed for %s: %s", label, exc)


def _position_key(address: str, position: dict | None = None, trade: dict | None = None) -> tuple[str, str]:
    if position is not None:
        identity = db.get_position_identity(position, wallet=address)
    elif trade is not None:
        identity = {
            "canonical_ref": db.get_trade_reconciliation_key(trade),
            "external_position_id": trade.get("external_position_id") or trade.get("token_id_a"),
            "condition_id": trade.get("copy_condition_id"),
        }
    else:
        identity = {"canonical_ref": None, "external_position_id": None, "condition_id": None}
    return (
        (address or "").lower(),
        identity.get("canonical_ref")
        or identity.get("external_position_id")
        or identity.get("condition_id")
        or "",
    )


def _baseline_matches_position(baseline: set[str], position: dict) -> bool:
    identity = db.get_position_identity(position)
    keys = {
        identity.get("canonical_ref"),
        identity.get("external_position_id"),
        identity.get("condition_id"),
    }
    return any(key in baseline for key in keys if key)


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_wallet(address: str, label: str, activity_limit: int = 500) -> dict:
    """Score a wallet 0-100. Returns full scoring breakdown."""
    trades = get_activity(address, limit=activity_limit)
    positions = get_positions(address)

    if not trades:
        return _score_result(address, label, 0, "no_data", {}, trades, positions)

    now = time.time()
    trade_count = len(trades)

    # Trades per month (bot detection)
    oldest = min(t["timestamp"] for t in trades)
    months_active = max((now - oldest) / (30 * 86400), 0.1)
    trades_per_month = trade_count / months_active

    # Average trade size
    sizes = [t.get("usdcSize", 0) for t in trades if t.get("usdcSize", 0) > 0]
    avg_size = sum(sizes) / len(sizes) if sizes else 0
    total_volume = sum(sizes)

    # Category focus (fewer = more focused = more informed)
    cat_counts = defaultdict(int)
    for t in trades:
        cat_counts[_categorise(t.get("title", ""))] += 1
    n_cats = len(cat_counts)
    top_cat_pct = max(cat_counts.values()) / trade_count if trade_count else 0

    # Buy/sell ratio (market makers hold both, informed traders buy and hold)
    buys = sum(1 for t in trades if t.get("side") == "BUY")
    sell_ratio = 1 - (buys / trade_count) if trade_count else 0.5

    # Current P&L
    unrealised = sum(p.get("cashPnl", 0) for p in positions)
    realised = sum(p.get("realizedPnl", 0) for p in positions)

    # Rolling win rate (last 30 positions by unrealised direction)
    recent_trades = trades[:30]
    recent_buy_avg = sum(
        t.get("usdcSize", 0) for t in recent_trades if t.get("side") == "BUY"
    ) / max(len(recent_trades), 1)

    # ── Score components (each 0-100) ──────────────────────────────────────────

    # 1. Sample size (need enough history)
    if trade_count < MIN_TRADES:
        return _score_result(address, label, 0, "insufficient_data",
                             {"reason": f"only {trade_count} trades, need {MIN_TRADES}"},
                             trades, positions)

    # 2. Bot filter — high frequency AND small size = bot (large frequent traders are fine)
    if trades_per_month > BOT_TRADES_MONTH and avg_size < 200:
        return _score_result(address, label, 0, "bot",
                             {"trades_per_month": round(trades_per_month, 1),
                              "avg_size_usd": round(avg_size, 0)},
                             trades, positions)

    # 3. Size filter (only copy meaningful positions)
    if avg_size < MIN_AVG_SIZE:
        return _score_result(address, label, 15, "small_trader",
                             {"avg_size_usd": round(avg_size, 0)},
                             trades, positions)

    # Score each dimension
    s_sample   = min(100, (trade_count / 200) * 100)          # up to 200 trades
    s_size     = min(100, math.log10(max(avg_size, 1)) / math.log10(100000) * 100)
    s_focus    = (1 - (n_cats - 1) / 8) * 100                 # fewer categories = higher
    s_focus    = max(0, min(100, s_focus))
    # Frequency score — only penalise small high-frequency trades (bots), not large active traders
    _size_adj_tpm = trades_per_month / max(avg_size / 1000, 1)  # normalise by size
    s_freq     = max(0, 100 - (_size_adj_tpm / 50) * 100)
    s_pnl      = min(100, 50 + (unrealised + realised) / max(total_volume, 1) * 500)
    s_buy_hold = max(0, 100 - sell_ratio * 150)               # low sell ratio = buy-and-hold

    score = (
        s_sample   * 0.15 +
        s_size     * 0.25 +
        s_focus    * 0.20 +
        s_freq     * 0.15 +
        s_pnl      * 0.15 +
        s_buy_hold * 0.10
    )
    score = round(score, 1)

    if score >= 65:
        classification = "informed"
    elif score >= MIN_SCORE:
        classification = "neutral"
    else:
        classification = "skip"

    breakdown = {
        "trade_count": trade_count,
        "trades_per_month": round(trades_per_month, 1),
        "avg_size_usd": round(avg_size, 0),
        "total_volume_usd": round(total_volume, 0),
        "n_categories": n_cats,
        "top_category": max(cat_counts, key=cat_counts.get) if cat_counts else "?",
        "top_cat_pct": round(top_cat_pct * 100, 1),
        "sell_ratio": round(sell_ratio, 3),
        "unrealised_pnl": round(unrealised, 2),
        "realised_pnl": round(realised, 2),
        "open_positions": len(positions),
        "components": {
            "sample":    round(s_sample, 1),
            "size":      round(s_size, 1),
            "focus":     round(s_focus, 1),
            "frequency": round(s_freq, 1),
            "pnl":       round(s_pnl, 1),
            "buy_hold":  round(s_buy_hold, 1),
        },
    }
    return _score_result(address, label, score, classification, breakdown, trades, positions)


def _score_result(address, label, score, classification, breakdown, trades, positions):
    return {
        "address": address,
        "label": label,
        "score": score,
        "classification": classification,
        "will_copy": score >= MIN_SCORE and classification not in ("bot", "no_data", "insufficient_data", "skip"),
        "breakdown": breakdown,
        "scored_at": time.time(),
    }


# ── Position diff & copy trading ───────────────────────────────────────────────

def _check_wallet(address: str, label: str, will_copy: bool) -> tuple[int, int]:
    """Check a wallet for new/exited positions. Returns (opened, closed)."""
    checked_at = time.time()
    try:
        positions = get_positions(address)
    except Exception as e:
        log.warning("Monitor: positions fetch failed for %s: %s", label, e)
        _record_event(
            source="wallet_monitor",
            wallet=address,
            label=label,
            event_type="wallet_polled",
            status="fetch_failed",
            reason_code="positions_fetch_failed",
            reason=f"Positions fetch failed: {e}",
            checked_at=checked_at,
        )
        return 0, 0

    current_positions = [p for p in positions if p.get("conditionId")]
    current_keys = {_position_key(address, position=p)[1] for p in current_positions}
    prev_keys = _known_positions.get(address, None)
    _known_positions[address] = current_keys
    db.update_watched_wallet_poll_status(
        address,
        checked_at=checked_at,
        positions_count=len(current_keys),
    )

    if prev_keys is None:
        # First time seeing this wallet — record state, don't trade.
        # Also set the DB baseline if not already set (forward-only copy trading).
        baseline = db.get_wallet_baseline(address)
        if baseline is None:
            db.set_wallet_baseline(address, sorted(current_keys))
            log.info("Monitor: %s — baseline set: %d existing positions (skipped)",
                     label, len(current_keys))
            _record_event(
                source="wallet_monitor",
                wallet=address,
                label=label,
                event_type="baseline_set",
                status="baseline_skipped",
                reason=f"Baseline set from {len(current_keys)} existing positions; only future positions will mirror.",
                checked_at=checked_at,
                positions_count=len(current_keys),
                details={"baseline_positions": sorted(current_keys)},
            )
        else:
            log.info("Monitor: %s — initial snapshot: %d positions", label, len(current_keys))
            _record_event(
                source="wallet_monitor",
                wallet=address,
                label=label,
                event_type="wallet_polled",
                status="initial_snapshot",
                reason=f"Initial snapshot loaded with {len(current_keys)} live positions.",
                checked_at=checked_at,
                positions_count=len(current_keys),
            )
        return 0, 0

    opened = 0
    closed = 0
    pos_by_key = {
        _position_key(address, position=p)[1]: p
        for p in current_positions
    }

    # New positions (skip any in the baseline — pre-existing when wallet was added)
    baseline = db.get_wallet_baseline(address) or set()
    for key in current_keys - prev_keys:
        pos = pos_by_key.get(key)
        if not pos:
            continue
        if _baseline_matches_position(baseline, pos):
            continue
        cid = pos.get("conditionId")
        size = pos.get("currentValue", 0)
        title = pos.get("title", "")
        price = pos.get("curPrice", 0)
        outcome = pos.get("outcome")
        log.info("Monitor: %s NEW position — %s %s @%.3f size=%s",
                 label, outcome, title[:45], price, f"${size:,.0f}")

        if will_copy:
            copy_settings = db.get_copy_trade_settings()
            max_wallet_open = copy_settings["per_wallet_cap"] if copy_settings["cap_enabled"] else None
            max_total_open = copy_settings["total_open_cap"] if copy_settings["cap_enabled"] else None
            decision = db.inspect_copy_trade_open(
                address,
                pos,
                size_usd=COPY_SIZE_USD,
                max_wallet_open=max_wallet_open,
                max_total_open=max_total_open,
            )
            if not decision["ok"]:
                log.info("Monitor: skipped %s — %s", label, decision["reason"])
                _record_event(
                    source="wallet_monitor",
                    wallet=address,
                    label=label,
                    event_type="new_position",
                    status="blocked",
                    reason_code=decision["reason_code"],
                    reason=decision["reason"],
                    condition_id=cid,
                    outcome_name=outcome,
                    market_title=title,
                    price=price,
                    position_value_usd=size,
                    checked_at=checked_at,
                    positions_count=len(current_keys),
                )
                continue
            t_id = db.open_copy_trade(
                address,
                label,
                pos,
                size_usd=COPY_SIZE_USD,
                max_wallet_open=max_wallet_open,
                max_total_open=max_total_open,
            )
            if t_id:
                opened += 1
                log.info("Monitor: AUTO-MIRRORED %s → trade #%d ($%.0f paper)",
                         label, t_id, COPY_SIZE_USD)
                _status["new_trades_found"] += 1
                _record_event(
                    source="wallet_monitor",
                    wallet=address,
                    label=label,
                    event_type="new_position",
                    status="mirrored",
                    reason_code="opened",
                    reason=f"Paper copy trade opened as trade #{t_id}.",
                    condition_id=cid,
                    outcome_name=outcome,
                    market_title=title,
                    price=price,
                    position_value_usd=size,
                    checked_at=checked_at,
                    positions_count=len(current_keys),
                    details={"trade_id": t_id, "size_usd": COPY_SIZE_USD},
                )
            else:
                log.debug("Monitor: copy trade open failed after ready check for %s", cid[:16])
                _record_event(
                    source="wallet_monitor",
                    wallet=address,
                    label=label,
                    event_type="new_position",
                    status="error",
                    reason_code="open_failed",
                    reason="Copy trade could not be opened after ready check passed.",
                    condition_id=cid,
                    outcome_name=outcome,
                    market_title=title,
                    price=price,
                    position_value_usd=size,
                    checked_at=checked_at,
                    positions_count=len(current_keys),
                )
        else:
            log.info("Monitor: %s scored below copy threshold — not mirroring", label)
            _record_event(
                source="wallet_monitor",
                wallet=address,
                label=label,
                event_type="new_position",
                status="ignored",
                reason_code="wallet_not_copyable",
                reason="New position seen, but wallet is not currently enabled for auto-copy.",
                condition_id=cid,
                outcome_name=outcome,
                market_title=title,
                price=price,
                position_value_usd=size,
                checked_at=checked_at,
                positions_count=len(current_keys),
            )

    # Exited positions — close our mirrors
    open_copy = {
        _position_key(t.get("copy_wallet"), trade=t): t
        for t in db.get_trades(status="open", limit=500)
        if t.get("trade_type") == "copy"
        and t.get("copy_wallet") == address
        and (_position_key(t.get("copy_wallet"), trade=t)[1])
    }
    for key in prev_keys - current_keys:
        trade = open_copy.get((address.lower(), key))
        if trade:
            pnl = db.close_trade(trade["id"], exit_price_a=trade["entry_price_a"],
                                 notes=f"auto-close: {label} exited position")
            closed += 1
            log.info("Monitor: AUTO-CLOSED copy trade #%d (%s exited) pnl=$%.2f",
                     trade["id"], label, pnl or 0)
            _record_event(
                source="wallet_monitor",
                wallet=address,
                label=label,
                event_type="position_closed",
                status="closed",
                reason_code="wallet_exited_position",
                reason=f"Mirrored trade #{trade['id']} closed because watched wallet exited the position.",
                condition_id=trade.get("copy_condition_id"),
                outcome_name=trade.get("copy_outcome"),
                market_title=trade.get("event"),
                position_value_usd=trade.get("size_usd"),
                checked_at=checked_at,
                positions_count=len(current_keys),
                details={"trade_id": trade["id"], "pnl": pnl},
            )

    if not opened and not closed:
        _record_event(
            source="wallet_monitor",
            wallet=address,
            label=label,
            event_type="wallet_polled",
            status="no_change",
            reason=f"Polled successfully: {len(current_keys)} live positions, no new copy-trading actions.",
            checked_at=checked_at,
            positions_count=len(current_keys),
        )
    else:
        _record_event(
            source="wallet_monitor",
            wallet=address,
            label=label,
            event_type="wallet_polled",
            status="changes_seen",
            reason=f"Poll processed: {opened} mirrored, {closed} closed, {len(current_keys)} live positions.",
            checked_at=checked_at,
            positions_count=len(current_keys),
            details={"opened": opened, "closed": closed},
        )

    return opened, closed


# ── Poll loop ──────────────────────────────────────────────────────────────────

def _load_state():
    """Load persisted wallet state from disk."""
    global _known_positions
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            _known_positions = {k: set(v) for k, v in data.get("positions", {}).items()}
            log.info("Monitor: loaded state for %d wallets", len(_known_positions))
        except Exception as e:
            log.warning("Monitor: could not load state: %s", e)


def _save_state():
    STATE_FILE.parent.mkdir(exist_ok=True)
    try:
        STATE_FILE.write_text(json.dumps(
            {"positions": {k: list(v) for k, v in _known_positions.items()},
             "saved_at": time.time()}
        ))
    except Exception as e:
        log.warning("Monitor: could not save state: %s", e)


def _auto_drop_check():
    """After re-scoring, deactivate wallets that fell below MIN_SCORE and close their trades."""
    for row in db.get_watched_wallets(active_only=True):
        address = row["address"]
        label = row["label"]
        score = row.get("score") or 0
        if score > 0 and score < MIN_SCORE:
            reason = f"score={score:.1f} dropped below MIN_SCORE={MIN_SCORE}"
            log.warning("Monitor: AUTO-DROP %s — %s", label, reason)
            db.deactivate_watched_wallet(address, reason=reason)
            open_copy = [
                t for t in db.get_trades(status="open", limit=500)
                if t.get("trade_type") == "copy" and t.get("copy_wallet") == address
            ]
            for trade in open_copy:
                pnl = db.close_trade(
                    trade["id"],
                    exit_price_a=trade.get("entry_price_a", 0.5),
                    notes=f"auto-drop: {label} {reason}",
                )
                log.info("Monitor: closed copy trade #%d (auto-drop) pnl=$%.2f", trade["id"], pnl or 0)
            _known_positions.pop(address, None)


def _score_all():
    """Score all watched wallets, update _status and persist to DB."""
    wallets = _get_active_wallets()
    log.info("Monitor: scoring %d wallets...", len(wallets))
    for address, label in wallets.items():
        try:
            result = score_wallet(address, label)
            _status["wallets"][address] = result
            db.update_wallet_score(address, result)
            log.info("Monitor: %s score=%.0f classification=%s will_copy=%s",
                     label, result["score"], result["classification"], result["will_copy"])
        except Exception as e:
            log.warning("Monitor: scoring failed for %s: %s", label, e)
    _status["last_score"] = time.time()
    _auto_drop_check()


def _poll_loop():
    """Main background polling loop."""
    # Seed DB from legacy hardcoded dict on first boot
    if not db.get_watched_wallets(active_only=False):
        for addr, lbl in _LEGACY_WALLETS.items():
            db.add_watched_wallet(addr, lbl, added_by="legacy_seed")
        log.info("Monitor: seeded %d wallets from legacy WATCHED_WALLETS", len(_LEGACY_WALLETS))

    _load_state()
    _score_all()   # Initial score

    last_scored = time.time()

    while not _stop_event.is_set():
        t0 = time.time()
        total_opened = total_closed = 0

        for address, label in _get_active_wallets().items():
            score_data = _status["wallets"].get(address, {})
            will_copy = score_data.get("will_copy", False)
            try:
                opened, closed = _check_wallet(address, label, will_copy)
                total_opened += opened
                total_closed += closed
            except Exception as e:
                log.warning("Monitor: poll failed for %s: %s", label, e)

        _status["last_poll"] = time.time()
        _status["polls_run"] += 1
        if total_opened or total_closed:
            log.info("Monitor: poll complete — %d opened, %d closed in %.1fs",
                     total_opened, total_closed, time.time() - t0)
        _save_state()

        # Re-score periodically
        if time.time() - last_scored > SCORE_INTERVAL:
            _score_all()
            last_scored = time.time()

        _stop_event.wait(POLL_INTERVAL)


# ── WebSocket price feed ───────────────────────────────────────────────────────

async def _ws_price_feed():
    """Subscribe to open copy trade markets and update midpoint prices."""
    import websockets

    while not _stop_event.is_set():
        # Collect token_ids for all open copy trades
        open_copy = [
            t for t in db.get_trades(status="open", limit=200)
            if t.get("trade_type") == "copy" and t.get("token_id_a")
        ]
        if not open_copy:
            await asyncio.sleep(30)
            continue

        token_ids = list({t["token_id_a"] for t in open_copy})
        log.debug("WS: subscribing to %d copy trade markets", len(token_ids))

        try:
            async with websockets.connect(WS_URI, open_timeout=10) as ws:
                await ws.send(json.dumps({"assets_ids": token_ids, "type": "Market"}))
                deadline = time.time() + 60   # resubscribe every 60s to pick up new trades

                while not _stop_event.is_set() and time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        msgs = json.loads(raw)
                        if not isinstance(msgs, list):
                            continue
                        for msg in msgs:
                            if msg.get("event_type") == "last_trade_price":
                                asset_id = msg.get("asset_id")
                                price = float(msg.get("price", 0))
                                if asset_id and price:
                                    log.debug("WS price: %s → %.4f", asset_id[:16], price)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            log.debug("WS feed error: %s — reconnecting in 15s", e)
            await asyncio.sleep(15)


def _ws_thread_target():
    """Run the async WebSocket feed in its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_ws_price_feed())
    except Exception as e:
        log.exception("WS feed thread crashed: %s", e)
    finally:
        loop.close()
        _update_running_status()


def _poll_thread_target():
    try:
        _poll_loop()
    except Exception as e:
        log.exception("Poll thread crashed: %s", e)
    finally:
        _update_running_status()


# ── Public API ─────────────────────────────────────────────────────────────────

def start():
    """Start the wallet monitor (polling + WebSocket). Call once from server.py."""
    global _poll_thread, _ws_thread

    poll_alive = _poll_thread is not None and _poll_thread.is_alive()
    ws_alive = _ws_thread is not None and _ws_thread.is_alive()
    if poll_alive or ws_alive:
        _update_running_status()
        log.warning("Monitor already running")
        return

    _stop_event.clear()

    _poll_thread = threading.Thread(target=_poll_thread_target, name="wallet-monitor", daemon=True)
    _poll_thread.start()

    _ws_thread = threading.Thread(target=_ws_thread_target, name="ws-price-feed", daemon=True)
    _ws_thread.start()

    _update_running_status()
    log.info("Wallet monitor started — polling every %ds, WS price feed active", POLL_INTERVAL)


def stop(join_timeout: float = 5.0):
    global _poll_thread, _ws_thread
    _stop_event.set()
    _save_state()

    current = threading.current_thread()
    for name, thread in (("poll", _poll_thread), ("ws", _ws_thread)):
        if thread and thread.is_alive() and thread is not current:
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                log.warning("Monitor %s thread did not stop within %.1fs", name, join_timeout)

    _poll_thread = None
    _ws_thread = None
    _update_running_status()
    log.info("Wallet monitor stopped")


def get_status() -> dict:
    return {
        "running": _status["running"],
        "last_poll": _status["last_poll"],
        "last_score": _status["last_score"],
        "polls_run": _status["polls_run"],
        "new_trades_found": _status["new_trades_found"],
        "wallets": _status["wallets"],
        "poll_interval_s": POLL_INTERVAL,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import log_setup
    from dotenv import load_dotenv
    load_dotenv()
    log_setup.init_logging()

    print("Scoring watched wallets...\n")
    for address, label in _get_active_wallets().items():
        r = score_wallet(address, label)
        print(f"{'─'*55}")
        print(f"  {label:20s}  score={r['score']:5.1f}  [{r['classification'].upper()}]  copy={r['will_copy']}")
        b = r["breakdown"]
        if b:
            print(f"  trades={b.get('trade_count','?')}  vol=${b.get('total_volume_usd',0):,.0f}  "
                  f"avg=${b.get('avg_size_usd',0):,.0f}  /mo={b.get('trades_per_month','?')}")
            print(f"  focus: {b.get('top_category','?')} ({b.get('top_cat_pct','?')}%)  "
                  f"cats={b.get('n_categories','?')}  unrealised=${b.get('unrealised_pnl',0):+,.0f}")
            if "components" in b:
                c = b["components"]
                print(f"  scores: sample={c['sample']:.0f} size={c['size']:.0f} "
                      f"focus={c['focus']:.0f} freq={c['frequency']:.0f} "
                      f"pnl={c['pnl']:.0f} buy_hold={c['buy_hold']:.0f}")

    print(f"\n{'─'*55}")
    print("Starting live monitor (Ctrl+C to stop)...")
    start()
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        stop()
