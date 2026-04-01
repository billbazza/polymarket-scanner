"""SQLite persistence layer for the scanner."""
import json
import logging
import math
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("SCANNER_DB_PATH", Path(__file__).parent / "scanner.db"))
_INIT_LOCK = threading.Lock()
_DB_INITIALIZED = False
log = logging.getLogger("scanner.db")


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _add_column_if_missing(conn, table_name, column_name, column_type):
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _migration_001_base_schema(conn):
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
            sizing_json TEXT,
            token_id_a TEXT,
            token_id_b TEXT
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
            notes TEXT,
            trade_type TEXT DEFAULT 'pairs',
            weather_signal_id INTEGER,
            token_id_a TEXT,
            token_id_b TEXT,
            copy_wallet TEXT,
            copy_label TEXT,
            copy_condition_id TEXT,
            copy_outcome TEXT
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

        CREATE TABLE IF NOT EXISTS longshot_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            market TEXT NOT NULL,
            market_id TEXT,
            yes_token TEXT,
            no_token TEXT,
            yes_price REAL,
            no_price REAL,
            calibrated_no_prob REAL,
            calibration_edge REAL,
            best_yes_bid REAL,
            best_yes_ask REAL,
            spread_pct REAL,
            limit_price REAL,
            no_cost REAL,
            ev_pct REAL,
            kelly_fraction REAL,
            fill_prob REAL,
            liquidity REAL,
            action TEXT DEFAULT 'SELL_YES',
            tradeable INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new'
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
        CREATE INDEX IF NOT EXISTS idx_longshot_signals_ts ON longshot_signals(timestamp);

        CREATE TABLE IF NOT EXISTS near_certainty_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            market TEXT NOT NULL,
            market_id TEXT,
            yes_token TEXT,
            no_token TEXT,
            yes_price REAL,
            calibrated_yes REAL,
            calibration_edge REAL,
            ev_pct REAL,
            ev REAL,
            cost REAL,
            fee REAL,
            kelly_fraction REAL,
            liquidity REAL,
            brain_prob REAL,
            brain_confirmed INTEGER DEFAULT 0,
            action TEXT DEFAULT 'BUY_YES',
            tradeable INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new'
        );

        CREATE INDEX IF NOT EXISTS idx_near_certainty_ts ON near_certainty_signals(timestamp);

        CREATE TABLE IF NOT EXISTS whale_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            event TEXT NOT NULL,
            market TEXT NOT NULL,
            market_id TEXT,
            token_id TEXT,
            current_price REAL,
            volume_24h REAL,
            liquidity REAL,
            volume_ratio REAL,
            biggest_order_usd REAL,
            dominant_side TEXT,
            suspicion_score INTEGER NOT NULL,
            score_volume INTEGER DEFAULT 0,
            score_price INTEGER DEFAULT 0,
            score_book INTEGER DEFAULT 0,
            score_thinness INTEGER DEFAULT 0,
            analysis TEXT,
            status TEXT DEFAULT 'new',
            dismissed INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_whale_alerts_ts ON whale_alerts(timestamp);
        CREATE INDEX IF NOT EXISTS idx_whale_alerts_score ON whale_alerts(suspicion_score);
    """)

def _migration_002_backfill_columns(conn):
    for col, coltype in [
        ("grade_label", "TEXT"),
        ("tradeable", "INTEGER DEFAULT 0"),
        ("ev_json", "TEXT"),
        ("sizing_json", "TEXT"),
        ("token_id_a", "TEXT"),
        ("token_id_b", "TEXT"),
    ]:
        _add_column_if_missing(conn, "signals", col, coltype)
    for col, coltype in [
        ("trade_type", "TEXT DEFAULT 'pairs'"),
        ("weather_signal_id", "INTEGER"),
        ("token_id_a", "TEXT"),
        ("token_id_b", "TEXT"),
        ("copy_wallet", "TEXT"),
        ("copy_label", "TEXT"),
        ("copy_condition_id", "TEXT"),
        ("copy_outcome", "TEXT"),
        ("whale_alert_id", "INTEGER"),
        ("event", "TEXT"),
        ("market_a", "TEXT"),
    ]:
        _add_column_if_missing(conn, "trades", col, coltype)

    for col, coltype in [("baseline_positions", "TEXT")]:
        _add_column_if_missing(conn, "watched_wallets", col, coltype)

    for col, coltype in [("analysis", "TEXT")]:
        _add_column_if_missing(conn, "whale_alerts", col, coltype)


def _migration_003_scan_jobs(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            params_json TEXT,
            result_json TEXT,
            error TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_scan_jobs_status ON scan_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_scan_jobs_kind ON scan_jobs(kind);
        CREATE INDEX IF NOT EXISTS idx_scan_jobs_created_at ON scan_jobs(created_at);
    """)


def _migration_004_report_items(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS report_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            section TEXT NOT NULL,
            item_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            disposition TEXT,
            notes TEXT,
            diagnosis_path TEXT,
            action_path TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(report_date, section, item_text)
        );

        CREATE INDEX IF NOT EXISTS idx_report_items_report_date ON report_items(report_date);
        CREATE INDEX IF NOT EXISTS idx_report_items_section ON report_items(section);
        CREATE INDEX IF NOT EXISTS idx_report_items_status ON report_items(status);
    """)


def _migration_005_settings(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scanner_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT,
            updated_at REAL NOT NULL
        );
    """)


def _migration_006_paper_account(conn):
    now = time.time()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_accounts (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            starting_bankroll REAL NOT NULL DEFAULT 2000,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    conn.execute(
        """
        INSERT INTO paper_accounts (id, starting_bankroll, created_at, updated_at)
        VALUES (1, 2000, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (now, now),
    )


def _migration_007_copy_no_entry_price_fix(conn):
    """Repair open copy BUY_NO trades created with inverted entry prices.

    Copy positions from Polymarket's data API already expose the held outcome's
    token price in `curPrice`. Older code inverted that value for NO positions,
    which turned cheap YES prices into nearly-free NO entries and blew up
    unrealized P&L / total equity in paper accounting.
    """
    conn.execute(
        """
        UPDATE trades
        SET entry_price_a = ROUND(1.0 - entry_price_a, 6),
            notes = CASE
                WHEN notes IS NULL OR notes = '' THEN
                    'Migration 007: corrected copy BUY_NO entry price from inverted curPrice.'
                ELSE
                    notes || ' | Migration 007: corrected copy BUY_NO entry price from inverted curPrice.'
            END
        WHERE trade_type='copy'
          AND status='open'
          AND side_a='BUY_NO'
          AND entry_price_a IS NOT NULL
          AND entry_price_a > 0
          AND entry_price_a < 1
        """
    )


_MIGRATIONS = [
    ("001_base_schema", _migration_001_base_schema),
    ("002_backfill_columns", _migration_002_backfill_columns),
    ("003_scan_jobs", _migration_003_scan_jobs),
    ("004_report_items", _migration_004_report_items),
    ("005_settings", _migration_005_settings),
    ("006_paper_account", _migration_006_paper_account),
    ("007_copy_no_entry_price_fix", _migration_007_copy_no_entry_price_fix),
]


def get_conn():
    init_db()
    return _connect()


def init_db():
    """Create or migrate the database schema."""
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return

    with _INIT_LOCK:
        if _DB_INITIALIZED:
            return

        conn = _connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
            """)
            applied = {
                row["name"]
                for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            for name, migration in _MIGRATIONS:
                if name in applied:
                    continue
                migration(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (name, time.time()),
                )
            conn.commit()
            _DB_INITIALIZED = True
        finally:
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


# --- Paper Account ---

def _paper_account_row(conn):
    row = conn.execute("SELECT * FROM paper_accounts WHERE id=1").fetchone()
    if row:
        return row

    now = time.time()
    conn.execute(
        """
        INSERT INTO paper_accounts (id, starting_bankroll, created_at, updated_at)
        VALUES (1, 2000, ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    return conn.execute("SELECT * FROM paper_accounts WHERE id=1").fetchone()


def get_paper_account_config() -> dict:
    conn = get_conn()
    row = _paper_account_row(conn)
    conn.close()
    return dict(row)


def set_paper_starting_bankroll(starting_bankroll: float) -> dict:
    bankroll = max(0.0, float(starting_bankroll))
    conn = get_conn()
    row = _paper_account_row(conn)
    now = time.time()
    conn.execute(
        "UPDATE paper_accounts SET starting_bankroll=?, updated_at=? WHERE id=?",
        (bankroll, now, row["id"]),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM paper_accounts WHERE id=1").fetchone()
    conn.close()
    return dict(updated)


def _normalize_probability_price(price):
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if 0.0 <= value <= 1.0:
        return value
    return None


def calculate_single_leg_mark_to_market(size_usd, entry_price, current_price) -> dict:
    """Return shares, current market value, and P&L for a single token position."""
    cost_basis = max(0.0, float(size_usd or 0.0))
    entry = _normalize_probability_price(entry_price)
    current = _normalize_probability_price(current_price)
    if cost_basis <= 0 or entry is None or current is None or entry <= 0:
        return {
            "ok": False,
            "shares": 0.0,
            "cost_basis": round(cost_basis, 2),
            "current_price": current,
            "current_value": 0.0,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
        }

    shares = cost_basis / entry
    current_value = shares * current
    pnl_usd = current_value - cost_basis
    pnl_pct = (pnl_usd / cost_basis * 100) if cost_basis > 0 else 0.0
    return {
        "ok": True,
        "shares": shares,
        "cost_basis": round(cost_basis, 2),
        "current_price": current,
        "current_value": round(current_value, 2),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
    }


def calculate_pairs_mark_to_market(
    size_usd,
    entry_price_a,
    current_price_a,
    entry_price_b,
    current_price_b,
    side_a,
) -> dict:
    """Return shares, market value, and P&L for a two-leg paper pairs trade."""
    total_cost = max(0.0, float(size_usd or 0.0))
    half = total_cost / 2
    entry_a = _normalize_probability_price(entry_price_a)
    current_a = _normalize_probability_price(current_price_a)
    entry_b = _normalize_probability_price(entry_price_b)
    current_b = _normalize_probability_price(current_price_b)

    if (
        total_cost <= 0
        or half <= 0
        or entry_a is None
        or current_a is None
        or entry_b is None
        or current_b is None
        or entry_a <= 0
        or entry_b <= 0
    ):
        return {
            "ok": False,
            "cost_basis": round(total_cost, 2),
            "current_value": 0.0,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "shares_a": 0.0,
            "shares_b": 0.0,
        }

    shares_a = half / entry_a
    shares_b = half / entry_b

    if side_a == "BUY":
        pnl_a = shares_a * (current_a - entry_a)
        pnl_b = shares_b * (entry_b - current_b)
    else:
        pnl_a = shares_a * (entry_a - current_a)
        pnl_b = shares_b * (current_b - entry_b)

    pnl_usd = pnl_a + pnl_b
    current_value = total_cost + pnl_usd
    pnl_pct = (pnl_usd / total_cost * 100) if total_cost > 0 else 0.0
    return {
        "ok": True,
        "cost_basis": round(total_cost, 2),
        "current_value": round(current_value, 2),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
        "shares_a": shares_a,
        "shares_b": shares_b,
    }


def _paper_unrealized_from_updates(updates):
    unrealized = 0.0
    for item in updates or []:
        pnl = ((item.get("unrealized_pnl") or {}).get("pnl_usd")) or 0.0
        unrealized += float(pnl)
    return unrealized


def _paper_unrealized_from_snapshots(conn):
    rows = conn.execute("""
        SELECT t.id, t.trade_type, t.side_a, t.size_usd, t.entry_price_a, t.entry_price_b,
               s.price_a, s.price_b
        FROM trades t
        LEFT JOIN snapshots s ON s.id = (
            SELECT s2.id
            FROM snapshots s2
            WHERE s2.trade_id = t.id
            ORDER BY s2.timestamp DESC, s2.id DESC
            LIMIT 1
        )
        WHERE t.status='open'
    """).fetchall()

    unrealized = 0.0
    for row in rows:
        trade_type = row["trade_type"] or "pairs"
        if trade_type in {"weather", "copy", "whale"}:
            valuation = calculate_single_leg_mark_to_market(
                row["size_usd"],
                row["entry_price_a"],
                row["price_a"],
            )
        else:
            valuation = calculate_pairs_mark_to_market(
                row["size_usd"],
                row["entry_price_a"],
                row["price_a"],
                row["entry_price_b"],
                row["price_b"],
                row["side_a"],
            )
        if valuation["ok"]:
            unrealized += float(valuation["pnl_usd"])
    return unrealized


def get_paper_account_state(refresh_unrealized: bool = False) -> dict:
    conn = get_conn()
    account = _paper_account_row(conn)
    starting_bankroll = float(account["starting_bankroll"] or 0.0)
    committed_capital = float(conn.execute(
        "SELECT COALESCE(SUM(size_usd), 0) FROM trades WHERE status='open'"
    ).fetchone()[0] or 0.0)
    realized_pnl = float(conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed'"
    ).fetchone()[0] or 0.0)
    cumulative_losses = abs(float(conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl < 0 THEN pnl ELSE 0 END), 0) FROM trades WHERE status='closed'"
    ).fetchone()[0] or 0.0))
    realized_gains = float(conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0) FROM trades WHERE status='closed'"
    ).fetchone()[0] or 0.0)
    open_trades = int(conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status='open'"
    ).fetchone()[0] or 0)

    if refresh_unrealized and open_trades:
        conn.close()
        try:
            import tracker
            unrealized_pnl = _paper_unrealized_from_updates(tracker.refresh_open_trades())
        except Exception as e:
            log.warning("Failed to refresh open paper trades for account summary: %s", e)
            conn = get_conn()
            try:
                unrealized_pnl = _paper_unrealized_from_snapshots(conn)
            finally:
                conn.close()
    else:
        unrealized_pnl = _paper_unrealized_from_snapshots(conn) if open_trades else 0.0
        conn.close()

    available_cash = starting_bankroll + realized_pnl - committed_capital
    open_position_value = committed_capital + unrealized_pnl
    total_equity = available_cash + open_position_value
    bankroll_used_pct = (committed_capital / starting_bankroll * 100) if starting_bankroll > 0 else 0.0
    return {
        "starting_bankroll": round(starting_bankroll, 2),
        "available_cash": round(available_cash, 2),
        "committed_capital": round(committed_capital, 2),
        "open_position_value": round(open_position_value, 2),
        "realized_pnl": round(realized_pnl, 2),
        "realized_gains": round(realized_gains, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "cumulative_losses": round(cumulative_losses, 2),
        "total_equity": round(total_equity, 2),
        "open_trades": open_trades,
        "bankroll_used_pct": round(bankroll_used_pct, 1),
        "cash_after_open_explanation": "Opening a paper trade deducts its full size from available cash immediately and moves that amount into committed capital until the trade closes.",
    }


def can_open_paper_trade(size_usd: float) -> dict:
    requested = max(0.0, float(size_usd))
    account = get_paper_account_state(refresh_unrealized=False)
    ok = account["available_cash"] >= requested
    return {
        "ok": ok,
        "requested_size_usd": round(requested, 2),
        "available_cash": account["available_cash"],
        "shortfall_usd": round(max(0.0, requested - account["available_cash"]), 2),
        "account": account,
    }


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

    account_check = can_open_paper_trade(size_usd)
    if not account_check["ok"]:
        log.warning(
            "Paper trade blocked for signal %s: need $%.2f cash, have $%.2f",
            signal_id,
            float(size_usd),
            account_check["available_cash"],
        )
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


def count_open_copy_trades(wallet: str | None = None) -> int:
    conn = get_conn()
    if wallet:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE copy_wallet=? AND trade_type='copy' AND status='open'",
            (wallet.lower(),)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_type='copy' AND status='open'"
        ).fetchone()
    conn.close()
    return int(row[0] if row else 0)


def count_open_trades() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()
    conn.close()
    return int(row[0] if row else 0)


def inspect_weather_trade_open(weather_signal_id, size_usd=100, max_total_open=None, conn=None):
    """Return a structured weather-trade open decision.

    This centralizes duplicate suppression, paper cash checks, and optional
    total-open gating so autonomy, API endpoints, and the dashboard can all
    surface the same blocking reason.
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        sig = conn.execute(
            "SELECT * FROM weather_signals WHERE id=?", (weather_signal_id,)
        ).fetchone()
        if not sig:
            return {
                "ok": False,
                "reason_code": "signal_not_found",
                "reason": f"Weather signal {weather_signal_id} not found.",
            }

        action = sig["action"]
        entry_token = sig["yes_token"] if action == "BUY_YES" else sig["no_token"]
        existing_signal_trade = conn.execute(
            "SELECT id FROM trades WHERE weather_signal_id=? AND status='open'",
            (weather_signal_id,),
        ).fetchone()
        if existing_signal_trade:
            trade_id = int(existing_signal_trade["id"])
            return {
                "ok": False,
                "reason_code": "signal_already_open",
                "reason": f"Weather signal {weather_signal_id} is already open as trade #{trade_id}.",
                "existing_trade_id": trade_id,
                "entry_token": entry_token,
            }

        existing_token_trade = None
        if entry_token:
            existing_token_trade = conn.execute(
                """
                SELECT id, weather_signal_id
                FROM trades
                WHERE trade_type='weather' AND token_id_a=? AND status='open'
                ORDER BY opened_at DESC, id DESC
                LIMIT 1
                """,
                (entry_token,),
            ).fetchone()
        if existing_token_trade:
            trade_id = int(existing_token_trade["id"])
            other_signal_id = existing_token_trade["weather_signal_id"]
            detail = f" via signal {other_signal_id}" if other_signal_id else ""
            return {
                "ok": False,
                "reason_code": "token_already_open",
                "reason": f"Already have an open weather trade on this token as trade #{trade_id}{detail}.",
                "existing_trade_id": trade_id,
                "existing_signal_id": other_signal_id,
                "entry_token": entry_token,
            }

        if max_total_open is not None and count_open_trades() >= max_total_open:
            return {
                "ok": False,
                "reason_code": "max_open_reached",
                "reason": f"At max open trades ({count_open_trades()}/{max_total_open}).",
                "entry_token": entry_token,
            }

        account_check = can_open_paper_trade(size_usd)
        if not account_check["ok"]:
            return {
                "ok": False,
                "reason_code": "insufficient_cash",
                "reason": (
                    f"Insufficient paper cash: ${account_check['available_cash']:.2f} available, "
                    f"${account_check['requested_size_usd']:.2f} requested."
                ),
                "entry_token": entry_token,
                "account": account_check["account"],
                "available_cash": account_check["available_cash"],
                "requested_size_usd": account_check["requested_size_usd"],
            }

        entry_price = sig["market_price"] if action == "BUY_YES" else round(1.0 - (sig["market_price"] or 0), 4)
        return {
            "ok": True,
            "reason_code": "ready",
            "reason": "Ready to open weather trade.",
            "signal": dict(sig),
            "entry_token": entry_token,
            "entry_price": entry_price,
            "action": action,
            "requested_size_usd": round(float(size_usd), 2),
        }
    finally:
        if owns_conn:
            conn.close()


def open_copy_trade(
    wallet: str,
    label: str,
    position: dict,
    size_usd: float = 20.0,
    *,
    max_wallet_open: int | None = None,
    max_total_open: int | None = None,
) -> int | None:
    """Open a paper copy trade mirroring a watched wallet's position.

    position dict should have: conditionId, outcome, curPrice, title, asset
    Returns trade_id or None if duplicate.
    """
    wallet = wallet.lower()
    condition_id = position.get("conditionId", "")
    if has_open_copy_trade(wallet, condition_id):
        return None
    if max_wallet_open is not None and count_open_copy_trades(wallet) >= max_wallet_open:
        return None
    if max_total_open is not None and count_open_trades() >= max_total_open:
        return None
    account_check = can_open_paper_trade(size_usd)
    if not account_check["ok"]:
        log.warning(
            "Paper copy trade blocked for wallet %s: need $%.2f cash, have $%.2f",
            wallet,
            float(size_usd),
            account_check["available_cash"],
        )
        return None

    outcome = position.get("outcome", "")
    price = position.get("curPrice") or position.get("avgPrice") or 0
    side = "BUY_YES" if outcome.lower() not in ("no",) else "BUY_NO"
    entry_price = _normalize_probability_price(price)
    if entry_price is None or entry_price <= 0:
        log.warning(
            "Paper copy trade blocked for wallet %s: invalid entry price %r for %s",
            wallet,
            price,
            condition_id,
        )
        return None

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
    decision = inspect_weather_trade_open(weather_signal_id, size_usd=size_usd, conn=conn)
    if not decision["ok"]:
        log.info(
            "Paper weather trade blocked for signal %s: %s",
            weather_signal_id,
            decision["reason"],
        )
        conn.close()
        return None

    action = decision["action"]
    token = decision["entry_token"]
    entry_price = decision["entry_price"]

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

    For single-leg trades (weather/copy/whale), exit_price_b is not needed.
    P&L = (exit - entry) / entry * size_usd.

    For pairs trades, both exit prices are required.
    """
    conn = get_conn()
    trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not trade:
        conn.close()
        return None

    trade_type = trade["trade_type"] if trade["trade_type"] else "pairs"

    if trade_type in {"weather", "copy", "whale"}:
        valuation = calculate_single_leg_mark_to_market(
            trade["size_usd"],
            trade["entry_price_a"],
            exit_price_a,
        )
        pnl_usd = valuation["pnl_usd"]
        exit_b = exit_price_a  # store single price in both columns for consistency
    else:
        if exit_price_b is None:
            exit_price_b = trade["entry_price_b"]
        valuation = calculate_pairs_mark_to_market(
            trade["size_usd"],
            trade["entry_price_a"],
            exit_price_a,
            trade["entry_price_b"],
            exit_price_b,
            trade["side_a"],
        )
        pnl_usd = valuation["pnl_usd"]
        exit_b = exit_price_b

    conn.execute("""
        UPDATE trades SET closed_at=?, exit_price_a=?, exit_price_b=?,
            pnl=?, status='closed', notes=?
        WHERE id=?
    """, (time.time(), exit_price_a, exit_b, pnl_usd, notes, trade_id))
    conn.commit()
    conn.close()

    return pnl_usd


def update_trade_notes(trade_id, notes):
    """Persist tracker or operator notes on a trade."""
    conn = get_conn()
    conn.execute("UPDATE trades SET notes=? WHERE id=?", (notes, trade_id))
    conn.commit()
    conn.close()


_TRADES_SELECT = """
    SELECT t.id, t.signal_id, t.opened_at, t.closed_at, t.side_a, t.side_b,
        t.entry_price_a, t.entry_price_b, t.exit_price_a, t.exit_price_b,
        t.size_usd, t.pnl, t.status, t.notes, t.trade_type, t.weather_signal_id,
        t.token_id_a, t.token_id_b, t.copy_wallet, t.copy_label, t.copy_condition_id,
        t.copy_outcome, t.whale_alert_id, ww.active AS copy_wallet_active,
        ww.auto_drop_reason AS copy_wallet_reason,
        COALESCE(s.event, ws.event, t.copy_label, t.event) AS event,
        COALESCE(s.market_a, ws.market, t.copy_outcome, t.market_a) AS market_a,
        s.market_b,
        COALESCE(s.action, ws.action) AS action,
        ws.city, ws.target_date, ws.threshold_f, ws.direction,
        ws.combined_edge_pct, ws.combined_prob
    FROM trades t
    LEFT JOIN signals s          ON t.signal_id          = s.id
    LEFT JOIN weather_signals ws ON t.weather_signal_id  = ws.id
    LEFT JOIN watched_wallets ww ON t.copy_wallet        = ww.address
"""


def get_trades(status=None, limit=50):
    conn = get_conn()
    if status and limit is None:
        rows = conn.execute(
            _TRADES_SELECT + " WHERE t.status=? ORDER BY t.opened_at DESC",
            (status,),
        ).fetchall()
    elif status:
        rows = conn.execute(
            _TRADES_SELECT + " WHERE t.status=? ORDER BY t.opened_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    elif limit is None:
        rows = conn.execute(
            _TRADES_SELECT + " ORDER BY t.opened_at DESC"
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
    price_a = _normalize_probability_price(price_a)
    price_b = _normalize_probability_price(price_b) if price_b is not None else None
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
        SELECT ws.*, t.id AS exact_open_trade_id
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
    results = []
    try:
        for row in rows:
            item = dict(row)
            decision = inspect_weather_trade_open(item["id"], size_usd=20, conn=conn)
            item["open_trade_id"] = decision.get("existing_trade_id") or item.get("exact_open_trade_id")
            item["can_open_trade"] = bool(item.get("tradeable")) and decision["ok"]
            item["blocking_reason"] = None if decision["ok"] else decision.get("reason")
            item["blocking_reason_code"] = None if decision["ok"] else decision.get("reason_code")
            item["entry_token"] = decision.get("entry_token")
            item.pop("exact_open_trade_id", None)
            results.append(item)
    finally:
        conn.close()
    return results


def get_weather_signal_by_id(signal_id):
    """Fetch a single weather signal by id."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM weather_signals WHERE id=?", (signal_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


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

# --- Jobs ---

def create_scan_job(kind: str, params: dict | None = None) -> int:
    conn = get_conn()
    conn.execute("""
        INSERT INTO scan_jobs (kind, status, params_json, created_at)
        VALUES (?, 'queued', ?, ?)
    """, (kind, json.dumps(params or {}), time.time()))
    job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return job_id


def start_scan_job(job_id: int) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scan_jobs SET status='running', started_at=? WHERE id=?",
        (time.time(), job_id),
    )
    conn.commit()
    conn.close()


def finish_scan_job(job_id: int, result: dict) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scan_jobs SET status='completed', result_json=?, finished_at=? WHERE id=?",
        (json.dumps(result), time.time(), job_id),
    )
    conn.commit()
    conn.close()


def fail_scan_job(job_id: int, error: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scan_jobs SET status='failed', error=?, finished_at=? WHERE id=?",
        (error, time.time(), job_id),
    )
    conn.commit()
    conn.close()


def get_scan_job(job_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM scan_jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    if not row:
        return None
    job = dict(row)
    job["params"] = json.loads(job.pop("params_json")) if job.get("params_json") else None
    job["result"] = json.loads(job.pop("result_json")) if job.get("result_json") else None
    return job


# --- Daily Report Items ---

def _report_item_dict(row):
    return dict(row) if row else None


def save_report_items(report_date: str, section: str, items: list[str]) -> None:
    conn = get_conn()
    now = time.time()
    for item_text in items:
        text = (item_text or "").strip()
        if not text:
            continue
        conn.execute(
            """
            INSERT INTO report_items (
                report_date, section, item_text, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'new', ?, ?)
            ON CONFLICT(report_date, section, item_text)
            DO UPDATE SET updated_at=excluded.updated_at
            """,
            (report_date, section, text, now, now),
        )
    conn.commit()
    conn.close()


def get_report_items(report_date: str | None = None) -> list[dict]:
    conn = get_conn()
    if report_date:
        rows = conn.execute(
            """
            SELECT * FROM report_items
            WHERE report_date=?
            ORDER BY section, id
            """,
            (report_date,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM report_items
            ORDER BY report_date DESC, section, id
            """
        ).fetchall()
    conn.close()
    return [_report_item_dict(row) for row in rows]


def get_report_item(item_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM report_items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return _report_item_dict(row)


def update_report_item(
    item_id: int,
    *,
    status: str | None = None,
    disposition: str | None = None,
    notes: str | None = None,
    diagnosis_path: str | None = None,
    action_path: str | None = None,
) -> dict | None:
    updates = []
    params = []
    if status is not None:
        updates.append("status=?")
        params.append(status)
    if disposition is not None:
        updates.append("disposition=?")
        params.append(disposition)
    if notes is not None:
        updates.append("notes=?")
        params.append(notes)
    if diagnosis_path is not None:
        updates.append("diagnosis_path=?")
        params.append(diagnosis_path)
    if action_path is not None:
        updates.append("action_path=?")
        params.append(action_path)
    updates.append("updated_at=?")
    params.append(time.time())
    params.append(item_id)

    conn = get_conn()
    conn.execute(
        f"UPDATE report_items SET {', '.join(updates)} WHERE id=?",
        tuple(params),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM report_items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return _report_item_dict(row)


def get_report_items_for_latest_statuses(report_date: str, item_texts: list[str], section: str) -> list[dict]:
    if not item_texts:
        return []
    placeholders = ",".join("?" for _ in item_texts)
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT * FROM report_items
        WHERE report_date=? AND section=? AND item_text IN ({placeholders})
        ORDER BY id DESC
        """,
        (report_date, section, *item_texts),
    ).fetchall()
    conn.close()
    latest = {}
    for row in rows:
        item = dict(row)
        latest.setdefault(item["item_text"], item)
    return list(latest.values())


# --- Settings ---

def get_setting(key: str, default=None):
    conn = get_conn()
    row = conn.execute(
        "SELECT value_json FROM scanner_settings WHERE key=?",
        (key,),
    ).fetchone()
    conn.close()
    if not row:
        return default
    try:
        return json.loads(row["value_json"])
    except Exception:
        return default


def set_setting(key: str, value) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO scanner_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        """,
        (key, json.dumps(value), time.time()),
    )
    conn.commit()
    conn.close()


def get_copy_trade_settings() -> dict:
    settings = get_setting("copy_trade_limits", default=None) or {}
    return {
        "cap_enabled": bool(settings.get("cap_enabled", False)),
        "per_wallet_cap": int(settings.get("per_wallet_cap", 3) or 3),
        "total_open_cap": int(settings.get("total_open_cap", 25) or 25),
    }

# --- Longshot Signals ---

def save_longshot_signal(opp):
    """Save a longshot-bias opportunity."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO longshot_signals (
            timestamp, event, market, market_id, yes_token, no_token,
            yes_price, no_price, calibrated_no_prob, calibration_edge,
            best_yes_bid, best_yes_ask, spread_pct,
            limit_price, no_cost, ev_pct, kelly_fraction, fill_prob,
            liquidity, action, tradeable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.time(), opp["event"], opp["market"], opp.get("market_id"),
        opp.get("yes_token"), opp.get("no_token"),
        opp["yes_price"], opp["no_price"],
        opp.get("calibrated_no_prob"), opp.get("calibration_edge"),
        opp.get("best_yes_bid"), opp.get("best_yes_ask"), opp.get("spread_pct"),
        opp.get("limit_price"), opp.get("no_cost"),
        opp.get("ev_pct"), opp.get("kelly_fraction"), opp.get("fill_prob"),
        opp.get("liquidity"),
        opp.get("action", "SELL_YES"),
        1 if opp.get("tradeable") else 0,
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_longshot_signals(limit=50, tradeable_only=False):
    """Fetch recent longshot-bias opportunities."""
    conn = get_conn()
    if tradeable_only:
        rows = conn.execute(
            "SELECT * FROM longshot_signals WHERE tradeable=1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM longshot_signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Near-Certainty Signals ---

def save_near_certainty_signal(opp):
    """Save a near-certainty YES edge opportunity."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO near_certainty_signals (
            timestamp, event, market, market_id, yes_token, no_token,
            yes_price, calibrated_yes, calibration_edge,
            ev_pct, ev, cost, fee, kelly_fraction,
            liquidity, brain_prob, brain_confirmed, action, tradeable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.time(), opp["event"], opp["market"], opp.get("market_id"),
        opp.get("yes_token"), opp.get("no_token"),
        opp["yes_price"], opp.get("calibrated_yes"), opp.get("calibration_edge"),
        opp.get("ev_pct"), opp.get("ev"), opp.get("cost"), opp.get("fee"),
        opp.get("kelly_fraction"), opp.get("liquidity"),
        opp.get("brain_prob"),
        1 if opp.get("brain_confirmed") else 0,
        opp.get("action", "BUY_YES"),
        1 if opp.get("tradeable") else 0,
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_near_certainty_signals(limit=50, tradeable_only=False):
    """Fetch recent near-certainty edge opportunities."""
    conn = get_conn()
    if tradeable_only:
        rows = conn.execute(
            "SELECT * FROM near_certainty_signals WHERE tradeable=1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM near_certainty_signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Whale Alerts ---

def save_whale_alert(alert):
    """Save a whale/insider alert. Deduplicates by market_id within 1 hour."""
    conn = get_conn()
    # Skip if we already flagged this market in the last hour
    cutoff = time.time() - 3600
    existing = conn.execute(
        "SELECT id FROM whale_alerts WHERE market_id=? AND timestamp > ?",
        (alert.get("market_id", ""), cutoff)
    ).fetchone()
    if existing:
        conn.close()
        return None

    conn.execute("""
        INSERT INTO whale_alerts (timestamp, event, market, market_id, token_id,
            current_price, volume_24h, liquidity, volume_ratio,
            biggest_order_usd, dominant_side,
            suspicion_score, score_volume, score_price, score_book, score_thinness,
            analysis, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        alert["timestamp"], alert["event"], alert["market"],
        alert.get("market_id", ""), alert.get("token_id", ""),
        alert.get("current_price"), alert.get("volume_24h", 0),
        alert.get("liquidity", 0), alert.get("volume_ratio", 0),
        alert.get("biggest_order_usd", 0), alert.get("dominant_side"),
        alert["suspicion_score"],
        alert.get("score_volume", 0), alert.get("score_price", 0),
        alert.get("score_book", 0), alert.get("score_thinness", 0),
        alert.get("analysis", ""),
        "new",
    ))
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return row_id


def get_whale_alerts(limit=50, min_score=0, undismissed_only=False):
    conn = get_conn()
    where = "WHERE suspicion_score >= ?"
    params = [min_score]
    if undismissed_only:
        where += " AND dismissed = 0"
    rows = conn.execute(
        f"SELECT * FROM whale_alerts {where} ORDER BY timestamp DESC LIMIT ?",
        (*params, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_whale_alert_by_id(alert_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM whale_alerts WHERE id=?", (alert_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_copy_trades(limit=5):
    conn = get_conn()
    rows = conn.execute("""
        SELECT id, opened_at, status, copy_wallet, copy_label,
               copy_condition_id, copy_outcome, size_usd
        FROM trades
        WHERE trade_type='copy'
        ORDER BY opened_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def dismiss_whale_alert(alert_id):
    conn = get_conn()
    conn.execute("UPDATE whale_alerts SET dismissed = 1 WHERE id = ?", (alert_id,))
    conn.commit()
    conn.close()


def get_new_whale_count():
    """Count undismissed whale alerts from the last 24h."""
    conn = get_conn()
    cutoff = time.time() - 86400
    row = conn.execute(
        "SELECT COUNT(*) FROM whale_alerts WHERE dismissed = 0 AND timestamp > ?",
        (cutoff,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


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
    paper_account = get_paper_account_state(refresh_unrealized=True)

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
        "realized_pnl": paper_account["realized_pnl"],
        "unrealized_pnl": paper_account["unrealized_pnl"],
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_signals": total_signals,
        "total_scans": total_scans,
        "pnl_series": pnl_series,
        "paper_account": paper_account,
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


def get_wallet_baseline(address: str) -> set:
    """Return the set of condition IDs that existed when this wallet was first scanned.

    If no baseline exists yet, returns None (meaning baseline needs to be set).
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT baseline_positions FROM watched_wallets WHERE address=?",
        (address.lower(),)
    ).fetchone()
    conn.close()
    if not row or not row["baseline_positions"]:
        return None
    try:
        return set(json.loads(row["baseline_positions"]))
    except Exception:
        return None


def set_wallet_baseline(address: str, condition_ids: list) -> None:
    """Store the baseline positions for a wallet — these will be skipped for copy trading."""
    conn = get_conn()
    conn.execute(
        "UPDATE watched_wallets SET baseline_positions=? WHERE address=?",
        (json.dumps(list(condition_ids)), address.lower())
    )
    conn.commit()
    conn.close()


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


def unwatch_wallet(address: str, reason: str = "manual_unwatch: operator stopped mirroring") -> bool:
    """Stop watching a wallet without deleting its history or closing trades."""
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE watched_wallets SET active=0, auto_drop_reason=? WHERE address=?",
        (reason, address.lower()),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


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


def open_whale_trade(trade_data):
    """Open a paper trade from a whale alert."""
    account_check = can_open_paper_trade(trade_data.get("size_usd", 0))
    if not account_check["ok"]:
        log.warning(
            "Paper whale trade blocked for alert %s: need $%.2f cash, have $%.2f",
            trade_data.get("whale_alert_id"),
            float(trade_data.get("size_usd", 0) or 0),
            account_check["available_cash"],
        )
        return None
    conn = get_conn()
    try:
        # Insert trade record
        conn.execute("""
            INSERT INTO trades (trade_type, opened_at, side_a, side_b,
                entry_price_a, entry_price_b, token_id_a, size_usd, status,
                whale_alert_id, event, market_a, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_data['trade_type'],
            trade_data['opened_at'],
            trade_data['side_a'],
            trade_data['side_b'],
            trade_data['entry_price_a'],
            trade_data['entry_price_b'],
            trade_data['token_id_a'],
            trade_data['size_usd'],
            trade_data['status'],
            trade_data.get('whale_alert_id'),
            trade_data['event'],
            trade_data['market_a'],
            f"Suspicion: {trade_data.get('suspicion_score', 0)}/100"
        ))
        trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return trade_id
    except Exception as e:
        print(f"Failed to open whale trade: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()
