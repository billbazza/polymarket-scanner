"""SQLite persistence layer for the scanner."""
import json
import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("SCANNER_DB_PATH", Path(__file__).parent / "scanner.db"))


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            market_a TEXT NOT NULL,
            market_b TEXT NOT NULL,
            price_a REAL,
            price_b REAL,
            z_score REAL NOT NULL,
            coint_pvalue REAL NOT NULL,
            beta REAL,
            half_life REAL,
            spread_mean REAL,
            spread_std REAL,
            current_spread REAL,
            liquidity REAL,
            volume_24h REAL,
            action TEXT,
            status TEXT DEFAULT 'new',
            grade_label TEXT,
            tradeable INTEGER DEFAULT 0,
            ev_json TEXT,
            sizing_json TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER REFERENCES signals(id),
            opened_at REAL NOT NULL,
            closed_at REAL,
            side_a TEXT NOT NULL,
            side_b TEXT NOT NULL,
            entry_price_a REAL NOT NULL,
            entry_price_b REAL NOT NULL,
            exit_price_a REAL,
            exit_price_b REAL,
            size_usd REAL DEFAULT 100,
            pnl REAL,
            status TEXT DEFAULT 'open',
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            trade_id INTEGER REFERENCES trades(id),
            price_a REAL,
            price_b REAL,
            spread REAL,
            z_score REAL
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            pairs_tested INTEGER,
            cointegrated INTEGER,
            opportunities INTEGER,
            duration_secs REAL
        );

        CREATE TABLE IF NOT EXISTS weather_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            market TEXT NOT NULL,
            market_id TEXT,
            yes_token TEXT,
            no_token TEXT,
            city TEXT,
            lat REAL,
            lon REAL,
            target_date TEXT,
            threshold_f REAL,
            direction TEXT,
            market_price REAL,
            noaa_forecast_f REAL,
            noaa_prob REAL,
            noaa_sigma_f REAL,
            om_forecast_f REAL,
            om_prob REAL,
            combined_prob REAL,
            combined_edge REAL,
            combined_edge_pct REAL,
            sources_agree INTEGER DEFAULT 0,
            sources_available INTEGER DEFAULT 0,
            hours_ahead INTEGER,
            ev_pct REAL,
            kelly_fraction REAL,
            action TEXT,
            tradeable INTEGER DEFAULT 0,
            liquidity REAL,
            status TEXT DEFAULT 'new'
        );

        CREATE TABLE IF NOT EXISTS locked_arb (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            market TEXT NOT NULL,
            market_id TEXT,
            yes_token TEXT,
            no_token TEXT,
            yes_price REAL,
            no_price REAL,
            sum_price REAL,
            gap_gross REAL,
            gap_net REAL,
            net_profit_pct REAL,
            liquidity REAL,
            yes_slippage_ok INTEGER,
            no_slippage_ok INTEGER,
            yes_slippage_pct REAL,
            no_slippage_pct REAL,
            tradeable INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new'
        );

        CREATE TABLE IF NOT EXISTS watched_wallets (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            address          TEXT NOT NULL UNIQUE,
            label            TEXT NOT NULL,
            added_at         REAL NOT NULL,
            added_by         TEXT DEFAULT 'manual',
            active           INTEGER DEFAULT 1,
            score            REAL,
            classification   TEXT,
            will_copy        INTEGER DEFAULT 0,
            score_breakdown  TEXT,
            scored_at        REAL,
            ai_verdict       TEXT,
            ai_reasoning     TEXT,
            ai_risk_flags    TEXT,
            ai_validated_at  REAL,
            auto_drop_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS wallet_candidates (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            address          TEXT NOT NULL,
            label            TEXT,
            discovered_at    REAL NOT NULL,
            score            REAL,
            classification   TEXT,
            will_copy        INTEGER DEFAULT 0,
            score_breakdown  TEXT,
            ai_verdict       TEXT,
            ai_reasoning     TEXT,
            ai_risk_flags    TEXT,
            status           TEXT DEFAULT 'pending',
            source_markets   TEXT
        );

        CREATE TABLE IF NOT EXISTS open_orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id     TEXT NOT NULL,
            trade_id     INTEGER REFERENCES trades(id),
            signal_id    INTEGER REFERENCES signals(id),
            token_id     TEXT NOT NULL,
            side         TEXT NOT NULL,
            leg          TEXT NOT NULL,
            limit_price  REAL NOT NULL,
            size_shares  REAL NOT NULL,
            size_usd     REAL,
            status       TEXT DEFAULT 'pending',
            mode         TEXT DEFAULT 'paper',
            placed_at    REAL NOT NULL,
            filled_at    REAL,
            fill_price   REAL,
            expires_at   REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_open_orders_status   ON open_orders(status);
        CREATE INDEX IF NOT EXISTS idx_open_orders_trade    ON open_orders(trade_id);
        CREATE INDEX IF NOT EXISTS idx_wallet_candidates_status ON wallet_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_snapshots_trade ON snapshots(trade_id);
        CREATE INDEX IF NOT EXISTS idx_weather_signals_ts ON weather_signals(timestamp);
        CREATE INDEX IF NOT EXISTS idx_locked_arb_ts ON locked_arb(timestamp);
        CREATE INDEX IF NOT EXISTS idx_watched_wallets_active ON watched_wallets(active);
    """)
    # Migrate: add new columns if they don't exist (safe for existing DBs)
    for col, coltype in [("grade_label", "TEXT"), ("tradeable", "INTEGER DEFAULT 0"),
                         ("ev_json", "TEXT"), ("sizing_json", "TEXT"),
                         ("token_id_a", "TEXT"), ("token_id_b", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass

    # trades: multi-type support (pairs, weather, copy)
    for col, coltype in [
        ("trade_type", "TEXT DEFAULT 'pairs'"),
        ("weather_signal_id", "INTEGER"),
        ("token_id_a", "TEXT"),
        ("token_id_b", "TEXT"),
        ("copy_wallet", "TEXT"),
        ("copy_label", "TEXT"),
        ("copy_condition_id", "TEXT"),
        ("copy_outcome", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


# --- Signals ---

def save_signal(opp):
    """Save a scan opportunity as a signal."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO signals (timestamp, event, market_a, market_b, price_a, price_b,
            z_score, coint_pvalue, beta, half_life, spread_mean, spread_std,
            current_spread, liquidity, volume_24h, action,
            grade_label, tradeable, ev_json, sizing_json, token_id_a, token_id_b)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.time(), opp["event"], opp["market_a"], opp["market_b"],
        opp["price_a"], opp["price_b"], opp["z_score"], opp["coint_pvalue"],
        opp["beta"], opp["half_life"], opp["spread_mean"], opp["spread_std"],
        opp["current_spread"], opp["liquidity"], opp["volume_24h"], opp["action"],
        opp.get("grade_label"), 1 if opp.get("tradeable") else 0,
        json.dumps(opp.get("ev")) if opp.get("ev") else None,
        json.dumps(opp.get("sizing")) if opp.get("sizing") else None,
        opp.get("token_id_a"), opp.get("token_id_b"),
    ))
    signal_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return signal_id


def get_signal_by_id(signal_id):
    """Fetch a single signal by primary key. Returns dict or None."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["ev"]     = json.loads(d.pop("ev_json"))     if d.get("ev_json")     else None
    d["sizing"] = json.loads(d.pop("sizing_json")) if d.get("sizing_json") else None
    return d


def get_signals(limit=50, status=None):
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status=? ORDER BY timestamp DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        # Deserialize JSON fields
        if d.get("ev_json"):
            d["ev"] = json.loads(d["ev_json"])
        else:
            d["ev"] = None
        if d.get("sizing_json"):
            d["sizing"] = json.loads(d["sizing_json"])
        else:
            d["sizing"] = None
        del d["ev_json"]
        del d["sizing_json"]
        results.append(d)
    return results


def update_signal_status(signal_id, status):
    conn = get_conn()
    conn.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))
    conn.commit()
    conn.close()


# --- Trades ---

def open_trade(signal_id, size_usd=100):
    """Open a paper trade from a signal.

    DB-level guard: returns None (no insert) if an open trade already exists
    for this signal_id, preventing duplicates from concurrent autonomy runs.
    """
    conn = get_conn()
    sig = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
    if not sig:
        conn.close()
        return None

    # DB-level dedup: abort if open trade already exists for this signal
    existing = conn.execute(
        "SELECT id FROM trades WHERE signal_id=? AND status='open'", (signal_id,)
    ).fetchone()
    if existing:
        conn.close()
        return None

    # Determine sides from z-score direction
    if sig["z_score"] < 0:
        side_a, side_b = "BUY", "SELL"
    else:
        side_a, side_b = "SELL", "BUY"

    conn.execute("""
        INSERT INTO trades (signal_id, opened_at, side_a, side_b,
            entry_price_a, entry_price_b, size_usd, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
    """, (
        signal_id, time.time(), side_a, side_b,
        sig["price_a"], sig["price_b"], size_usd,
    ))
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE signals SET status=? WHERE id=?", ("traded", signal_id))
    conn.commit()
    conn.close()
    return trade_id


def has_open_weather_trade(token_id):
    """Return True if there is already an open weather trade for this token."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM trades WHERE token_id_a=? AND trade_type='weather' AND status='open'",
        (token_id,)
    ).fetchone()
    conn.close()
    return row is not None


def has_open_copy_trade(wallet: str, condition_id: str) -> bool:
    """Return True if we already have an open copy trade for this wallet+market."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM trades WHERE copy_wallet=? AND copy_condition_id=? AND status='open'",
        (wallet, condition_id)
    ).fetchone()
    conn.close()
    return row is not None


def open_copy_trade(wallet: str, label: str, position: dict, size_usd: float = 20.0) -> int | None:
    """Open a paper copy trade mirroring a watched wallet's position.

    position dict should have: conditionId, outcome, curPrice, title, asset
    Returns trade_id or None if duplicate.
    """
    condition_id = position.get("conditionId", "")
    if has_open_copy_trade(wallet, condition_id):
        return None

    outcome = position.get("outcome", "")
    price = position.get("curPrice") or position.get("avgPrice") or 0
    side = "BUY_YES" if outcome.lower() not in ("no",) else "BUY_NO"
    entry_price = price if side == "BUY_YES" else round(1.0 - price, 4)

    conn = get_conn()
    conn.execute("""
        INSERT INTO trades (trade_type, opened_at, side_a, side_b,
            entry_price_a, entry_price_b, token_id_a, size_usd, status,
            copy_wallet, copy_label, copy_condition_id, copy_outcome)
        VALUES ('copy', ?, ?, '', ?, 0, ?, ?, 'open', ?, ?, ?, ?)
    """, (
        time.time(), side, entry_price,
        position.get("asset", ""), size_usd,
        wallet, label, condition_id, outcome,
    ))
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return trade_id


def open_weather_trade(weather_signal_id, size_usd=100):
    """Open a single-leg paper trade from a weather signal.

    DB-level guard: returns None if an open trade already exists for this signal.
    """
    conn = get_conn()
    sig = conn.execute(
        "SELECT * FROM weather_signals WHERE id=?", (weather_signal_id,)
    ).fetchone()
    if not sig:
        conn.close()
        return None

    existing = conn.execute(
        "SELECT id FROM trades WHERE weather_signal_id=? AND status='open'",
        (weather_signal_id,)
    ).fetchone()
    if existing:
        conn.close()
        return None

    action = sig["action"]  # BUY_YES or BUY_NO
    if action == "BUY_YES":
        token = sig["yes_token"]
        entry_price = sig["market_price"]
    else:
        token = sig["no_token"]
        entry_price = round(1.0 - (sig["market_price"] or 0), 4)

    conn.execute("""
        INSERT INTO trades (signal_id, weather_signal_id, trade_type, opened_at,
            side_a, side_b, entry_price_a, entry_price_b,
            token_id_a, size_usd, status)
        VALUES (NULL, ?, 'weather', ?, ?, '', ?, 0, ?, ?, 'open')
    """, (weather_signal_id, time.time(), action, entry_price, token, size_usd))
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE weather_signals SET status='traded' WHERE id=?", (weather_signal_id,))
    conn.commit()
    conn.close()
    return trade_id


def close_trade(trade_id, exit_price_a, exit_price_b=None, notes=""):
    """Close a paper trade and calculate P&L.

    For weather trades (single-leg), exit_price_b is not needed.
    P&L = (exit - entry) / entry * size_usd.

    For pairs trades, both exit prices are required.
    """
    conn = get_conn()
    trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not trade:
        conn.close()
        return None

    trade_type = trade["trade_type"] if trade["trade_type"] else "pairs"

    if trade_type == "weather":
        entry = trade["entry_price_a"] or 0
        pnl_usd = (exit_price_a - entry) / entry * trade["size_usd"] if entry > 0 else 0
        exit_b = exit_price_a  # store single price in both columns for consistency
    else:
        if exit_price_b is None:
            exit_price_b = trade["entry_price_b"]
        if trade["side_a"] == "BUY":
            pnl_pct = (exit_price_a - trade["entry_price_a"]) + (trade["entry_price_b"] - exit_price_b)
        else:
            pnl_pct = (trade["entry_price_a"] - exit_price_a) + (exit_price_b - trade["entry_price_b"])
        pnl_usd = pnl_pct * trade["size_usd"]
        exit_b = exit_price_b

    conn.execute("""
        UPDATE trades SET closed_at=?, exit_price_a=?, exit_price_b=?,
            pnl=?, status='closed', notes=?
        WHERE id=?
    """, (time.time(), exit_price_a, exit_b, pnl_usd, notes, trade_id))
    conn.commit()
    conn.close()
    return pnl_usd


_TRADES_SELECT = """
    SELECT t.*,
        COALESCE(s.event,    ws.event,  t.copy_label)  AS event,
        COALESCE(s.market_a, ws.market, t.copy_outcome) AS market_a,
        s.market_b,
        COALESCE(s.action,   ws.action) AS action,
        ws.city, ws.target_date, ws.threshold_f, ws.direction,
        ws.combined_edge_pct, ws.combined_prob
    FROM trades t
    LEFT JOIN signals s          ON t.signal_id          = s.id
    LEFT JOIN weather_signals ws ON t.weather_signal_id  = ws.id
"""


def get_trades(status=None, limit=50):
    conn = get_conn()
    if status:
        rows = conn.execute(
            _TRADES_SELECT + " WHERE t.status=? ORDER BY t.opened_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            _TRADES_SELECT + " ORDER BY t.opened_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade(trade_id):
    conn = get_conn()
    row = conn.execute(
        _TRADES_SELECT + " WHERE t.id=?", (trade_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Snapshots ---

def save_snapshot(trade_id, price_a, price_b, spread, z_score):
    conn = get_conn()
    conn.execute("""
        INSERT INTO snapshots (timestamp, trade_id, price_a, price_b, spread, z_score)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (time.time(), trade_id, price_a, price_b, spread, z_score))
    conn.commit()
    conn.close()


def get_snapshots(trade_id, limit=500):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE trade_id=? ORDER BY timestamp ASC LIMIT ?",
        (trade_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Weather Signals ---

def save_weather_signal(opp):
    """Save a weather-edge opportunity."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO weather_signals (
            timestamp, event, market, market_id, yes_token, no_token,
            city, lat, lon, target_date, threshold_f, direction,
            market_price,
            noaa_forecast_f, noaa_prob, noaa_sigma_f,
            om_forecast_f, om_prob,
            combined_prob, combined_edge, combined_edge_pct,
            sources_agree, sources_available,
            hours_ahead, ev_pct, kelly_fraction, action, tradeable, liquidity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.time(), opp["event"], opp["market"], opp.get("market_id"),
        opp.get("yes_token"), opp.get("no_token"),
        opp["city"], opp["lat"], opp["lon"],
        opp["target_date"], opp["threshold_f"], opp["direction"],
        opp["market_price"],
        opp.get("noaa_forecast_f"), opp.get("noaa_prob"), opp.get("noaa_sigma_f"),
        opp.get("om_forecast_f"), opp.get("om_prob"),
        opp["combined_prob"], opp["combined_edge"], opp["combined_edge_pct"],
        1 if opp.get("sources_agree") else 0,
        opp.get("sources_available", 0),
        opp.get("hours_ahead"), opp.get("ev_pct"), opp.get("kelly_fraction"),
        opp.get("action"), 1 if opp.get("tradeable") else 0,
        opp.get("liquidity"),
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_weather_signals(limit=50, tradeable_only=False):
    """Fetch recent weather-edge opportunities, annotated with open trade id if one exists."""
    base = """
        SELECT ws.*, t.id AS open_trade_id
        FROM weather_signals ws
        LEFT JOIN trades t ON t.weather_signal_id = ws.id AND t.status = 'open'
    """
    conn = get_conn()
    if tradeable_only:
        rows = conn.execute(
            base + " WHERE ws.tradeable=1 ORDER BY ws.timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            base + " ORDER BY ws.timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Locked Arb ---

def save_locked_arb(opp):
    """Save a locked-market arbitrage opportunity."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO locked_arb (
            timestamp, event, market, market_id, yes_token, no_token,
            yes_price, no_price, sum_price, gap_gross, gap_net, net_profit_pct,
            liquidity, yes_slippage_ok, no_slippage_ok, yes_slippage_pct,
            no_slippage_pct, tradeable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.time(), opp["event"], opp["market"], opp.get("market_id"),
        opp.get("yes_token"), opp.get("no_token"),
        opp["yes_price"], opp["no_price"], opp["sum_price"],
        opp["gap_gross"], opp["gap_net"], opp["net_profit_pct"],
        opp.get("liquidity"),
        1 if opp.get("yes_slippage_ok") else 0,
        1 if opp.get("no_slippage_ok") else 0,
        opp.get("yes_slippage_pct"), opp.get("no_slippage_pct"),
        1 if opp.get("tradeable") else 0,
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_locked_arb(limit=50, tradeable_only=False):
    """Fetch recent locked-arb opportunities."""
    conn = get_conn()
    if tradeable_only:
        rows = conn.execute(
            "SELECT * FROM locked_arb WHERE tradeable=1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM locked_arb ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Scan Runs ---

def save_scan_run(pairs_tested, cointegrated, opportunities, duration):
    conn = get_conn()
    conn.execute("""
        INSERT INTO scan_runs (timestamp, pairs_tested, cointegrated, opportunities, duration_secs)
        VALUES (?, ?, ?, ?, ?)
    """, (time.time(), pairs_tested, cointegrated, opportunities, duration))
    conn.commit()
    conn.close()


def get_scan_runs(limit=20):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM scan_runs ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Stats ---

def get_stats():
    """Dashboard summary stats."""
    conn = get_conn()
    total_trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    open_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    _excl = "AND (notes IS NULL OR notes != 'manual close - dedup cleanup')"
    closed_trades = conn.execute(f"SELECT COUNT(*) FROM trades WHERE status='closed' {_excl}").fetchone()[0]
    total_pnl = conn.execute(f"SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed' {_excl}").fetchone()[0]
    wins = conn.execute(f"SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0 {_excl}").fetchone()[0]
    losses = conn.execute(f"SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl <= 0 {_excl}").fetchone()[0]

    total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    total_scans = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]

    # Cumulative P&L series — only closed trades with real P&L, sorted by close time
    pnl_rows = conn.execute("""
        SELECT closed_at, pnl FROM trades
        WHERE status='closed' AND pnl IS NOT NULL AND pnl != 0
        ORDER BY closed_at ASC
    """).fetchall()

    conn.close()

    win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0

    # Build cumulative series: each point is the running total after that trade closes
    cumulative = 0.0
    pnl_series = []
    for closed_at, pnl in pnl_rows:
        cumulative += pnl
        pnl_series.append({"t": closed_at, "pnl": round(cumulative, 2)})

    return {
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_signals": total_signals,
        "total_scans": total_scans,
        "pnl_series": pnl_series,
    }


# --- Watched Wallets ---

def add_watched_wallet(address: str, label: str, added_by: str = "manual") -> int | None:
    """Add a wallet to the watch list. Returns id or None if already exists."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO watched_wallets (address, label, added_at, added_by, active)
               VALUES (?, ?, ?, ?, 1)""",
            (address.lower(), label, time.time(), added_by),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return row_id
    except sqlite3.IntegrityError:
        # Already exists — reactivate if it was dropped
        conn.execute(
            "UPDATE watched_wallets SET active=1, auto_drop_reason=NULL, label=? WHERE address=?",
            (label, address.lower()),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM watched_wallets WHERE address=?", (address.lower(),)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def get_watched_wallets(active_only: bool = True) -> list[dict]:
    """Return watched wallets, deserializing JSON fields."""
    conn = get_conn()
    if active_only:
        rows = conn.execute(
            "SELECT * FROM watched_wallets WHERE active=1 ORDER BY score DESC NULLS LAST"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM watched_wallets ORDER BY active DESC, score DESC NULLS LAST"
        ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for field in ("score_breakdown", "ai_risk_flags"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        out.append(d)
    return out


def update_wallet_score(address: str, score_result: dict) -> None:
    """Persist scoring result from wallet_monitor.score_wallet()."""
    conn = get_conn()
    conn.execute(
        """UPDATE watched_wallets SET
               score=?, classification=?, will_copy=?,
               score_breakdown=?, scored_at=?
           WHERE address=?""",
        (
            score_result.get("score"),
            score_result.get("classification"),
            1 if score_result.get("will_copy") else 0,
            json.dumps(score_result.get("breakdown") or {}),
            time.time(),
            address.lower(),
        ),
    )
    conn.commit()
    conn.close()


def update_wallet_ai(address: str, verdict: str, reasoning: str, risk_flags: list) -> None:
    """Persist Claude's wallet recommendation."""
    conn = get_conn()
    conn.execute(
        """UPDATE watched_wallets SET
               ai_verdict=?, ai_reasoning=?, ai_risk_flags=?, ai_validated_at=?
           WHERE address=?""",
        (verdict, reasoning, json.dumps(risk_flags or []), time.time(), address.lower()),
    )
    conn.commit()
    conn.close()


def deactivate_watched_wallet(address: str, reason: str = "") -> None:
    """Mark a wallet as inactive (auto-drop). Preserves history."""
    conn = get_conn()
    conn.execute(
        "UPDATE watched_wallets SET active=0, auto_drop_reason=? WHERE address=?",
        (reason, address.lower()),
    )
    conn.commit()
    conn.close()


def remove_watched_wallet(address: str) -> bool:
    """Hard-delete a wallet from the watch list."""
    conn = get_conn()
    cursor = conn.execute("DELETE FROM watched_wallets WHERE address=?", (address.lower(),))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


# --- Wallet Candidates ---

def save_wallet_candidate(candidate: dict) -> int:
    """Upsert a discovered wallet candidate (replace stale entry for same address+status)."""
    status = candidate.get("status", "pending")
    conn = get_conn()
    conn.execute("DELETE FROM wallet_candidates WHERE address=? AND status=?",
                 (candidate["address"], status))
    conn.execute("""
        INSERT INTO wallet_candidates
            (address, label, discovered_at, score, classification, will_copy,
             score_breakdown, ai_verdict, ai_reasoning, ai_risk_flags, status, source_markets)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        candidate["address"], candidate.get("label"), time.time(),
        candidate.get("score"), candidate.get("classification"),
        1 if candidate.get("will_copy") else 0,
        json.dumps(candidate.get("breakdown") or {}),
        candidate.get("ai_verdict"), candidate.get("ai_reasoning"),
        json.dumps(candidate.get("ai_risk_flags") or []),
        status,
        json.dumps(candidate.get("source_markets") or []),
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_wallet_candidates(status: str = "pending") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM wallet_candidates WHERE status=? ORDER BY score DESC NULLS LAST",
        (status,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for field in ("score_breakdown", "ai_risk_flags", "source_markets"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        out.append(d)
    return out


def update_candidate_status(candidate_id: int, status: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE wallet_candidates SET status=? WHERE id=?", (status, candidate_id))
    conn.commit()
    conn.close()


# --- Open Orders (maker GTC limit orders) ---

def save_open_order(order: dict) -> int:
    """Record a pending GTC maker order. Returns row id."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO open_orders
            (order_id, trade_id, signal_id, token_id, side, leg,
             limit_price, size_shares, size_usd, status, mode,
             placed_at, expires_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        order["order_id"], order.get("trade_id"), order.get("signal_id"),
        order["token_id"], order["side"], order["leg"],
        order["limit_price"], order["size_shares"], order.get("size_usd"),
        order.get("status", "pending"), order.get("mode", "paper"),
        order.get("placed_at", time.time()),
        order.get("expires_at", time.time() + 4 * 3600),
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_open_orders(status: str = "pending") -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM open_orders WHERE status=? ORDER BY placed_at ASC",
        (status,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fill_open_order(row_id: int, fill_price: float) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE open_orders SET status='filled', filled_at=?, fill_price=? WHERE id=?",
        (time.time(), fill_price, row_id),
    )
    conn.commit()
    conn.close()


def cancel_open_order(row_id: int, reason: str = "expired") -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE open_orders SET status=? WHERE id=?",
        (reason, row_id),
    )
    conn.commit()
    conn.close()


# Initialize on import
init_db()
