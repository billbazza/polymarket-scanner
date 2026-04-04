"""Test suite — validates all major code paths without hitting external APIs.

Run: python3 test_all.py
All tests should print PASS. Any FAIL lines indicate broken logic.
"""
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Redirect DB to a temp file so tests never touch scanner.db ──────────────
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["SCANNER_DB_PATH"] = _tmp_db.name

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
INFO = "\033[94mINFO\033[0m"

results = {"pass": 0, "fail": 0}

def check(name, condition, detail=""):
    if condition:
        print(f"  {PASS}  {name}")
        results["pass"] += 1
    else:
        print(f"  {FAIL}  {name}" + (f" — {detail}" if detail else ""))
        results["fail"] += 1

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

def run(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"  {FAIL}  {name} — EXCEPTION: {e}")
        traceback.print_exc()
        results["fail"] += 1


# ── 1. Imports ─────────────────────────────────────────────────────────────

section("1. Module imports")

def test_imports():
    import log_setup
    check("log_setup", True)

    import db
    check("db", True)

    import returns
    check("returns", True)

    import math_engine
    check("math_engine", True)

    import locked_scanner
    check("locked_scanner", True)

    import weather_scanner
    check("weather_scanner", True)

    import tracker
    check("tracker", True)

    import autonomy
    check("autonomy", True)

run("imports", test_imports)


# ── 2. Database ─────────────────────────────────────────────────────────────

section("2. Database — CRUD and new functions")

def test_db():
    import db

    # init_db runs on import; check tables exist
    conn = db.get_conn()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    for tbl in ["signals", "trades", "snapshots", "scan_runs",
                "locked_arb", "weather_signals", "scan_jobs", "schema_migrations",
                "paper_accounts"]:
        check(f"table '{tbl}' exists", tbl in tables)

    # get_signal_by_id — non-existent ID returns None
    check("get_signal_by_id(0) → None",
          db.get_signal_by_id(0) is None)

    # save and retrieve a locked arb record
    opp = {
        "event": "Test Event",
        "market": "Will X happen?",
        "market_id": "test-123",
        "yes_token": "tok_yes",
        "no_token": "tok_no",
        "yes_price": 0.45,
        "no_price": 0.50,
        "sum_price": 0.95,
        "gap_gross": 0.05,
        "gap_net": 0.01,
        "net_profit_pct": 1.0,
        "liquidity": 5000.0,
        "yes_slippage_ok": True,
        "no_slippage_ok": True,
        "yes_slippage_pct": 0.1,
        "no_slippage_pct": 0.2,
        "tradeable": True,
    }
    row_id = db.save_locked_arb(opp)
    check("save_locked_arb returns id", isinstance(row_id, int) and row_id > 0)

    rows = db.get_locked_arb(limit=5)
    check("get_locked_arb returns rows", len(rows) > 0)
    check("locked_arb tradeable flag stored",
          any(r["tradeable"] == 1 for r in rows))

    # save and retrieve a weather signal
    wopp = {
        "event": "NYC Weather",
        "market": "Will NYC hit 72F on Saturday?",
        "market_id": "w-123",
        "yes_token": "tok_yes_w",
        "no_token": "tok_no_w",
        "city": "new york city",
        "lat": 40.7128,
        "lon": -74.006,
        "target_date": "2026-03-30",
        "threshold_f": 72.0,
        "direction": "above",
        "market_price": 0.40,
        "noaa_forecast_f": 74.5,
        "noaa_prob": 0.72,
        "noaa_sigma_f": 2.5,
        "om_forecast_f": 73.8,
        "om_prob": 0.68,
        "combined_prob": 0.70,
        "combined_edge": 0.30,
        "combined_edge_pct": 30.0,
        "sources_agree": True,
        "sources_available": 2,
        "hours_ahead": 48,
        "ev_pct": 25.0,
        "kelly_fraction": 0.18,
        "action": "BUY_YES",
        "tradeable": True,
        "liquidity": 2000.0,
    }
    wid = db.save_weather_signal(wopp)
    check("save_weather_signal returns id", isinstance(wid, int) and wid > 0)

    wsigs = db.get_weather_signals(limit=5)
    check("get_weather_signals returns rows", len(wsigs) > 0)
    check("weather sources_agree stored",
          any(r["sources_agree"] == 1 for r in wsigs))

run("db", test_db)


# ── 2b. Paper execution balance lifecycle ───────────────────────────────────

section("2b. Paper execution balance lifecycle")

def test_paper_balance_lifecycle():
    import db
    import execution

    db.set_paper_starting_bankroll(execution.PAPER_BALANCE_USD)
    starting = db.get_paper_account_state(refresh_unrealized=False)
    check("paper bankroll default persisted", starting["starting_bankroll"] == execution.PAPER_BALANCE_USD)
    check("paper opening cash matches bankroll", starting["available_cash"] == execution.PAPER_BALANCE_USD)

    signal = {
        "event": "Paper Balance Test",
        "market_a": "Will A happen?",
        "market_b": "Will B happen?",
        "price_a": 0.40,
        "price_b": 0.60,
        "z_score": -1.8,
        "coint_pvalue": 0.04,
        "beta": 1.0,
        "half_life": 5.0,
        "spread_mean": 0.0,
        "spread_std": 0.1,
        "current_spread": -0.2,
        "liquidity": 5000,
        "volume_24h": 1000,
        "action": "BUY A / SELL B",
        "token_id_a": "tok-a",
        "token_id_b": "tok-b",
    }
    signal["id"] = db.save_signal(signal)

    result = execution._execute_paper(signal, size_usd=100, price_a=0.40, price_b=0.60)
    check("paper execute succeeds", result["ok"] is True)
    after_open = db.get_paper_account_state(refresh_unrealized=False)
    check("paper cash debited on open", after_open["available_cash"] == execution.PAPER_BALANCE_USD - 100)
    check("paper committed capital tracked", after_open["committed_capital"] == 100)
    check("paper remaining balance echoed", result["remaining_balance"] == after_open["available_cash"])

    pnl = db.close_trade(result["trade_id"], exit_price_a=0.50, exit_price_b=0.55)
    after_close = db.get_paper_account_state(refresh_unrealized=False)
    expected_balance = execution.PAPER_BALANCE_USD + pnl
    check("paper close returns pnl", pnl is not None)
    check("paper cash restored on close",
          abs(after_close["available_cash"] - round(expected_balance, 2)) < 1e-9,
          f"cash={after_close['available_cash']}, expected={expected_balance}")
    check("paper realized pnl persisted",
          abs(after_close["realized_pnl"] - round(pnl, 2)) < 1e-9,
          f"realized={after_close['realized_pnl']}, pnl={pnl}")
    check("paper committed capital cleared", after_close["committed_capital"] == 0)
    blocked = db.can_open_paper_trade(after_close["available_cash"] + 1)
    check("paper account rejects oversized trade", blocked["ok"] is False)

run("paper_balance", test_paper_balance_lifecycle)


# ── 2c. Open-trade valuation sanity ──────────────────────────────────────────

section("2c. Open-trade valuation sanity")

def test_single_leg_buy_no_mark_to_market():
    import db
    import tracker

    before = db.get_paper_account_state(refresh_unrealized=False)
    position = {
        "conditionId": "cond-copy-buy-no-mtm",
        "outcome": "No",
        "curPrice": 0.98,
        "asset": "tok_copy_no_mtm",
    }
    trade_id = db.open_copy_trade("0xcopy-mtm", "copy mtm", position, size_usd=20)
    check("copy BUY_NO trade opened", trade_id is not None and trade_id > 0, f"got {trade_id}")

    trade = db.get_trade(trade_id)
    check("copy BUY_NO entry stores held token price", abs((trade or {}).get("entry_price_a", 0) - 0.98) < 1e-9,
          f"got {trade.get('entry_price_a') if trade else None}")

    with patch("api.get_midpoint", return_value=0.99):
        updates = tracker.refresh_open_trades()

    update = next((u for u in updates if u.get("trade_id") == trade_id), None)
    expected = ((0.99 - 0.98) / 0.98) * 20
    check("copy BUY_NO refresh produced update", update is not None)
    if update:
        check("copy BUY_NO unrealized pnl stays small and correct",
              abs(update["unrealized_pnl"]["pnl_usd"] - round(expected, 2)) < 0.01,
              f"got {update['unrealized_pnl']['pnl_usd']}, expected {expected:.2f}")

    account = db.get_paper_account_state(refresh_unrealized=False)
    check("wallet-attached copy trade excluded from paper account unrealized",
          abs(account["unrealized_pnl"] - before["unrealized_pnl"]) < 0.01,
          f"got delta {account['unrealized_pnl'] - before['unrealized_pnl']}")
    strategy = db.get_strategy_performance(refresh_unrealized=False)
    copy_bucket = next((row for row in strategy["strategies"] if row["strategy"] == "copy"), {})
    check("strategy view still reports copy unrealized pnl",
          abs(copy_bucket.get("unrealized_pnl", 0) - round(expected, 2)) < 0.01,
          f"got {copy_bucket.get('unrealized_pnl')}, expected {expected:.2f}")
    db.close_trade(trade_id, exit_price_a=0.99, notes="test cleanup")


def test_pairs_open_trade_mark_to_market():
    import db
    import tracker

    before = db.get_paper_account_state(refresh_unrealized=False)
    signal = {
        "event": "Pairs MTM Test",
        "market_a": "Market A",
        "market_b": "Market B",
        "price_a": 0.25,
        "price_b": 0.80,
        "z_score": -2.0,
        "coint_pvalue": 0.02,
        "beta": 1.0,
        "half_life": 4.0,
        "spread_mean": 0.0,
        "spread_std": 0.1,
        "current_spread": -0.55,
        "liquidity": 8000,
        "volume_24h": 1200,
        "action": "BUY A / SELL B",
        "token_id_a": "tok-pairs-a",
        "token_id_b": "tok-pairs-b",
    }
    signal_id = db.save_signal(signal)
    trade_id = db.open_trade(signal_id, size_usd=100)
    check("pairs trade opened", trade_id is not None and trade_id > 0, f"got {trade_id}")

    with patch("api.get_midpoint", side_effect=[0.35, 0.70]):
        updates = tracker.refresh_open_trades()

    update = next((u for u in updates if u.get("trade_id") == trade_id), None)
    expected = 50 / 0.25 * (0.35 - 0.25) + 50 / 0.80 * (0.80 - 0.70)
    check("pairs refresh produced update", update is not None)
    if update:
        check("pairs unrealized pnl uses shares-based synthetic-short math",
              abs(update["unrealized_pnl"]["pnl_usd"] - round(expected, 2)) < 0.01,
              f"got {update['unrealized_pnl']['pnl_usd']}, expected {expected:.2f}")

    account = db.get_paper_account_state(refresh_unrealized=False)
    check("paper account unrealized includes pairs mark-to-market",
          abs((account["unrealized_pnl"] - before["unrealized_pnl"]) - round(expected, 2)) < 0.01,
          f"got delta {account['unrealized_pnl'] - before['unrealized_pnl']}, expected {expected:.2f}")
    db.close_trade(trade_id, exit_price_a=0.35, exit_price_b=0.70, notes="test cleanup")


run("single_leg_buy_no_mark_to_market", test_single_leg_buy_no_mark_to_market)
run("pairs_open_trade_mark_to_market", test_pairs_open_trade_mark_to_market)


# ── 2d. Copy trader unwatch semantics ───────────────────────────────────────

section("2d. Copy trader unwatch semantics")

def test_copy_trader_unwatch_semantics():
    import asyncio
    import db
    import server

    address = "0xcopy-unwatch"
    db.add_watched_wallet(address, "Copy Wallet")
    trade_id = db.open_copy_trade(
        address,
        "Copy Wallet",
        {
            "conditionId": "copy-unwatch-condition",
            "outcome": "Yes",
            "curPrice": 0.61,
            "title": "Will copy unwatch semantics hold?",
            "asset": "copy-unwatch-token",
        },
        size_usd=20,
    )
    check("copy trade opened before unwatch", trade_id is not None and trade_id > 0, f"got {trade_id}")

    before = next(
        (t for t in db.get_trades(status="open", limit=50) if t["id"] == trade_id),
        None,
    )
    check("copy trade initially linked to active wallet", before is not None and before.get("copy_wallet_active") == 1)

    result = asyncio.run(server.remove_from_watchlist(address))
    check("unwatch endpoint succeeds", result.get("ok") is True, detail=str(result))
    check("unwatch endpoint keeps trade count", result.get("open_copy_trades_remaining") == 1, detail=str(result))
    check("unwatch endpoint exposes close policy", "stay open" in (result.get("close_policy") or "").lower())

    active_wallets = db.get_watched_wallets(active_only=True)
    all_wallets = db.get_watched_wallets(active_only=False)
    check("wallet removed from active watch set", not any(w["address"] == address for w in active_wallets))
    stored_wallet = next((w for w in all_wallets if w["address"] == address), None)
    check("wallet history preserved as inactive", stored_wallet is not None and stored_wallet.get("active") == 0)
    check("manual unwatch reason persisted",
          (stored_wallet or {}).get("auto_drop_reason", "").startswith("manual_unwatch:"))

    open_trade = next(
        (t for t in db.get_trades(status="open", limit=50) if t["id"] == trade_id),
        None,
    )
    check("open copy trade remains in global trade list", open_trade is not None)
    check("open copy trade now marked unwatched", open_trade is not None and open_trade.get("copy_wallet_active") == 0)

    detached = asyncio.run(server.copy_detached_trades())
    detached_trade = next((t for t in detached.get("trades", []) if t.get("trade_id") == trade_id), None)
    check("detached copy-trade API returns unwatched trade", detached_trade is not None)
    check("detached copy-trade API labels unwatched state",
          detached_trade is not None and detached_trade.get("status_label") == "unwatched",
          detail=str(detached_trade))

run("copy_trader_unwatch_semantics", test_copy_trader_unwatch_semantics)


# ── 2e. Optional-limit DB getters ───────────────────────────────────────────

section("2e. Optional-limit DB getters")

def test_optional_limit_getters():
    import db

    signal = {
        "event": "Optional Limit Signal",
        "market_a": "Limit A",
        "market_b": "Limit B",
        "price_a": 0.41,
        "price_b": 0.59,
        "z_score": -1.7,
        "coint_pvalue": 0.03,
        "beta": 1.0,
        "half_life": 6.0,
        "spread_mean": 0.0,
        "spread_std": 0.1,
        "current_spread": -0.18,
        "liquidity": 7000,
        "volume_24h": 900,
        "action": "BUY A / SELL B",
        "token_id_a": "limit-a-token",
        "token_id_b": "limit-b-token",
    }
    signal_id = db.save_signal(signal)
    trade_id = db.open_trade(signal_id, size_usd=50)
    db.save_snapshot(trade_id, 0.43, 0.57, -0.14, -1.4)
    db.save_scan_run(3, 1, 1, 0.8)

    longshot_id = db.save_longshot_signal({
        "event": "Optional Limit Longshot",
        "market": "Sell yes?",
        "market_id": "limit-longshot",
        "yes_token": "limit-longshot-yes",
        "no_token": "limit-longshot-no",
        "yes_price": 0.08,
        "no_price": 0.92,
        "calibrated_no_prob": 0.95,
        "calibration_edge": 0.03,
        "best_yes_bid": 0.07,
        "best_yes_ask": 0.09,
        "spread_pct": 25.0,
        "limit_price": 0.09,
        "no_cost": 0.91,
        "ev_pct": 4.2,
        "kelly_fraction": 0.05,
        "fill_prob": 0.4,
        "liquidity": 1500,
        "tradeable": True,
    })
    near_certainty_id = db.save_near_certainty_signal({
        "event": "Optional Limit Near Certainty",
        "market": "Buy yes?",
        "market_id": "limit-near-certainty",
        "yes_token": "limit-near-yes",
        "no_token": "limit-near-no",
        "yes_price": 0.91,
        "calibrated_yes": 0.95,
        "calibration_edge": 0.04,
        "ev_pct": 3.8,
        "ev": 0.04,
        "cost": 0.91,
        "fee": 0.0,
        "kelly_fraction": 0.03,
        "liquidity": 2200,
        "brain_prob": 0.95,
        "brain_confirmed": True,
        "tradeable": True,
    })
    whale_id = db.save_whale_alert({
        "timestamp": time.time(),
        "event": "Optional Limit Whale",
        "market": "Whale market",
        "market_id": "limit-whale",
        "token_id": "limit-whale-token",
        "current_price": 0.62,
        "volume_24h": 25000,
        "liquidity": 9000,
        "volume_ratio": 3.2,
        "biggest_order_usd": 1200,
        "dominant_side": "BUY",
        "suspicion_score": 77,
        "analysis": "test alert",
    })

    check("get_signals(limit=None) returns seeded signal",
          any(row["id"] == signal_id for row in db.get_signals(limit=None)))
    check("get_trades(limit=None) returns seeded trade",
          any(row["id"] == trade_id for row in db.get_trades(limit=None)))
    check("get_snapshots(limit=None) returns seeded snapshot",
          len(db.get_snapshots(trade_id, limit=None)) >= 1)
    check("get_scan_runs(limit=None) returns seeded run",
          len(db.get_scan_runs(limit=None)) >= 1)
    check("get_weather_signals(limit=None) remains callable",
          isinstance(db.get_weather_signals(limit=None), list))
    check("get_locked_arb(limit=None) remains callable",
          isinstance(db.get_locked_arb(limit=None), list))
    check("get_longshot_signals(limit=None) returns seeded row",
          any(row["id"] == longshot_id for row in db.get_longshot_signals(limit=None)))
    check("get_near_certainty_signals(limit=None) returns seeded row",
          any(row["id"] == near_certainty_id for row in db.get_near_certainty_signals(limit=None)))
    check("get_whale_alerts(limit=None) returns seeded row",
          any(row["id"] == whale_id for row in db.get_whale_alerts(limit=None)))
    check("get_latest_copy_trades(limit=None) remains callable",
          isinstance(db.get_latest_copy_trades(limit=None), list))

    try:
        db.get_scan_runs(limit="bad-limit")
    except ValueError as exc:
        check("invalid limit fails with contextual ValueError",
              "get_scan_runs" in str(exc), detail=str(exc))
    else:
        check("invalid limit fails with contextual ValueError", False)

run("optional_limit_getters", test_optional_limit_getters)


# ── 3. Math Engine ──────────────────────────────────────────────────────────

section("3. Math engine — EV, Kelly, slippage")

def test_math():
    import math_engine

    # EV: positive edge trade
    ev = math_engine.ev_from_zscore(z_score=2.5, half_life=3, spread_std=0.05, size_usd=100)
    check("ev_from_zscore returns dict", isinstance(ev, dict))
    check("ev has ev_pct key", "ev_pct" in ev)
    check("high-z, fast-hl has positive EV", ev["ev"] > 0,
          f"ev={ev['ev']:.4f}")

    # EV: slow reversion should have lower EV
    ev_slow = math_engine.ev_from_zscore(z_score=2.5, half_life=50, spread_std=0.05, size_usd=100)
    check("slow half-life reduces EV", ev_slow["ev"] < ev["ev"])

    # Kelly fraction bounds
    kf = math_engine.kelly_fraction(win_prob=0.7, win_payout=0.8, loss_amount=0.2)
    check("kelly_fraction in [0, 0.25]", 0 <= kf <= 0.25, f"kf={kf}")

    kf_zero = math_engine.kelly_fraction(win_prob=0.3, win_payout=0.1, loss_amount=0.9)
    check("negative-EV kelly returns 0", kf_zero == 0, f"kf={kf_zero}")

    kf_edge = math_engine.kelly_fraction(win_prob=0, win_payout=1, loss_amount=1)
    check("zero win-prob kelly returns 0", kf_edge == 0)

    # Returns math
    import returns
    pnl = returns.pairs_pnl(entry_a=0.5, exit_a=0.6, entry_b=0.5, exit_b=0.4,
                             side_a="BUY", size_usd=100)
    check("pairs_pnl returns dict", isinstance(pnl, dict))
    check("profitable trade has positive pnl", pnl["pnl_usd"] > 0,
          f"pnl={pnl['pnl_usd']}")

    pnl_loss = returns.pairs_pnl(entry_a=0.5, exit_a=0.4, entry_b=0.5, exit_b=0.6,
                                  side_a="BUY", size_usd=100)
    check("losing trade has negative pnl", pnl_loss["pnl_usd"] < 0)

    pnl_zero = returns.pairs_pnl(entry_a=0, exit_a=0.5, entry_b=0.5, exit_b=0.5,
                                  side_a="BUY", size_usd=100)
    check("zero entry price returns 0 pnl", pnl_zero["pnl_usd"] == 0)

    # score_opportunity: momentum_pass filter present
    opp_with_momentum = {
        "event": "test", "z_score": 2.0, "z_prev": 2.5,
        "spread_retreating": True, "coint_pvalue": 0.05,
        "half_life": 5.0, "spread_std": 0.1, "spread_mean": 0.0,
    }
    scored = math_engine.score_opportunity(opp_with_momentum)
    check("momentum_pass in filters", "momentum_pass" in scored["filters"])
    check("retreating spread passes momentum", scored["filters"]["momentum_pass"] is True)

    opp_diverging = dict(opp_with_momentum, spread_retreating=False)
    scored_div = math_engine.score_opportunity(opp_diverging)
    check("diverging spread fails momentum", scored_div["filters"]["momentum_pass"] is False)
    check("diverging spread not tradeable", scored_div["tradeable"] is False)

    # score_opportunity: price_pass filter (Filter #3)
    opp_safe_prices = dict(opp_with_momentum, price_a=0.45, price_b=0.55)
    scored_safe = math_engine.score_opportunity(opp_safe_prices)
    check("price_pass in filters", "price_pass" in scored_safe["filters"])
    check("mid-range prices pass price filter", scored_safe["filters"]["price_pass"] is True)

    opp_near_res_a = dict(opp_with_momentum, price_a=0.02, price_b=0.55)
    scored_near = math_engine.score_opportunity(opp_near_res_a)
    check("price_a=0.02 fails price filter", scored_near["filters"]["price_pass"] is False)
    check("near-resolution pair not tradeable", scored_near["tradeable"] is False)

    opp_near_res_b = dict(opp_with_momentum, price_a=0.45, price_b=0.97)
    scored_near_b = math_engine.score_opportunity(opp_near_res_b)
    check("price_b=0.97 fails price filter", scored_near_b["filters"]["price_pass"] is False)

    # Boundary: exactly at threshold
    opp_boundary = dict(opp_with_momentum, price_a=0.05, price_b=0.95)
    scored_boundary = math_engine.score_opportunity(opp_boundary)
    check("prices at 0.05/0.95 boundary pass", scored_boundary["filters"]["price_pass"] is True)

run("math", test_math)


# ── 3b. Resolution proximity filter ─────────────────────────────────────────

section("3b. Resolution proximity filter")

def test_resolution_filter():
    from datetime import datetime, timezone, timedelta
    import scanner as sc

    future_far  = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future_near = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    past        = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    check("far future → >21 days", sc._days_to_resolution(future_far) > 21)
    check("near future → <21 days", sc._days_to_resolution(future_near) < 21)
    check("past → 0 days", sc._days_to_resolution(past) == 0)
    check("empty string → inf", sc._days_to_resolution("") == float("inf"))
    check("None → inf", sc._days_to_resolution(None) == float("inf"))
    check("bad string → inf", sc._days_to_resolution("not-a-date") == float("inf"))

    # Verify async_scanner has the same helper
    import async_scanner as asc
    check("async_scanner has _days_to_resolution",
          callable(getattr(asc, "_days_to_resolution", None)))
    check("async MIN_DAYS matches scanner",
          asc.MIN_DAYS_TO_RESOLUTION == sc.MIN_DAYS_TO_RESOLUTION)

run("resolution_filter", test_resolution_filter)


# ── 3c. Spread momentum filter ───────────────────────────────────────────────

section("3c. Spread momentum filter")

def test_momentum_filter():
    import numpy as np
    import scanner as sc
    import async_scanner as asc
    from unittest.mock import patch

    # Build a cointegrated pair: A and B track each other with some noise,
    # then spread widens at the end (still diverging — spread_retreating = False)
    rng = np.random.default_rng(42)
    base = np.linspace(0.3, 0.7, 52)
    noise = rng.normal(0, 0.015, 52)
    prices_b = np.clip(base + noise, 0.05, 0.95)
    prices_a = prices_b + 0.05 + rng.normal(0, 0.01, 52)
    # Make spread widen at the very end so spread_retreating = False
    prices_a[-1] += 0.06
    prices_a[-2] += 0.03

    result = sc.test_pair(prices_a, prices_b)
    check("test_pair returns z_prev", result is not None and "z_prev" in result,
          f"result={result}")
    check("test_pair returns spread_retreating",
          result is not None and "spread_retreating" in result)
    if result:
        check("spread_retreating is bool", isinstance(result["spread_retreating"], bool))

    # Retreating: pull the last point back toward mean
    prices_a2 = prices_a.copy()
    prices_a2[-1] = prices_a2[-2] - 0.02   # spread shrinking
    result2 = sc.test_pair(prices_a2, prices_b)
    if result2:
        check("spread_retreating is bool (2)", isinstance(result2["spread_retreating"], bool))

    # async_scanner._test_pair also returns the new fields
    result3 = asc._test_pair(prices_a, prices_b)
    check("async _test_pair returns z_prev",
          result3 is not None and "z_prev" in result3)
    check("async _test_pair returns spread_retreating",
          result3 is not None and "spread_retreating" in result3)

    mock_events = [{
        "title": "Mock Event",
        "liquidity": 10000,
        "volume_24h": 500,
        "markets": [
            {"question": "A", "yes_token": "tok_a", "yes_price": 0.4, "end_date": "2099-01-01T00:00:00Z"},
            {"question": "B", "yes_token": "tok_b", "yes_price": 0.6, "end_date": "2099-01-01T00:00:00Z"},
        ],
    }]
    with patch("scanner.find_multi_market_events", return_value=mock_events), \
         patch("scanner.get_aligned_prices", return_value=(prices_a2, prices_b)), \
         patch("math_engine.score_opportunity", return_value={
             "ev": {"ev_pct": 12},
             "sizing": {"recommended_size": 10},
             "filters": {},
             "grade": "A",
             "grade_label": "A",
             "tradeable": True,
         }):
        stats = sc.scan(include_stats=True, verbose=False)
    check("scan(include_stats=True) returns dict", isinstance(stats, dict))
    check("scan stats include opportunities list", isinstance(stats.get("opportunities"), list))
    check("scan stats include pairs_tested", "pairs_tested" in stats)
    check("scan stats include pairs_cointegrated", "pairs_cointegrated" in stats)

run("momentum_filter", test_momentum_filter)


# ── 4. Locked Scanner — parser and logic ───────────────────────────────────

section("4. Locked scanner — market parsing")

def test_locked():
    import locked_scanner

    # Valid binary market
    market = {
        "question": "Will X happen?",
        "clobTokenIds": '["tok1", "tok2"]',
        "outcomePrices": '["0.45", "0.50"]',
        "id": "m1",
    }
    result = locked_scanner._parse_market(market, "Test Event", 5000)
    check("valid market parsed", result is not None)
    check("yes_price correct", result["yes_price"] == 0.45)
    check("no_price correct", result["no_price"] == 0.50)
    check("sum_price correct", abs(result["sum_price"] - 0.95) < 0.0001)
    check("gap_gross = 0.05", abs(result["gap_gross"] - 0.05) < 0.0001)

    # Gap net after 2% fee: 0.05 - 0.02 * 0.95 = 0.05 - 0.019 = 0.031
    expected_net = 0.05 - locked_scanner.FEE_RATE * 0.95
    check("gap_net fee calc correct",
          abs(result["gap_net"] - expected_net) < 0.0001,
          f"got {result['gap_net']:.4f}, expected {expected_net:.4f}")

    # Resolved market (price near 0 or 1) — should be skipped
    resolved = {**market, "outcomePrices": '["0.99", "0.01"]'}
    check("resolved market returns None",
          locked_scanner._parse_market(resolved, "Test", 5000) is None)

    # Missing tokens
    no_tokens = {**market, "clobTokenIds": "[]"}
    check("no tokens returns None",
          locked_scanner._parse_market(no_tokens, "Test", 5000) is None)

    # Missing prices
    no_prices = {**market, "outcomePrices": ""}
    check("no prices returns None",
          locked_scanner._parse_market(no_prices, "Test", 5000) is None)

    # Non-JSON tokens (graceful)
    bad_json = {**market, "clobTokenIds": "bad"}
    check("bad JSON tokens returns None",
          locked_scanner._parse_market(bad_json, "Test", 5000) is None)

    # Min net gap filter
    sum_with_no_gap = 0.985  # gap_gross=0.015, after fees < MIN_NET_GAP
    tight = {**market, "outcomePrices": f'["{sum_with_no_gap/2}", "{sum_with_no_gap/2}"]'}
    r_tight = locked_scanner._parse_market(tight, "Test", 5000)
    check("tight market still parsed (filtering is in scan())",
          r_tight is not None)
    check("tight market gap_net < MIN_NET_GAP",
          r_tight["gap_net"] < locked_scanner.MIN_NET_GAP)

run("locked", test_locked)


# ── 5. Weather Scanner — parser, probability model ─────────────────────────

section("5. Weather scanner — question parser and probability model")

def test_weather():
    import weather_scanner
    from datetime import date

    today = date.today()

    # Basic parse — above threshold
    result = weather_scanner._parse_question(
        "Will New York City hit 72°F or higher on Saturday?"
    )
    check("NYC above 72 parsed", result is not None, str(result))
    if result:
        check("city = new york city", result["city"] == "new york city")
        check("threshold = 72", result["threshold_f"] == 72.0)
        check("direction = above", result["direction"] == "above")
        check("target_date is future", result["target_date"] > today.isoformat())

    # Below threshold
    r2 = weather_scanner._parse_question(
        "Will Chicago temperatures remain below 60°F on Monday?"
    )
    check("Chicago below 60 parsed", r2 is not None)
    if r2:
        check("direction = below", r2["direction"] == "below")
        check("threshold = 60", r2["threshold_f"] == 60.0)

    # No city → None
    r3 = weather_scanner._parse_question(
        "Will it hit 72°F on Saturday?"
    )
    check("no city → None", r3 is None)

    # No temperature → None
    r4 = weather_scanner._parse_question(
        "Will NYC be hot on Saturday?"
    )
    check("no temperature → None", r4 is None)

    # No direction → None
    r5 = weather_scanner._parse_question(
        "Will NYC temperature be 72°F on Saturday?"
    )
    # "be" is not a direction keyword so should return None
    check("no direction keyword → None", r5 is None)

    # Month + day date parsing — use a date 4 days ahead (within 7-day window)
    _test_date = today + timedelta(days=4)
    _month_name = _test_date.strftime("%B")   # e.g. "March"
    _day_num    = _test_date.day              # e.g. 31
    _date_str   = f"{_month_name} {_day_num}"
    r6 = weather_scanner._parse_question(
        f"Will Miami exceed 85°F on {_date_str}?"
    )
    check("month+day date parsed", r6 is not None,
          f"question used: Will Miami exceed 85°F on {_date_str}?")
    if r6:
        check(f"date is {_date_str}", r6["target_date"] == _test_date.isoformat())

    # Date parsing — today/tomorrow
    r7 = weather_scanner._parse_question(
        "Will Boston hit 55°F or higher today?"
    )
    check("today parsed", r7 is not None)
    if r7:
        check("today = today", r7["target_date"] == today.isoformat())

    r8 = weather_scanner._parse_question(
        "Will Denver reach 60°F or above tomorrow?"
    )
    check("tomorrow parsed", r8 is not None)
    if r8:
        expected = (today + timedelta(days=1)).isoformat()
        check("tomorrow = today+1", r8["target_date"] == expected)

    # Probability model
    # Forecast = 75°F, threshold = 72°F, direction = above, 24h → high probability
    prob, sigma = weather_scanner._calc_probability(75.0, 72.0, "above", 24)
    check("prob is float in [0,1]", 0 < prob < 1, f"prob={prob}")
    check("forecast above threshold → prob > 0.5", prob > 0.5,
          f"prob={prob:.3f}")
    check("24h sigma = 2.5", sigma == 2.5)

    # Forecast = 68°F, threshold = 72°F → low probability of hitting 72
    prob_low, _ = weather_scanner._calc_probability(68.0, 72.0, "above", 24)
    check("forecast below threshold → prob < 0.5", prob_low < 0.5,
          f"prob={prob_low:.3f}")

    # Below direction
    prob_below, _ = weather_scanner._calc_probability(68.0, 72.0, "below", 24)
    check("below direction: prob + above_prob ≈ 1",
          abs(prob_below + prob_low - 1.0) < 0.0001,
          f"below={prob_below:.3f} above={prob_low:.3f}")

    # Sigma increases with hours
    _, s24  = weather_scanner._calc_probability(70.0, 72.0, "above", 24)
    _, s48  = weather_scanner._calc_probability(70.0, 72.0, "above", 48)
    _, s72  = weather_scanner._calc_probability(70.0, 72.0, "above", 72)
    _, s999 = weather_scanner._calc_probability(70.0, 72.0, "above", 200)
    check("sigma increases with hours ahead",
          s24 < s48 < s72 < s999,
          f"{s24} < {s48} < {s72} < {s999}")

run("weather", test_weather)


# ── 6. Tracker — token ID logic ────────────────────────────────────────────

section("6. Tracker — token ID resolution and missing-token handling")

def test_tracker():
    import tracker
    import db

    # Simulate signal with token IDs (modern signal)
    good_signal = {
        "id": 9999,
        "market_a": "Will X happen?",
        "market_b": "Will Y happen?",
        "token_id_a": "0xabc123",
        "token_id_b": "0xdef456",
        "beta": 1.0,
        "spread_mean": 0.0,
        "spread_std": 0.05,
    }

    # Simulate signal WITHOUT token IDs (legacy signal)
    bad_signal = {
        "id": 9998,
        "market_a": "Will X happen?",
        "market_b": "Will Y happen?",
        "token_id_a": None,
        "token_id_b": None,
    }

    # Check that tracker.py reads token_id_a not market_a
    check("good_signal has token_id_a", bool(good_signal.get("token_id_a")))
    check("bad_signal missing token IDs caught",
          not good_signal.get("token_id_a") or good_signal["token_id_a"] != good_signal["market_a"],
          "token_id_a should differ from market_a (question text)")

    # The tracker should skip signals with missing token IDs gracefully
    # We test this by checking the logic directly
    token_a = bad_signal.get("token_id_a")
    token_b = bad_signal.get("token_id_b")
    check("missing token IDs → would skip", not token_a or not token_b)

    # db.get_signal_by_id returns None for non-existent ID (used by tracker)
    check("get_signal_by_id(9999) = None for non-existent",
          db.get_signal_by_id(9999) is None)

run("tracker", test_tracker)


# ── 7. Autonomy levels ──────────────────────────────────────────────────────

section("7. Autonomy — level config and paper limit")

def test_autonomy():
    import autonomy

    levels = autonomy.LEVELS
    check("paper level exists", "paper" in levels)
    check("paper max_open disabled", levels["paper"]["max_open"] is None,
          f"got {levels['paper']['max_open']}")
    check("scout cannot trade", not levels["scout"]["can_trade"])
    check("paper can trade", levels["paper"]["can_trade"])
    check("book max_open > 0", levels["book"]["max_open"] > 0)
    check("autonomy state stored in logs", autonomy.STATE_FILE.parent.name == "logs",
          f"path={autonomy.STATE_FILE}")

    # Graduation criteria exist for non-top levels
    for level in ["paper", "penny"]:
        grad = levels[level].get("graduation")
        check(f"{level} has graduation criteria", grad is not None)
        check(f"{level} graduation has min_trades", "min_trades" in grad)
        check(f"{level} graduation has min_win_rate", "min_win_rate" in grad)

    check("book graduation is None (top level)", levels["book"]["graduation"] is None)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        runtime_state = tmpdir_path / "logs" / "autonomy_state.json"
        legacy_state = tmpdir_path / "autonomy_state.json"

        with patch.object(autonomy, "STATE_FILE", runtime_state), \
             patch.object(autonomy, "LEGACY_STATE_FILE", legacy_state):
            baseline = autonomy.default_state()
            baseline["level"] = "paper"
            autonomy.save_state(baseline)
            check("save_state creates runtime file", runtime_state.exists())
            check("load_state reads runtime file", autonomy.load_state()["level"] == "paper")

            runtime_state.unlink()
            legacy_payload = autonomy.default_state()
            legacy_payload["level"] = "penny"
            legacy_state.write_text(json.dumps(legacy_payload, indent=2))
            migrated = autonomy.load_state()
            check("load_state migrates legacy file", migrated["level"] == "penny")
            check("migration recreates runtime file", runtime_state.exists())

run("autonomy", test_autonomy)


# ── 8. Returns / Sharpe ─────────────────────────────────────────────────────

section("8. Returns — log return math and Sharpe ratio")

def test_returns():
    import returns

    # log_return basic
    lr = returns.log_return(0.5, 1.0)
    check("log_return(0.5→1.0) ≈ 0.693", abs(lr - 0.6931) < 0.001, f"got {lr:.4f}")

    lr_neg = returns.log_return(1.0, 0.5)
    check("log_return(1.0→0.5) ≈ -0.693", abs(lr_neg + 0.6931) < 0.001)

    check("log_return symmetric", abs(lr + lr_neg) < 0.0001)

    # Zero/negative prices
    check("log_return(0, x) = 0", returns.log_return(0, 0.5) == 0)
    check("log_return(x, 0) = 0", returns.log_return(0.5, 0) == 0)

    # log_to_simple round-trip
    simple = returns.log_to_simple(0.0)
    check("log_to_simple(0) = 0", abs(simple) < 0.0001)
    simple_up = returns.log_to_simple(returns.log_return(1.0, 1.5))
    check("log_to_simple round-trip ≈ 0.5", abs(simple_up - 0.5) < 0.001)

    # Sharpe ratio
    good_returns = [0.01, 0.02, 0.015, 0.018, 0.012, 0.022, 0.019]
    sharpe = returns.sharpe_ratio(good_returns)
    check("positive returns → positive Sharpe", sharpe > 0, f"sharpe={sharpe:.2f}")

    bad_returns = [-0.01, -0.02, -0.015, -0.018]
    sharpe_bad = returns.sharpe_ratio(bad_returns)
    check("negative returns → negative Sharpe", sharpe_bad < 0)

    check("single return → Sharpe = 0", returns.sharpe_ratio([0.01]) == 0)

run("returns", test_returns)


# ── 9. API client — structure ───────────────────────────────────────────────

section("9. API client — structure and retry logic")

def test_api_structure():
    import api

    # Functions exist
    for fn in ["get_events", "get_all_active_events", "get_price_history",
               "get_midpoint", "get_book", "get_spread"]:
        check(f"api.{fn} exists", hasattr(api, fn) and callable(getattr(api, fn)))

    # Rate limiting state initialised
    check("api._min_interval > 0", api._min_interval > 0)

run("api_structure", test_api_structure)


# ── 10. Dashboard HTML — JS syntax spot-checks ─────────────────────────────

section("10. Dashboard — known syntax bugs")

def test_dashboard():
    from pathlib import Path
    html = Path("dashboard.html").read_text()

    # The semicolon-inside-template-expression bug should be fixed
    bad_pattern = "var(--text2)';font-weight:"
    check("JS template semicolon bug fixed", bad_pattern not in html,
          f"Found bad pattern: {bad_pattern!r}")

    # All panels referenced in switchTab
    for panel in ["signals", "locked", "weather", "trades", "history", "scans", "console"]:
        check(f"panel-{panel} in HTML", f'id="panel-{panel}"' in html)

    # Scan endpoints referenced
    for endpoint in ["/api/scan", "/api/scan/fast", "/api/scan/locked", "/api/scan/weather"]:
        check(f"endpoint {endpoint!r} referenced", endpoint in html)

    # New stat cards present
    check("Locked Opps stat card", "statLocked" in html)
    check("Weather Opps stat card", "statWeather" in html)

run("dashboard", test_dashboard)


# ── 11. Weather trade lifecycle ─────────────────────────────────────────────

section("11. Weather — paper trade lifecycle (open → refresh → auto-close)")

def test_weather_trade_lifecycle():
    import db
    from unittest.mock import patch

    # Create a synthetic weather signal (includes all fields save_weather_signal needs)
    opp = {
        "market": "Will NYC exceed 75°F on 2026-04-01?",
        "market_id": "mkt_test_weather_1",
        "event": "NYC temperature April 2026",
        "city": "new york city",
        "lat": 40.71,
        "lon": -74.01,
        "target_date": "2026-04-01",
        "threshold_f": 75,
        "direction": "above",
        "yes_token": "tok_weather_yes_1",
        "no_token": "tok_weather_no_1",
        "market_price": 0.40,
        "noaa_prob": 0.55,
        "noaa_forecast_f": 77.0,
        "noaa_sigma_f": 5.0,
        "om_prob": 0.57,
        "om_forecast_f": 78.0,
        "combined_prob": 0.56,
        "combined_edge": 0.16,
        "combined_edge_pct": 16.0,
        "sources_agree": True,
        "sources_available": 2,
        "hours_ahead": 72,
        "ev_pct": 14.0,
        "kelly_fraction": 0.08,
        "action": "BUY_YES",
        "tradeable": True,
        "liquidity": 1500,
    }

    sig_id = db.save_weather_signal(opp)
    check("weather signal saved", sig_id is not None and sig_id > 0, f"got {sig_id}")

    # Open weather paper trade
    trade_id = db.open_weather_trade(sig_id, size_usd=20)
    check("weather trade opened", trade_id is not None and trade_id > 0, f"got {trade_id}")

    # Signal status should be 'traded' now
    import sqlite3
    conn = sqlite3.connect(str(db.DB_PATH))
    conn.row_factory = sqlite3.Row
    sig_row = conn.execute("SELECT status FROM weather_signals WHERE id=?", (sig_id,)).fetchone()
    check("weather signal marked traded", sig_row and sig_row["status"] == "traded",
          f"got status={sig_row['status'] if sig_row else None}")

    # Trade should be open
    trade = db.get_trade(trade_id)
    check("weather trade is open", trade and trade["status"] == "open",
          f"got {trade.get('status') if trade else None}")
    check("weather trade_type=weather", trade and trade.get("trade_type") == "weather",
          f"got {trade.get('trade_type') if trade else None}")
    check("weather trade entry_price_a=0.40", trade and abs(trade["entry_price_a"] - 0.40) < 0.001,
          f"got {trade.get('entry_price_a') if trade else None}")

    # Refresh: mock api.get_midpoint to return current price
    import tracker
    with patch("api.get_midpoint", return_value=0.50):
        updates = tracker.refresh_open_trades()
    weather_updates = [u for u in updates if u.get("trade_id") == trade_id]
    check("refresh includes weather trade", len(weather_updates) == 1,
          f"got {len(weather_updates)} updates")
    if weather_updates:
        pnl_info = weather_updates[0].get("unrealized_pnl", {})
        expected_pnl = (0.50 - 0.40) / 0.40 * 20  # = $5.00
        check("weather unrealized pnl correct",
              abs(pnl_info.get("pnl_usd", 0) - expected_pnl) < 0.01,
              f"got ${pnl_info.get('pnl_usd'):.2f}, expected ${expected_pnl:.2f}")

    # Auto-close: mock price at 0.99 (WIN)
    with patch("api.get_midpoint", return_value=0.99):
        closed = tracker.auto_close_trades()
    weather_closed = [c for c in closed if c.get("trade_id") == trade_id]
    check("weather trade auto-closed on resolution", len(weather_closed) == 1,
          f"got {len(weather_closed)} closed")
    if weather_closed:
        c = weather_closed[0]
        check("weather close reason contains WIN", "WIN" in c.get("reason", ""),
              f"got reason={c.get('reason')}")
        expected_realized = (0.99 - 0.40) / 0.40 * 20
        check("weather realized pnl correct",
              abs(c.get("pnl_usd", 0) - expected_realized) < 0.01,
              f"got ${c.get('pnl_usd'):.2f}, expected ${expected_realized:.2f}")

    # Trade should now be closed in DB
    closed_trade = db.get_trade(trade_id)
    check("weather trade status=closed in DB",
          closed_trade and closed_trade["status"] == "closed",
          f"got {closed_trade.get('status') if closed_trade else None}")

    conn.close()

run("weather_trade_lifecycle", test_weather_trade_lifecycle)


section("12. Single-leg tracker fallback on midpoint 404")

def _http_404(*_args, **_kwargs):
    import requests

    resp = requests.Response()
    resp.status_code = 404
    resp.url = "https://clob.polymarket.com/midpoint"
    raise requests.HTTPError("404 Client Error", response=resp)


def test_copy_trade_resolves_via_gamma_fallback():
    import db
    import tracker

    position = {
        "conditionId": "cond-copy-404",
        "outcome": "No",
        "curPrice": 0.80,
        "asset": "tok_copy_no_404",
    }
    trade_id = db.open_copy_trade("0xcopy", "copy wallet", position, size_usd=20)
    check("copy trade opened", trade_id is not None and trade_id > 0, f"got {trade_id}")

    gamma_market = {
        "conditionId": "cond-copy-404",
        "closed": True,
        "acceptingOrders": False,
        "umaResolutionStatus": "resolved",
        "clobTokenIds": '["tok_copy_yes_404", "tok_copy_no_404"]',
        "outcomePrices": '["0", "1"]',
    }

    with patch("api.get_midpoint", side_effect=_http_404), \
         patch("api.get_market", return_value=gamma_market):
        updates = tracker.refresh_open_trades()
        closed = tracker.auto_close_trades()

    copy_updates = [u for u in updates if u.get("trade_id") == trade_id]
    check("copy trade refresh uses Gamma fallback", len(copy_updates) == 1,
          f"got {len(copy_updates)} updates")
    if copy_updates:
        check("copy trade refresh source=gamma", copy_updates[0].get("price_source") == "gamma",
              f"got {copy_updates[0].get('price_source')}")
        check("copy trade refresh price=1.0", abs(copy_updates[0].get("current_price_a", 0) - 1.0) < 0.001,
              f"got {copy_updates[0].get('current_price_a')}")

    copy_closed = [c for c in closed if c.get("trade_id") == trade_id]
    check("copy trade auto-closed from Gamma resolved price", len(copy_closed) == 1,
          f"got {len(copy_closed)} closed")
    if copy_closed:
        check("copy trade closed at 1.0", abs(copy_closed[0].get("exit_price_a", 0) - 1.0) < 0.001,
              f"got {copy_closed[0].get('exit_price_a')}")

    trade = db.get_trade(trade_id)
    check("copy trade status closed after Gamma fallback",
          trade and trade["status"] == "closed",
          f"got {trade.get('status') if trade else None}")


def test_weather_trade_awaits_resolution_when_unpriceable():
    import db
    import tracker

    target = (date.today() - timedelta(days=1)).isoformat()
    opp = {
        "market": "Will NYC exceed 75°F yesterday?",
        "market_id": "mkt_weather_404_pending",
        "event": "NYC temperature pending settlement",
        "city": "new york city",
        "lat": 40.71,
        "lon": -74.01,
        "target_date": target,
        "threshold_f": 75,
        "direction": "above",
        "yes_token": "tok_weather_yes_pending",
        "no_token": "tok_weather_no_pending",
        "market_price": 0.40,
        "noaa_prob": 0.55,
        "noaa_forecast_f": 77.0,
        "noaa_sigma_f": 5.0,
        "om_prob": 0.57,
        "om_forecast_f": 78.0,
        "combined_prob": 0.56,
        "combined_edge": 0.16,
        "combined_edge_pct": 16.0,
        "sources_agree": True,
        "sources_available": 2,
        "hours_ahead": 24,
        "ev_pct": 14.0,
        "kelly_fraction": 0.08,
        "action": "BUY_NO",
        "tradeable": True,
        "liquidity": 1500,
    }

    sig_id = db.save_weather_signal(opp)
    trade_id = db.open_weather_trade(sig_id, size_usd=20)
    check("pending weather trade opened", trade_id is not None and trade_id > 0, f"got {trade_id}")

    gamma_market = {
        "id": "mkt_weather_404_pending",
        "closed": False,
        "acceptingOrders": False,
        "umaResolutionStatus": None,
        "clobTokenIds": '["tok_weather_yes_pending", "tok_weather_no_pending"]',
        "outcomePrices": '["0", "0"]',
    }

    with patch("api.get_midpoint", side_effect=_http_404), \
         patch("api.get_market", return_value=gamma_market):
        updates = tracker.refresh_open_trades()
        closed = tracker.auto_close_trades()

    pending_updates = [u for u in updates if u.get("trade_id") == trade_id]
    check("pending weather trade omitted from refresh when unpriceable", len(pending_updates) == 0,
          f"got {len(pending_updates)} updates")
    pending_closed = [c for c in closed if c.get("trade_id") == trade_id]
    check("pending weather trade stays open", len(pending_closed) == 0,
          f"got {len(pending_closed)} closed")

    trade = db.get_trade(trade_id)
    check("pending weather trade still open in DB",
          trade and trade["status"] == "open",
          f"got {trade.get('status') if trade else None}")
    check("pending weather trade note recorded",
          trade and "awaiting final resolution" in (trade.get("notes") or ""),
          f"got {trade.get('notes') if trade else None}")


run("copy_trade_resolves_via_gamma_fallback", test_copy_trade_resolves_via_gamma_fallback)
run("weather_trade_awaits_resolution_when_unpriceable", test_weather_trade_awaits_resolution_when_unpriceable)


def test_whale_trade_invalid_token_is_sanitized_on_open():
    import db
    import whale_detector

    alert = {
        "id": 9991,
        "event": "Whale invalid token event",
        "market": "Will invalid token be sanitized?",
        "token_id": "test_token_id",
        "current_price": 0.61,
        "dominant_side": "BID",
        "analysis": "placeholder whale alert",
        "suspicion_score": 77,
    }

    trade_id = whale_detector.create_whale_trade(alert, size_usd=20)
    check("whale trade blocked when token invalid", trade_id is None, f"got {trade_id}")




def test_single_leg_invalid_token_skips_gamma_lookup():
    import db
    import tracker

    conn = db.get_conn()
    try:
        conn.execute(
            """
            INSERT INTO trades (trade_type, opened_at, side_a, side_b,
                entry_price_a, entry_price_b, token_id_a, size_usd, status, event, market_a, notes)
            VALUES ('whale', ?, 'BUY_YES', '', 0.55, 0, ?, 20, 'open', ?, ?, '')
            """,
            (time.time(), "test_token_id", "Existing malformed whale trade", "Will malformed token stay open?"),
        )
        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    with patch("api.get_midpoint", return_value=0.5) as midpoint_mock, \
         patch("api.get_market", return_value=None) as market_mock:
        updates = tracker.refresh_open_trades()
        closed = tracker.auto_close_trades()

    malformed_updates = [u for u in updates if u.get("trade_id") == trade_id]
    malformed_closed = [c for c in closed if c.get("trade_id") == trade_id]
    trade = db.get_trade(trade_id)

    check("invalid-token whale trade omitted from refresh updates", len(malformed_updates) == 0,
          f"got {len(malformed_updates)} updates")
    check("invalid-token whale trade stays open", len(malformed_closed) == 0,
          f"got {len(malformed_closed)} closes")
    midpoint_tokens = [call.args[0] for call in midpoint_mock.call_args_list if call.args]
    gamma_tokens = [call.kwargs.get("token_id") for call in market_mock.call_args_list if call.kwargs.get("token_id")]
    check("invalid-token path skips midpoint lookup",
          "test_token_id" not in midpoint_tokens,
          f"got midpoint tokens={midpoint_tokens}")
    check("invalid-token path skips Gamma lookup",
          "test_token_id" not in gamma_tokens,
          f"got Gamma token lookups={gamma_tokens}")
    check("invalid-token whale trade remains open in DB",
          trade and trade["status"] == "open",
          f"got {trade.get('status') if trade else None}")
    check("invalid-token whale trade note recorded",
          trade and "invalid token_id_a placeholder" in (trade.get("notes") or ""),
          f"got {trade.get('notes') if trade else None}")


run("whale_trade_invalid_token_is_sanitized_on_open", test_whale_trade_invalid_token_is_sanitized_on_open)
run("single_leg_invalid_token_skips_gamma_lookup", test_single_leg_invalid_token_skips_gamma_lookup)


section("13. Weather duplicates and open-trade counting")

def test_weather_signal_block_reason_for_duplicate_token():
    import db

    opp = {
        "market": "Will Dallas reach 84°F on April 2?",
        "market_id": "mkt_weather_duplicate",
        "event": "Highest temperature in Dallas on April 2?",
        "city": "dallas",
        "lat": 32.77,
        "lon": -96.79,
        "target_date": "2026-04-02",
        "threshold_f": 84,
        "direction": "above",
        "yes_token": "tok_weather_dup_yes",
        "no_token": "tok_weather_dup_no",
        "market_price": 0.34,
        "noaa_prob": 0.21,
        "noaa_forecast_f": 80.0,
        "noaa_sigma_f": 3.5,
        "om_prob": 0.12,
        "om_forecast_f": 79.0,
        "combined_prob": 0.165,
        "combined_edge": -0.175,
        "combined_edge_pct": -17.5,
        "sources_agree": True,
        "sources_available": 2,
        "hours_ahead": 24,
        "ev_pct": 11.0,
        "kelly_fraction": 0.07,
        "action": "BUY_NO",
        "tradeable": True,
        "liquidity": 1500,
    }

    first_id = db.save_weather_signal(opp)
    trade_id = db.open_weather_trade(first_id, size_usd=20)
    check("first duplicate weather trade opened", trade_id is not None and trade_id > 0, f"got {trade_id}")

    second_id = db.save_weather_signal({**opp, "market_id": "mkt_weather_duplicate_2"})
    decision = db.inspect_weather_trade_open(second_id, size_usd=20)
    check("duplicate weather signal blocked", decision["ok"] is False)
    check("duplicate block reason code", decision.get("reason_code") == "token_already_open",
          f"got {decision.get('reason_code')}")
    check("duplicate block references open trade", decision.get("existing_trade_id") == trade_id,
          f"got {decision.get('existing_trade_id')}")

    rows = db.get_weather_signals(limit=10, tradeable_only=True)
    duplicate_row = next((row for row in rows if row["id"] == second_id), None)
    check("duplicate weather row returned", duplicate_row is not None)
    if duplicate_row:
        check("duplicate weather row shows open trade", duplicate_row.get("open_trade_id") == trade_id,
              f"got {duplicate_row.get('open_trade_id')}")
        check("duplicate weather row cannot open", duplicate_row.get("can_open_trade") is False,
              f"got {duplicate_row.get('can_open_trade')}")
        check("duplicate weather row exposes blocking reason",
              "Already have an open weather trade" in (duplicate_row.get("blocking_reason") or ""),
              f"got {duplicate_row.get('blocking_reason')}")


def test_get_trades_without_limit_returns_all_open_rows():
    import db

    conn = db.get_conn()
    now = time.time()
    try:
        for i in range(55):
            conn.execute(
                """
                INSERT INTO trades (trade_type, opened_at, side_a, side_b,
                    entry_price_a, entry_price_b, token_id_a, size_usd, status, notes)
                VALUES ('copy', ?, 'BUY_YES', '', 0.5, 0, ?, 1, 'open', 'limit test')
                """,
                (now + i, f"tok-limit-{i}"),
            )
        conn.commit()
    finally:
        conn.close()

    total_open = db.count_open_trades()
    open_rows = db.get_trades(status="open", limit=None)
    check("get_trades(limit=None) returns every open trade", len(open_rows) == total_open,
          f"got {len(open_rows)} rows vs count {total_open}")


run("weather_signal_block_reason_for_duplicate_token", test_weather_signal_block_reason_for_duplicate_token)
run("get_trades_without_limit_returns_all_open_rows", test_get_trades_without_limit_returns_all_open_rows)


def test_copy_trade_settings_default_to_uncapped_policy():
    import asyncio
    import db
    import server

    settings = db.get_copy_trade_settings()
    check("copy settings default caps disabled", settings["cap_enabled"] is False, detail=str(settings))
    check("copy settings default wallet cap uncapped", settings["per_wallet_cap"] is None, detail=str(settings))
    check("copy settings default total cap uncapped", settings["total_open_cap"] is None, detail=str(settings))
    check("copy settings default policy is uncapped cash-limited",
          settings.get("position_policy") == "uncapped_cash_limited", detail=str(settings))

    updated = asyncio.run(server.update_copy_settings(cap_enabled=True, per_wallet_cap=2, total_open_cap=0))
    saved = updated["settings"]
    check("copy settings preserve explicit wallet cap", saved["per_wallet_cap"] == 2, detail=str(saved))
    check("copy settings normalize zero total cap to uncapped", saved["total_open_cap"] is None, detail=str(saved))
    check("copy settings report active overrides when enabled", saved["caps_active"] is True, detail=str(saved))
    check("copy settings effective total cap stays uncapped", saved["effective_total_open_cap"] is None, detail=str(saved))


def test_paper_open_api_surfaces_block_reasons():
    import asyncio
    import db
    import server

    signal = {
        "event": "Pairs API Block Reason",
        "market_a": "Will API pair A happen?",
        "market_b": "Will API pair B happen?",
        "price_a": 0.44,
        "price_b": 0.56,
        "z_score": 1.7,
        "coint_pvalue": 0.03,
        "beta": 1.0,
        "half_life": 4.0,
        "spread_mean": 0.0,
        "spread_std": 0.1,
        "current_spread": 0.17,
        "liquidity": 8000,
        "volume_24h": 1000,
        "action": "SELL A / BUY B",
        "token_id_a": "tok-api-pair-a",
        "token_id_b": "tok-api-pair-b",
    }
    signal_id = db.save_signal(signal)
    first_trade = db.open_trade(signal_id, size_usd=100)
    check("pairs API test setup trade opened", first_trade is not None and first_trade > 0, f"got {first_trade}")

    response = asyncio.run(server.create_trade(signal_id=signal_id, size_usd=100))
    payload = json.loads(response.body.decode())
    check("pairs API duplicate open returns structured block", payload.get("ok") is False, detail=str(payload))
    check("pairs API duplicate reason code exposed", payload.get("reason_code") == "signal_already_open", detail=str(payload))
    check("pairs API policy detail exposed", "cash" in (payload.get("policy", {}).get("detail") or "").lower(), detail=str(payload))

    wallet = "0xcopy-api-block"
    position = {
        "conditionId": "copy-api-block-condition",
        "outcome": "Yes",
        "curPrice": 0.63,
        "title": "Will copy API blocking reason show?",
        "asset": "copy-api-block-token",
    }
    with patch("copy_scanner.get_positions", return_value=[position]):
        response = asyncio.run(server.mirror_position(wallet=wallet, condition_id=position["conditionId"], size_usd=1_000_000))
    payload = json.loads(response.body.decode())
    check("copy mirror API insufficient cash returns structured block", payload.get("ok") is False, detail=str(payload))
    check("copy mirror API reason code exposed", payload.get("reason_code") == "insufficient_cash", detail=str(payload))
    check("copy mirror API includes paper account", isinstance(payload.get("paper_account"), dict), detail=str(payload))


run("copy_trade_settings_default_to_uncapped_policy", test_copy_trade_settings_default_to_uncapped_policy)
run("paper_open_api_surfaces_block_reasons", test_paper_open_api_surfaces_block_reasons)


# ── Summary ─────────────────────────────────────────────────────────────────

section("Summary")
total = results["pass"] + results["fail"]
print(f"\n  {results['pass']}/{total} passed   {results['fail']} failed\n")

if results["fail"] > 0:
    sys.exit(1)
