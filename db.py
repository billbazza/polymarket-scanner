"""SQLite persistence layer for the scanner."""
import json
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path

import runtime_config
import weather_risk_review

DB_PATH = runtime_config.get_path("SCANNER_DB_PATH", Path(__file__).parent / "scanner.db")
_INIT_LOCK = threading.Lock()
_DB_INITIALIZED = False
log = logging.getLogger("scanner.db")

TRADE_STATE_PAPER = "paper_research"
TRADE_STATE_WALLET = "wallet_attached"
TRADE_STATE_LIVE = "live_exchange"

RUNTIME_SCOPE_PAPER = "paper"
RUNTIME_SCOPE_PENNY = "penny"

RECONCILIATION_INTERNAL = "internal_simulation"
RECONCILIATION_WALLET = "wallet_position"
RECONCILIATION_ORDERS = "exchange_orders"

STRATEGY_ORDER = ("cointegration", "weather", "whale", "copy", "other")
STRATEGY_LABELS = {
    "cointegration": "Cointegration",
    "weather": "Weather",
    "whale": "Whale",
    "copy": "Copy",
    "other": "Other",
}
COPY_ENTRY_PRICE_MIN = 0.15
COPY_ENTRY_PRICE_MAX = 0.85


_WATCHED_WALLET_MONITOR_COLUMNS = [
    ("baseline_positions", "TEXT"),
    ("last_checked_at", "REAL"),
    ("last_positions_count", "INTEGER"),
    ("last_event_at", "REAL"),
    ("last_event_type", "TEXT"),
    ("last_event_status", "TEXT"),
    ("last_event_reason", "TEXT"),
]

DEFAULT_WEATHER_REOPEN_PROBATION_LIMIT = 1


def normalize_runtime_scope(value: str | None, *, default: str = RUNTIME_SCOPE_PAPER) -> str:
    if value == RUNTIME_SCOPE_PENNY:
        return RUNTIME_SCOPE_PENNY
    if value == RUNTIME_SCOPE_PAPER:
        return RUNTIME_SCOPE_PAPER
    return default


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _table_exists(conn, table_name):
    """Return True if the named table is present in the schema."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _add_column_if_missing(conn, table_name, column_name, column_type):
    if not _table_exists(conn, table_name):
        return
    if column_name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _ensure_columns_if_table_exists(conn, table_name, columns):
    if not _table_exists(conn, table_name):
        return
    for column_name, column_type in columns:
        _add_column_if_missing(conn, table_name, column_name, column_type)


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _normalize_query_limit(limit, context: str):
    """Normalize optional LIMIT values before handing them to SQLite.

    SQLite raises `datatype mismatch` when NULL is bound into `LIMIT ?`.
    Callers use `None` to mean "no limit", so convert that into branch logic
    before the query runs and fail early with a precise message for bad values.
    """
    if limit is None:
        return None
    try:
        normalized = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid limit for {context}: {limit!r}") from exc
    if normalized < 0:
        raise ValueError(f"Invalid limit for {context}: {limit!r}")
    return normalized


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
            paper_tradeable INTEGER DEFAULT 0,
            ev_json TEXT,
            sizing_json TEXT,
            filters_json TEXT,
            token_id_a TEXT,
            token_id_b TEXT,
            admission_path TEXT,
            experiment_name TEXT,
            experiment_status TEXT,
            experiment_reason_code TEXT,
            experiment_reason TEXT,
            experiment_guardrails_json TEXT,
            admission_json TEXT,
            perplexity_json TEXT
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
            copy_outcome TEXT,
            strategy_name TEXT DEFAULT 'unknown',
            entry_grade_label TEXT,
            admission_path TEXT,
            experiment_name TEXT,
            experiment_status TEXT,
            entry_z_score REAL,
            entry_ev_pct REAL,
            entry_half_life REAL,
            entry_liquidity REAL,
            entry_slippage_pct_a REAL,
            entry_slippage_pct_b REAL,
            reversion_exit_z REAL,
            stop_z_threshold REAL,
            max_hold_hours REAL,
            closed_z_score REAL,
            exit_reason TEXT,
            max_unrealized_profit REAL DEFAULT 0,
            max_unrealized_drawdown REAL DEFAULT 0,
            regime_break_threshold REAL,
            regime_break_flag INTEGER DEFAULT 0,
            regime_break_notes TEXT
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
            strategy_name TEXT,
            market_family TEXT,
            market_id TEXT,
            yes_token TEXT,
            no_token TEXT,
            city TEXT,
            lat REAL,
            lon REAL,
            target_date TEXT,
            threshold_f REAL,
            direction TEXT,
            resolution_source TEXT,
            station_id TEXT,
            station_label TEXT,
            settlement_unit TEXT,
            settlement_precision REAL,
            station_timezone TEXT,
            outcome_label TEXT,
            market_price REAL,
            noaa_forecast_f REAL,
            noaa_prob REAL,
            noaa_sigma_f REAL,
            om_forecast_f REAL,
            om_prob REAL,
            combined_prob REAL,
            combined_edge REAL,
            combined_edge_pct REAL,
            selected_prob REAL,
            selected_edge REAL,
            selected_edge_pct REAL,
            correction_mode TEXT,
            correction_json TEXT,
            source_meta_json TEXT,
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
        ("paper_tradeable", "INTEGER DEFAULT 0"),
        ("ev_json", "TEXT"),
        ("sizing_json", "TEXT"),
        ("filters_json", "TEXT"),
        ("token_id_a", "TEXT"),
        ("token_id_b", "TEXT"),
        ("admission_path", "TEXT"),
        ("experiment_name", "TEXT"),
        ("experiment_status", "TEXT"),
        ("experiment_reason_code", "TEXT"),
        ("experiment_reason", "TEXT"),
        ("experiment_guardrails_json", "TEXT"),
        ("admission_json", "TEXT"),
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
        ("strategy_name", "TEXT DEFAULT 'unknown'"),
        ("entry_grade_label", "TEXT"),
        ("admission_path", "TEXT"),
        ("experiment_name", "TEXT"),
        ("experiment_status", "TEXT"),
        ("entry_z_score", "REAL"),
        ("entry_ev_pct", "REAL"),
        ("entry_half_life", "REAL"),
        ("entry_liquidity", "REAL"),
        ("entry_slippage_pct_a", "REAL"),
        ("entry_slippage_pct_b", "REAL"),
        ("reversion_exit_z", "REAL"),
        ("stop_z_threshold", "REAL"),
        ("max_hold_hours", "REAL"),
        ("closed_z_score", "REAL"),
        ("exit_reason", "TEXT"),
        ("max_unrealized_profit", "REAL DEFAULT 0"),
        ("max_unrealized_drawdown", "REAL DEFAULT 0"),
        ("regime_break_threshold", "REAL"),
        ("regime_break_flag", "INTEGER DEFAULT 0"),
        ("regime_break_notes", "TEXT"),
    ]:
        _add_column_if_missing(conn, "trades", col, coltype)

    _ensure_columns_if_table_exists(conn, "watched_wallets", _WATCHED_WALLET_MONITOR_COLUMNS)

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


def _migration_008_cointegration_trial_fields(conn):
    _migration_002_backfill_columns(conn)


def _migration_009_paper_trade_attempts(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_trade_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            source TEXT NOT NULL,
            strategy TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason_code TEXT,
            reason TEXT,
            event TEXT,
            signal_id INTEGER,
            weather_signal_id INTEGER,
            trade_id INTEGER,
            token_id TEXT,
            wallet TEXT,
            condition_id TEXT,
            autonomy_level TEXT,
            phase TEXT,
            size_usd REAL,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_paper_trade_attempts_ts
            ON paper_trade_attempts(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_paper_trade_attempts_outcome
            ON paper_trade_attempts(outcome);
        CREATE INDEX IF NOT EXISTS idx_paper_trade_attempts_strategy
            ON paper_trade_attempts(strategy);
    """)


def _migration_010_wallet_monitor_events(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wallet_monitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            source TEXT NOT NULL,
            wallet TEXT NOT NULL,
            label TEXT,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            reason_code TEXT,
            reason TEXT,
            condition_id TEXT,
            outcome_name TEXT,
            market_title TEXT,
            price REAL,
            position_value_usd REAL,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_wallet_monitor_events_ts
            ON wallet_monitor_events(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_wallet_monitor_events_wallet_ts
            ON wallet_monitor_events(wallet, timestamp DESC);
    """)


def _migration_021_runtime_scope_split(conn):
    _ensure_columns_if_table_exists(
        conn,
        "trades",
        [("runtime_scope", "TEXT NOT NULL DEFAULT 'paper'")],
    )
    _ensure_columns_if_table_exists(
        conn,
        "paper_trade_attempts",
        [("runtime_scope", "TEXT NOT NULL DEFAULT 'paper'")],
    )
    conn.execute(
        """
        UPDATE trades
        SET runtime_scope = CASE
            WHEN trade_state_mode IN (?, ?) THEN ?
            ELSE ?
        END
        WHERE runtime_scope IS NULL OR runtime_scope = ''
        """,
        (
            TRADE_STATE_WALLET,
            TRADE_STATE_LIVE,
            RUNTIME_SCOPE_PENNY,
            RUNTIME_SCOPE_PAPER,
        ),
    )
    conn.execute(
        """
        UPDATE paper_trade_attempts
        SET runtime_scope = CASE
            WHEN LOWER(COALESCE(autonomy_level, '')) IN ('penny', 'book') THEN ?
            ELSE ?
        END
        WHERE runtime_scope IS NULL OR runtime_scope = ''
        """,
        (RUNTIME_SCOPE_PENNY, RUNTIME_SCOPE_PAPER),
    )


def _migration_011_trade_monitor_events(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_monitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            source TEXT NOT NULL,
            trade_id INTEGER NOT NULL REFERENCES trades(id),
            trade_status TEXT,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            classification TEXT,
            reason_code TEXT,
            reason TEXT,
            remediation_action TEXT,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trade_monitor_events_ts
            ON trade_monitor_events(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_monitor_events_trade_ts
            ON trade_monitor_events(trade_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_monitor_events_classification
            ON trade_monitor_events(classification, timestamp DESC);
    """)


def _migration_012_trade_state_modes(conn):
    for col, coltype in [
        ("trade_state_mode", f"TEXT DEFAULT '{TRADE_STATE_PAPER}'"),
        ("reconciliation_mode", f"TEXT DEFAULT '{RECONCILIATION_INTERNAL}'"),
        ("canonical_ref", "TEXT"),
        ("external_position_id", "TEXT"),
        ("external_order_id_a", "TEXT"),
        ("external_order_id_b", "TEXT"),
        ("external_source", "TEXT"),
        ("event", "TEXT"),
        ("market_a", "TEXT"),
    ]:
        _add_column_if_missing(conn, "trades", col, coltype)

    conn.execute(
        f"""
        UPDATE trades
        SET trade_state_mode = CASE
                WHEN trade_type='copy' THEN '{TRADE_STATE_WALLET}'
                WHEN EXISTS (
                    SELECT 1
                    FROM open_orders oo
                    WHERE oo.trade_id = trades.id
                      AND oo.mode = 'live'
                ) THEN '{TRADE_STATE_LIVE}'
                ELSE '{TRADE_STATE_PAPER}'
            END
        WHERE trade_state_mode IS NULL OR trade_state_mode = ''
        """
    )
    conn.execute(
        f"""
        UPDATE trades
        SET reconciliation_mode = CASE
                WHEN trade_type='copy' THEN '{RECONCILIATION_WALLET}'
                WHEN EXISTS (
                    SELECT 1
                    FROM open_orders oo
                    WHERE oo.trade_id = trades.id
                      AND oo.mode = 'live'
                ) THEN '{RECONCILIATION_ORDERS}'
                ELSE '{RECONCILIATION_INTERNAL}'
            END
        WHERE reconciliation_mode IS NULL OR reconciliation_mode = ''
        """
    )
    conn.execute(
        """
        UPDATE trades
        SET external_source = CASE
                WHEN trade_type='copy' THEN 'watched_wallet'
                WHEN EXISTS (
                    SELECT 1
                    FROM open_orders oo
                    WHERE oo.trade_id = trades.id
                      AND oo.mode = 'live'
                ) THEN 'polymarket_clob'
                ELSE external_source
            END
        WHERE external_source IS NULL OR external_source = ''
        """
    )
    conn.execute(
        """
        UPDATE trades
        SET external_order_id_a = (
                SELECT oo.order_id
                FROM open_orders oo
                WHERE oo.trade_id = trades.id
                  AND oo.leg = 'a'
                  AND oo.mode = 'live'
                ORDER BY oo.id DESC
                LIMIT 1
            ),
            external_order_id_b = (
                SELECT oo.order_id
                FROM open_orders oo
                WHERE oo.trade_id = trades.id
                  AND oo.leg = 'b'
                  AND oo.mode = 'live'
                ORDER BY oo.id DESC
                LIMIT 1
            )
        WHERE trade_state_mode = ?
          AND (
                external_order_id_a IS NULL OR external_order_id_a = ''
             OR external_order_id_b IS NULL OR external_order_id_b = ''
          )
        """,
        (TRADE_STATE_LIVE,),
    )
    conn.execute(
        """
        UPDATE trades
        SET external_position_id = CASE
                WHEN token_id_a IS NOT NULL AND token_id_a != '' THEN token_id_a
                WHEN copy_condition_id IS NOT NULL AND copy_condition_id != '' THEN
                    copy_condition_id || ':' || LOWER(COALESCE(copy_outcome, ''))
                ELSE external_position_id
            END
        WHERE trade_type='copy'
          AND (external_position_id IS NULL OR external_position_id = '')
        """
    )
    conn.execute(
        """
        UPDATE trades
        SET canonical_ref = CASE
                WHEN trade_type='copy'
                     AND copy_wallet IS NOT NULL AND copy_wallet != ''
                     AND copy_condition_id IS NOT NULL AND copy_condition_id != ''
                     AND copy_outcome IS NOT NULL AND copy_outcome != '' THEN
                    'wallet:' || LOWER(copy_wallet) || ':condition:' || copy_condition_id || ':outcome:' || LOWER(copy_outcome)
                WHEN trade_type='copy' AND copy_wallet IS NOT NULL AND copy_wallet != '' THEN
                    'wallet:' || LOWER(copy_wallet) || ':position:' || COALESCE(external_position_id, '')
                WHEN EXISTS (
                    SELECT 1
                    FROM open_orders oo
                    WHERE oo.trade_id = trades.id
                      AND oo.mode = 'live'
                ) THEN
                    'live:' || COALESCE(external_order_id_a, '') || ':' || COALESCE(external_order_id_b, '')
                ELSE canonical_ref
            END
        WHERE canonical_ref IS NULL OR canonical_ref = ''
        """
    )


def _migration_013_paper_sizing_decisions(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS paper_sizing_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            source TEXT NOT NULL,
            strategy TEXT NOT NULL,
            mode TEXT NOT NULL,
            rollout_state TEXT,
            active_policy TEXT,
            selected_policy TEXT,
            applied INTEGER DEFAULT 0,
            signal_id INTEGER,
            weather_signal_id INTEGER,
            trade_id INTEGER,
            event TEXT,
            baseline_size_usd REAL,
            confidence_size_usd REAL,
            selected_size_usd REAL,
            confidence_score REAL,
            available_cash REAL,
            committed_capital REAL,
            total_equity REAL,
            current_total_utilization_pct REAL,
            projected_total_utilization_pct REAL,
            current_strategy_utilization_pct REAL,
            projected_strategy_utilization_pct REAL,
            constraints_json TEXT,
            details_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_paper_sizing_decisions_ts
            ON paper_sizing_decisions(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_paper_sizing_decisions_strategy_ts
            ON paper_sizing_decisions(strategy, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_paper_sizing_decisions_rollout_ts
            ON paper_sizing_decisions(rollout_state, timestamp DESC);
    """)


def _migration_014_signal_admission_diagnostics(conn):
    _add_column_if_missing(conn, "signals", "admission_json", "TEXT")


def _migration_015_weather_intraday_correction(conn):
    for col, coltype in [
        ("selected_prob", "REAL"),
        ("selected_edge", "REAL"),
        ("selected_edge_pct", "REAL"),
        ("correction_mode", "TEXT"),
        ("correction_json", "TEXT"),
    ]:
        _add_column_if_missing(conn, "weather_signals", col, coltype)


def _migration_016_weather_exact_temp_metadata(conn):
    for col, coltype in [
        ("strategy_name", "TEXT"),
        ("market_family", "TEXT"),
        ("resolution_source", "TEXT"),
        ("station_id", "TEXT"),
        ("station_label", "TEXT"),
        ("settlement_unit", "TEXT"),
        ("settlement_precision", "REAL"),
        ("station_timezone", "TEXT"),
        ("outcome_label", "TEXT"),
        ("source_meta_json", "TEXT"),
    ]:
        _add_column_if_missing(conn, "weather_signals", col, coltype)


def _migration_017_perplexity_metadata(conn):
    _add_column_if_missing(conn, "signals", "perplexity_json", "TEXT")


def _migration_018_weather_token_probation(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS weather_token_probation (
            token_id TEXT PRIMARY KEY,
            reopen_count INTEGER DEFAULT 0,
            last_reopened_at REAL,
            last_closed_at REAL,
            last_exit_reason TEXT
        );
    """)


def _migration_019_signal_grade_field(conn):
    _add_column_if_missing(conn, "signals", "grade", "INTEGER")


_MIGRATIONS = [
    ("001_base_schema", _migration_001_base_schema),
    ("002_backfill_columns", _migration_002_backfill_columns),
    ("003_scan_jobs", _migration_003_scan_jobs),
    ("004_report_items", _migration_004_report_items),
    ("005_settings", _migration_005_settings),
    ("006_paper_account", _migration_006_paper_account),
    ("007_copy_no_entry_price_fix", _migration_007_copy_no_entry_price_fix),
    ("008_cointegration_trial_fields", _migration_008_cointegration_trial_fields),
    ("009_paper_trade_attempts", _migration_009_paper_trade_attempts),
    ("010_wallet_monitor_events", _migration_010_wallet_monitor_events),
    ("011_trade_monitor_events", _migration_011_trade_monitor_events),
    ("012_trade_state_modes", _migration_012_trade_state_modes),
    ("013_paper_sizing_decisions", _migration_013_paper_sizing_decisions),
    ("014_signal_admission_diagnostics", _migration_014_signal_admission_diagnostics),
    ("015_weather_intraday_correction", _migration_015_weather_intraday_correction),
    ("016_weather_exact_temp_metadata", _migration_016_weather_exact_temp_metadata),
    ("017_perplexity_metadata", _migration_017_perplexity_metadata),
    ("018_weather_token_probation", _migration_018_weather_token_probation),
    ("019_signal_grade_field", _migration_019_signal_grade_field),
    ("021_runtime_scope_split", _migration_021_runtime_scope_split),
]


def get_conn():
    init_db()
    return _connect()


def _repair_schema_gaps(conn):
    """Heal forward-compatible columns even if prior migrations were marked applied.

    Older local DBs can already have `schema_migrations` rows recorded while still
    missing watched-wallet monitoring columns added later in development. Run a
    lightweight repair pass on every startup so operator-facing copy-trader tabs
    work without manual SQLite intervention.
    """
    _ensure_columns_if_table_exists(conn, "watched_wallets", _WATCHED_WALLET_MONITOR_COLUMNS)
    _ensure_columns_if_table_exists(conn, "signals", [
        ("grade", "INTEGER"),
        ("admission_json", "TEXT"),
        ("perplexity_json", "TEXT"),
    ])
    _ensure_columns_if_table_exists(conn, "weather_signals", [
        ("strategy_name", "TEXT"),
        ("market_family", "TEXT"),
        ("resolution_source", "TEXT"),
        ("station_id", "TEXT"),
        ("station_label", "TEXT"),
        ("settlement_unit", "TEXT"),
        ("settlement_precision", "REAL"),
        ("station_timezone", "TEXT"),
        ("outcome_label", "TEXT"),
        ("selected_prob", "REAL"),
        ("selected_edge", "REAL"),
        ("selected_edge_pct", "REAL"),
        ("correction_mode", "TEXT"),
        ("correction_json", "TEXT"),
        ("source_meta_json", "TEXT"),
    ])


def _deserialize_weather_signal_row(row):
    item = dict(row)
    if item.get("correction_json"):
        try:
            item["correction"] = json.loads(item["correction_json"])
        except json.JSONDecodeError:
            item["correction"] = None
    else:
        item["correction"] = None
    item.pop("correction_json", None)
    if item.get("source_meta_json"):
        try:
            item["source_meta"] = json.loads(item["source_meta_json"])
        except json.JSONDecodeError:
            item["source_meta"] = None
    else:
        item["source_meta"] = None
    item.pop("source_meta_json", None)
    return item


def _deserialize_signal_row(row):
    d = dict(row)
    if d.get("ev_json"):
        d["ev"] = json.loads(d["ev_json"])
    else:
        d["ev"] = None
    if d.get("sizing_json"):
        d["sizing"] = json.loads(d["sizing_json"])
    else:
        d["sizing"] = None
    if d.get("filters_json"):
        d["filters"] = json.loads(d["filters_json"])
    else:
        d["filters"] = None
    if d.get("experiment_guardrails_json"):
        d["experiment_guardrails"] = json.loads(d["experiment_guardrails_json"])
    else:
        d["experiment_guardrails"] = None
    if d.get("admission_json"):
        d["admission"] = json.loads(d["admission_json"])
    else:
        d["admission"] = None
    del d["ev_json"]
    del d["sizing_json"]
    del d["filters_json"]
    del d["experiment_guardrails_json"]
    del d["admission_json"]
    if d.get("perplexity_json"):
        try:
            d["perplexity"] = json.loads(d["perplexity_json"])
        except json.JSONDecodeError:
            d["perplexity"] = None
    else:
        d["perplexity"] = None
    d["profitable_candidate_feature"] = bool(
        d.get("perplexity") and d["perplexity"].get("profitable_candidate")
    )
    d["profitable_candidate_reason"] = (
        d.get("perplexity") and d["perplexity"].get("reason")
    )
    d["perplexity_status"] = (
        d.get("perplexity") and d["perplexity"].get("status")
    )
    d["perplexity_confidence"] = (
        d.get("perplexity") and d["perplexity"].get("confidence")
    )
    d.pop("perplexity_json", None)
    return _attach_signal_observability(d)


def _attach_signal_observability(signal):
    admission = signal.get("admission") or {}
    filters = signal.get("filters") or {}
    failed_filters = admission.get("failed_filters")
    if failed_filters is None:
        failed_filters = [name for name, passed in filters.items() if not passed]
    signal["failed_filters"] = failed_filters
    signal["accepted_signal"] = bool(admission.get("accepted", signal.get("tradeable")))
    signal["monitorable_signal"] = bool(
        admission.get("monitorable_signal")
        or signal.get("paper_tradeable")
        or signal.get("tradeable")
    )
    signal["admission_status"] = (
        admission.get("status")
        or ("tradeable" if signal.get("tradeable") else "monitor" if signal["monitorable_signal"] else "rejected")
    )
    signal["admission_reason_code"] = (
        signal.get("experiment_reason_code")
        or admission.get("primary_reason_code")
        or ("accepted" if signal["accepted_signal"] else "rejected")
    )
    signal["admission_reason"] = (
        signal.get("experiment_reason")
        or admission.get("primary_reason")
        or ("All admission filters passed." if signal["accepted_signal"] else "Signal rejected by admission filters.")
    )
    signal["admission_summary"] = (
        signal["admission_reason"]
        if not failed_filters
        else f"{signal['admission_reason']} Failed: {', '.join(failed_filters)}."
    )
    return signal


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
            _repair_schema_gaps(conn)
            conn.commit()
            _DB_INITIALIZED = True
        finally:
            conn.close()


# --- Signals ---

def save_signal(opp):
    """Save a scan opportunity as a signal."""
    conn = get_conn()
    grade_value = opp.get("grade")
    if grade_value is not None:
        try:
            grade_value = int(grade_value)
        except (TypeError, ValueError):
            grade_value = None
    conn.execute("""
        INSERT INTO signals (timestamp, event, market_a, market_b, price_a, price_b,
            z_score, coint_pvalue, beta, half_life, spread_mean, spread_std,
            current_spread, liquidity, volume_24h, action,
            grade_label, grade, tradeable, paper_tradeable, ev_json, sizing_json, filters_json,
            token_id_a, token_id_b, admission_path, experiment_name,
            experiment_status, experiment_reason_code, experiment_reason,
            experiment_guardrails_json, admission_json, perplexity_json)
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        time.time(), opp["event"], opp["market_a"], opp["market_b"],
        opp["price_a"], opp["price_b"], opp["z_score"], opp["coint_pvalue"],
        opp["beta"], opp["half_life"], opp["spread_mean"], opp["spread_std"],
        opp["current_spread"], opp["liquidity"], opp["volume_24h"], opp["action"],
        opp.get("grade_label"), grade_value, 1 if opp.get("tradeable") else 0,
        1 if opp.get("paper_tradeable") else 0,
        json.dumps(opp.get("ev")) if opp.get("ev") else None,
        json.dumps(opp.get("sizing")) if opp.get("sizing") else None,
        json.dumps(opp.get("filters")) if opp.get("filters") else None,
        opp.get("token_id_a"), opp.get("token_id_b"),
        opp.get("admission_path"), opp.get("experiment_name"),
        opp.get("experiment_status"), opp.get("experiment_reason_code"),
        opp.get("experiment_reason"),
        json.dumps(opp.get("experiment_guardrails")) if opp.get("experiment_guardrails") else None,
        json.dumps(opp.get("admission")) if opp.get("admission") else None,
        json.dumps(opp.get("perplexity")) if opp.get("perplexity") else None,
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
    return _deserialize_signal_row(row)


def get_signals(limit=50, status=None, include_rejected=True):
    limit = _normalize_query_limit(limit, "get_signals")
    conn = get_conn()
    if status and limit is None:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status=? ORDER BY timestamp DESC",
            (status,),
        ).fetchall()
    elif status:
        rows = conn.execute(
            "SELECT * FROM signals WHERE status=? ORDER BY timestamp DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    elif limit is None:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = _deserialize_signal_row(r)
        if not include_rejected and not d.get("monitorable_signal"):
            continue
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


def _normalize_trade_state_mode(value: str | None) -> str:
    if value in {TRADE_STATE_PAPER, TRADE_STATE_WALLET, TRADE_STATE_LIVE}:
        return value
    return TRADE_STATE_PAPER


def _normalize_reconciliation_mode(value: str | None) -> str:
    if value in {RECONCILIATION_INTERNAL, RECONCILIATION_WALLET, RECONCILIATION_ORDERS}:
        return value
    return RECONCILIATION_INTERNAL


def normalize_wallet_position_identifier(
    wallet: str | None,
    *,
    token_id: str | None = None,
    condition_id: str | None = None,
    outcome: str | None = None,
) -> dict:
    wallet_norm = (wallet or "").lower().strip()
    token = str(token_id or "").strip()
    condition = str(condition_id or "").strip()
    outcome_norm = str(outcome or "").strip().lower()
    external_position_id = token or (
        f"{condition}:{outcome_norm}" if condition and outcome_norm else condition
    )
    canonical_ref = None
    if wallet_norm and condition and outcome_norm:
        canonical_ref = f"wallet:{wallet_norm}:condition:{condition}:outcome:{outcome_norm}"
    elif wallet_norm and external_position_id:
        canonical_ref = f"wallet:{wallet_norm}:position:{external_position_id}"
    return {
        "wallet": wallet_norm,
        "token_id": token or None,
        "condition_id": condition or None,
        "outcome": outcome_norm or None,
        "external_position_id": external_position_id or None,
        "canonical_ref": canonical_ref,
    }


def get_position_identity(position: dict, wallet: str | None = None) -> dict:
    return normalize_wallet_position_identifier(
        wallet,
        token_id=position.get("asset"),
        condition_id=position.get("conditionId"),
        outcome=position.get("outcome"),
    )


def build_live_trade_identity(order_id_a: str | None, order_id_b: str | None, wallet: str | None = None) -> dict:
    order_a = str(order_id_a or "").strip()
    order_b = str(order_id_b or "").strip()
    wallet_norm = (wallet or "").lower().strip()
    if order_a and order_b:
        canonical_ref = f"live:{wallet_norm or 'wallet'}:{order_a}:{order_b}"
    elif order_a or order_b:
        canonical_ref = f"live:{wallet_norm or 'wallet'}:{order_a or order_b}"
    else:
        canonical_ref = None
    return {
        "external_order_id_a": order_a or None,
        "external_order_id_b": order_b or None,
        "canonical_ref": canonical_ref,
        "external_source": "polymarket_clob" if canonical_ref else None,
    }


def get_trade_reconciliation_key(trade: dict) -> str | None:
    trade_state_mode = _normalize_trade_state_mode(trade.get("trade_state_mode"))
    if trade_state_mode == TRADE_STATE_WALLET:
        return (
            trade.get("canonical_ref")
            or trade.get("external_position_id")
        )
    if trade_state_mode == TRADE_STATE_LIVE:
        return (
            trade.get("canonical_ref")
            or build_live_trade_identity(
                trade.get("external_order_id_a"),
                trade.get("external_order_id_b"),
            )["canonical_ref"]
        )
    return f"paper:{trade.get('id')}" if trade.get("id") else None


def find_open_copy_trade(
    wallet: str,
    *,
    canonical_ref: str | None = None,
    external_position_id: str | None = None,
    condition_id: str | None = None,
    outcome: str | None = None,
    runtime_scope: str | None = None,
    conn=None,
) -> dict | None:
    """Return the current open copy trade matching a watched-wallet position."""
    wallet_norm = (wallet or "").lower().strip()
    canonical_ref = (canonical_ref or "").strip() or None
    external_position_id = (external_position_id or "").strip() or None
    condition_id = (condition_id or "").strip() or None
    outcome_norm = str(outcome or "").strip().lower() or None
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        matchers = []
        params = [wallet_norm, normalize_runtime_scope(runtime_scope)]
        if canonical_ref:
            matchers.append("canonical_ref = ?")
            params.append(canonical_ref)
        if condition_id and outcome_norm:
            matchers.append("(copy_condition_id = ? AND LOWER(COALESCE(copy_outcome, '')) = ?)")
            params.extend([condition_id, outcome_norm])
        if external_position_id:
            matchers.append("external_position_id = ?")
            params.append(external_position_id)
        if not matchers:
            return None
        row = conn.execute(
            f"""
            SELECT id, copy_wallet, copy_condition_id, copy_outcome, canonical_ref, external_position_id
            FROM trades
            WHERE trade_type='copy'
              AND status='open'
              AND copy_wallet=?
              AND runtime_scope=?
              AND ({' OR '.join(matchers)})
            ORDER BY opened_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return dict(row) if row else None
    finally:
        if owns_conn:
            conn.close()


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


def _paper_unrealized_map_from_updates(updates):
    unrealized = {}
    for item in updates or []:
        trade_id = item.get("trade_id")
        if not trade_id:
            continue
        pnl = ((item.get("unrealized_pnl") or {}).get("pnl_usd")) or 0.0
        unrealized[int(trade_id)] = float(pnl)
    return unrealized


def _infer_reporting_trade_state_mode(trade) -> tuple[str, bool, str | None]:
    raw_mode = (trade.get("trade_state_mode") or "").strip()
    if raw_mode in {TRADE_STATE_PAPER, TRADE_STATE_WALLET, TRADE_STATE_LIVE}:
        return raw_mode, False, None

    trade_type = (trade.get("trade_type") or "pairs").strip().lower()
    reconciliation_mode = (trade.get("reconciliation_mode") or "").strip().lower()
    strategy_name = (trade.get("strategy_name") or "").strip().lower()

    if trade_type == "copy" or reconciliation_mode == RECONCILIATION_WALLET:
        return TRADE_STATE_WALLET, True, "missing_or_invalid_trade_state_mode"
    if (
        reconciliation_mode == RECONCILIATION_ORDERS
        or strategy_name.endswith("_live")
        or trade.get("external_order_id_a")
        or trade.get("external_order_id_b")
    ):
        return TRADE_STATE_LIVE, True, "missing_or_invalid_trade_state_mode"
    return TRADE_STATE_PAPER, True, "missing_or_invalid_trade_state_mode"


def _resolve_reporting_trade_state(trade) -> dict:
    mode, inferred, reason = _infer_reporting_trade_state_mode(trade)
    return {
        "mode": mode,
        "inferred": inferred,
        "reason_code": reason,
        "is_paper": mode == TRADE_STATE_PAPER,
        "is_external": mode in {TRADE_STATE_WALLET, TRADE_STATE_LIVE},
    }


def _latest_open_trade_valuation_rows(conn):
    return conn.execute("""
        SELECT t.id, t.trade_type, t.side_a, t.size_usd, t.entry_price_a, t.entry_price_b,
               s.price_a, s.price_b, s.timestamp AS snapshot_timestamp
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


def get_whale_open_drawdown_snapshot():
    """Return the aggregate unrealized PnL and count for open whale trades."""
    conn = get_conn()
    try:
        total = 0.0
        count = 0
        missing_marks = False
        for row in _latest_open_trade_valuation_rows(conn):
            trade_type = (row["trade_type"] or "pairs").strip().lower()
            if trade_type != "whale":
                continue
            count += 1
            valuation = calculate_single_leg_mark_to_market(
                row["size_usd"],
                row["entry_price_a"],
                row["price_a"],
            )
            if not valuation["ok"]:
                missing_marks = True
                continue
            total += float(valuation["pnl_usd"])
        return {
            "pnl_usd": round(total, 2),
            "open_trades": count,
            "mark_missing": missing_marks,
        }
    finally:
        conn.close()


def _open_trade_valuation_map_from_snapshots(conn):
    valuations = {}
    for row in _latest_open_trade_valuation_rows(conn):
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

        valuations[int(row["id"])] = {
            "ok": bool(valuation["ok"]),
            "pnl_usd": float(valuation["pnl_usd"]) if valuation["ok"] else 0.0,
            "current_value": float(valuation.get("current_value") or 0.0) if valuation["ok"] else 0.0,
            "snapshot_timestamp": row["snapshot_timestamp"],
            "mark_missing": not bool(valuation["ok"]),
        }
    return valuations


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


def _paper_unrealized_map_from_snapshots(conn):
    unrealized = {}
    for trade_id, valuation in _open_trade_valuation_map_from_snapshots(conn).items():
        if valuation["ok"]:
            unrealized[int(trade_id)] = float(valuation["pnl_usd"])
    return unrealized


def _trade_strategy_key(trade) -> str:
    trade_type = (trade.get("trade_type") or "pairs").strip().lower()
    strategy_name = (trade.get("strategy_name") or "").strip().lower()

    if trade_type == "pairs":
        if strategy_name in {"cointegration", "cointegration_live", "", "unknown"}:
            return "cointegration"
        return strategy_name.replace("_live", "") or "cointegration"
    if trade_type in {"weather", "copy", "whale"}:
        return trade_type
    return strategy_name.replace("_live", "") or "other"


def _empty_strategy_bucket(strategy: str) -> dict:
    return {
        "strategy": strategy,
        "label": STRATEGY_LABELS.get(strategy, strategy.replace("_", " ").title()),
        "trade_count": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "paper_open_trades": 0,
        "paper_closed_trades": 0,
        "wallet_open_trades": 0,
        "live_open_trades": 0,
        "external_open_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "net_pnl": 0.0,
        "paper_realized_pnl": 0.0,
        "paper_unrealized_pnl": 0.0,
        "paper_net_pnl": 0.0,
        "committed_capital": 0.0,
        "external_capital": 0.0,
        "capital_deployed": 0.0,
        "avg_trade_size": 0.0,
        "bankroll_utilization_pct": 0.0,
        "trade_state_inferred_trades": 0,
        "open_trades_missing_marks": 0,
        "data_quality_status": "ok",
    }


def _runtime_scope_clause(runtime_scope: str | None, column: str = "runtime_scope") -> tuple[str, list]:
    scope = normalize_runtime_scope(runtime_scope, default="")
    if scope not in {RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY}:
        return "", []
    return f" WHERE {column} = ?", [scope]


def get_strategy_performance(refresh_unrealized: bool = False, runtime_scope: str | None = None) -> dict:
    scope = normalize_runtime_scope(runtime_scope, default="")
    if refresh_unrealized:
        try:
            import tracker
            tracker.refresh_open_trades(runtime_scope=runtime_scope)
        except Exception as e:
            log.warning("Failed to refresh open paper trades for strategy summary: %s", e)

    conn = get_conn()
    try:
        account = _paper_account_row(conn)
        starting_bankroll = float(account["starting_bankroll"] or 0.0)
        where_clause, params = _runtime_scope_clause(runtime_scope)
        rows = conn.execute("""
            SELECT id, trade_type, strategy_name, status, pnl, size_usd, notes,
                   trade_state_mode, reconciliation_mode, runtime_scope,
                   external_order_id_a, external_order_id_b
            FROM trades
        """ + where_clause + " ORDER BY opened_at ASC, id ASC", params).fetchall()
        valuation_map = _open_trade_valuation_map_from_snapshots(conn)
    finally:
        conn.close()

    buckets = {strategy: _empty_strategy_bucket(strategy) for strategy in STRATEGY_ORDER}
    total_committed_capital = 0.0
    total_realized_pnl = 0.0
    total_unrealized_pnl = 0.0
    total_paper_realized_pnl = 0.0
    total_paper_unrealized_pnl = 0.0
    total_external_open_trades = 0
    total_open_trades_missing_marks = 0
    total_inferred_trade_states = 0

    treat_all_scoped_trades_as_primary = scope == RUNTIME_SCOPE_PENNY

    for row in rows:
        trade = dict(row)
        if trade.get("status") == "closed" and trade.get("notes") == "manual close - dedup cleanup":
            continue
        strategy = _trade_strategy_key(trade)
        bucket = buckets.setdefault(strategy, _empty_strategy_bucket(strategy))
        state = _resolve_reporting_trade_state(trade)
        valuation = valuation_map.get(int(trade["id"]), {"ok": False, "pnl_usd": 0.0, "mark_missing": False})
        size_usd = float(trade.get("size_usd") or 0.0)
        pnl = float(trade.get("pnl") or 0.0)

        bucket["trade_count"] += 1
        bucket["capital_deployed"] += size_usd
        if state["inferred"]:
            bucket["trade_state_inferred_trades"] += 1
            total_inferred_trade_states += 1

        if trade.get("status") == "open":
            bucket["open_trades"] += 1
            if valuation["ok"]:
                bucket["unrealized_pnl"] += float(valuation["pnl_usd"])
            else:
                bucket["open_trades_missing_marks"] += 1
                total_open_trades_missing_marks += 1
            if state["is_paper"] or treat_all_scoped_trades_as_primary:
                bucket["paper_open_trades"] += 1
                bucket["committed_capital"] += size_usd
                if valuation["ok"]:
                    bucket["paper_unrealized_pnl"] += float(valuation["pnl_usd"])
            else:
                bucket["external_open_trades"] += 1
                bucket["external_capital"] += size_usd
                total_external_open_trades += 1
                if state["mode"] == TRADE_STATE_WALLET:
                    bucket["wallet_open_trades"] += 1
                elif state["mode"] == TRADE_STATE_LIVE:
                    bucket["live_open_trades"] += 1
        elif trade.get("status") == "closed":
            bucket["closed_trades"] += 1
            bucket["realized_pnl"] += pnl
            if state["is_paper"] or treat_all_scoped_trades_as_primary:
                bucket["paper_closed_trades"] += 1
                bucket["paper_realized_pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1

    strategies = []
    for strategy in list(STRATEGY_ORDER) + sorted(k for k in buckets if k not in STRATEGY_ORDER):
        bucket = buckets[strategy]
        closed_trades = bucket["closed_trades"]
        bucket["win_rate"] = round((bucket["wins"] / closed_trades * 100) if closed_trades else 0.0, 1)
        bucket["realized_pnl"] = round(bucket["realized_pnl"], 2)
        bucket["unrealized_pnl"] = round(bucket["unrealized_pnl"], 2)
        bucket["net_pnl"] = round(bucket["realized_pnl"] + bucket["unrealized_pnl"], 2)
        bucket["paper_realized_pnl"] = round(bucket["paper_realized_pnl"], 2)
        bucket["paper_unrealized_pnl"] = round(bucket["paper_unrealized_pnl"], 2)
        bucket["paper_net_pnl"] = round(bucket["paper_realized_pnl"] + bucket["paper_unrealized_pnl"], 2)
        bucket["committed_capital"] = round(bucket["committed_capital"], 2)
        bucket["paper_committed_capital"] = bucket["committed_capital"]
        bucket["external_capital"] = round(bucket["external_capital"], 2)
        bucket["capital_deployed"] = round(bucket["capital_deployed"], 2)
        bucket["avg_trade_size"] = round((bucket["capital_deployed"] / bucket["trade_count"]) if bucket["trade_count"] else 0.0, 2)
        bucket["bankroll_utilization_pct"] = round(
            (bucket["committed_capital"] / starting_bankroll * 100) if starting_bankroll > 0 else 0.0,
            1,
        )
        bucket["data_quality_status"] = "warning" if (
            bucket["trade_state_inferred_trades"] or bucket["open_trades_missing_marks"]
        ) else "ok"
        total_committed_capital += bucket["committed_capital"]
        total_realized_pnl += bucket["realized_pnl"]
        total_unrealized_pnl += bucket["unrealized_pnl"]
        total_paper_realized_pnl += bucket["paper_realized_pnl"]
        total_paper_unrealized_pnl += bucket["paper_unrealized_pnl"]
        if bucket["trade_count"] or strategy in {"cointegration", "weather", "whale", "copy"}:
            strategies.append(bucket)

    scope = normalize_runtime_scope(runtime_scope, default="")
    return {
        "starting_bankroll": round(starting_bankroll, 2),
        "total_committed_capital": round(total_committed_capital, 2),
        "total_realized_pnl": round(total_realized_pnl, 2),
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "total_paper_realized_pnl": round(total_paper_realized_pnl, 2),
        "total_paper_unrealized_pnl": round(total_paper_unrealized_pnl, 2),
        "runtime_scope": scope or None,
        "reporting_scope": (
            f"strategy_pnl_scope_{scope}__utilization_scope_only"
            if scope
            else "strategy_pnl_all_states__utilization_paper_only"
        ),
        "data_quality": {
            "trade_state_inferred_trades": total_inferred_trade_states,
            "open_trades_missing_marks": total_open_trades_missing_marks,
            "external_open_trades_excluded_from_paper_utilization": total_external_open_trades,
            "has_gaps": bool(
                total_inferred_trade_states
                or total_open_trades_missing_marks
                or total_external_open_trades
            ),
        },
        "strategies": strategies,
    }


def get_paper_account_state(
    refresh_unrealized: bool = False,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
) -> dict:
    runtime_scope = normalize_runtime_scope(runtime_scope)
    if refresh_unrealized:
        try:
            import tracker
            tracker.refresh_open_trades(runtime_scope=runtime_scope)
        except Exception as e:
            log.warning("Failed to refresh open paper trades for account summary: %s", e)

    conn = get_conn()
    try:
        account = _paper_account_row(conn)
        starting_bankroll = float(account["starting_bankroll"] or 0.0)
        where_clause, params = _runtime_scope_clause(runtime_scope)
        rows = conn.execute("""
            SELECT id, trade_type, strategy_name, status, pnl, size_usd,
                   trade_state_mode, reconciliation_mode, runtime_scope,
                   external_order_id_a, external_order_id_b
            FROM trades
        """ + where_clause, params).fetchall()
        valuation_map = _open_trade_valuation_map_from_snapshots(conn)
    finally:
        conn.close()

    committed_capital = 0.0
    realized_pnl = 0.0
    cumulative_losses = 0.0
    realized_gains = 0.0
    open_trades = 0
    unrealized_pnl = 0.0
    excluded_open_trades = 0
    excluded_unrealized_pnl = 0.0
    excluded_realized_pnl = 0.0
    inferred_trade_states = 0
    open_paper_trades_missing_marks = 0
    include_all_scoped_trades = runtime_scope == RUNTIME_SCOPE_PENNY

    for row in rows:
        trade = dict(row)
        state = _resolve_reporting_trade_state(trade)
        if state["inferred"]:
            inferred_trade_states += 1
        size_usd = float(trade.get("size_usd") or 0.0)
        pnl = float(trade.get("pnl") or 0.0)
        if trade.get("status") == "open":
            valuation = valuation_map.get(int(trade["id"]), {"ok": False, "pnl_usd": 0.0})
            if state["is_paper"] or include_all_scoped_trades:
                open_trades += 1
                committed_capital += size_usd
                if valuation["ok"]:
                    unrealized_pnl += float(valuation["pnl_usd"])
                else:
                    open_paper_trades_missing_marks += 1
            else:
                excluded_open_trades += 1
                if valuation["ok"]:
                    excluded_unrealized_pnl += float(valuation["pnl_usd"])
        elif trade.get("status") == "closed":
            if state["is_paper"] or include_all_scoped_trades:
                realized_pnl += pnl
                if pnl < 0:
                    cumulative_losses += abs(pnl)
                elif pnl > 0:
                    realized_gains += pnl
            else:
                excluded_realized_pnl += pnl

    available_cash = starting_bankroll + realized_pnl - committed_capital
    open_position_value = committed_capital + unrealized_pnl
    total_equity = available_cash + open_position_value
    bankroll_used_pct = (committed_capital / starting_bankroll * 100) if starting_bankroll > 0 else 0.0
    return {
        "runtime_scope": runtime_scope,
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
        "excluded_open_trades": excluded_open_trades,
        "excluded_realized_pnl": round(excluded_realized_pnl, 2),
        "excluded_unrealized_pnl": round(excluded_unrealized_pnl, 2),
        "reporting_scope": (
            "paper_research_only"
            if runtime_scope == RUNTIME_SCOPE_PAPER
            else f"runtime_scope_only::{runtime_scope}"
        ),
        "data_quality": {
            "trade_state_inferred_trades": inferred_trade_states,
            "open_paper_trades_missing_marks": open_paper_trades_missing_marks,
            "excluded_external_open_trades": excluded_open_trades,
            "has_gaps": bool(
                inferred_trade_states
                or open_paper_trades_missing_marks
                or excluded_open_trades
            ),
        },
        "cash_after_open_explanation": (
            "Opening a paper trade deducts its full size from available cash immediately and moves that amount into committed capital until the trade closes."
            if runtime_scope == RUNTIME_SCOPE_PAPER
            else "Penny runtime accounting only includes penny-scoped trades, so open paper experiments do not consume penny cash or max-open capacity."
        ),
        **_paper_position_policy_dict(),
    }


def can_open_paper_trade(size_usd: float, runtime_scope: str = RUNTIME_SCOPE_PAPER) -> dict:
    requested = max(0.0, float(size_usd))
    account = get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
    ok = account["available_cash"] >= requested
    return {
        "ok": ok,
        "runtime_scope": runtime_scope,
        "requested_size_usd": round(requested, 2),
        "available_cash": account["available_cash"],
        "shortfall_usd": round(max(0.0, requested - account["available_cash"]), 2),
        "account": account,
    }


def get_paper_account_overview(
    refresh_unrealized: bool = False,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
) -> dict:
    if refresh_unrealized:
        strategy_breakdown = get_strategy_performance(refresh_unrealized=True, runtime_scope=runtime_scope)
        account = get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
    else:
        account = get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
        strategy_breakdown = get_strategy_performance(refresh_unrealized=False, runtime_scope=runtime_scope)

    overview = dict(account)
    overview["strategy_breakdown"] = strategy_breakdown
    return overview


def _get_live_wallet_snapshot() -> dict:
    """Return the current Polygon wallet/balance snapshot for penny-mode reporting."""
    try:
        import blockchain
    except Exception as exc:
        log.warning("Live wallet snapshot unavailable: %s", exc)
        return {
            "wallet_connected": False,
            "wallet_address": None,
            "available_balance_usd": 0.0,
            "balance_source": "polygon_wallet",
            "wallet_error": f"blockchain module unavailable: {exc}",
        }

    try:
        wallet_address = blockchain.get_wallet_address()
    except Exception as exc:
        log.warning("Failed to derive live wallet address: %s", exc)
        return {
            "wallet_connected": False,
            "wallet_address": None,
            "available_balance_usd": 0.0,
            "balance_source": "polygon_wallet",
            "wallet_error": str(exc),
        }

    if not wallet_address:
        return {
            "wallet_connected": False,
            "wallet_address": None,
            "available_balance_usd": 0.0,
            "balance_source": "polygon_wallet",
            "wallet_error": "POLYMARKET_PRIVATE_KEY not configured",
        }

    try:
        available_balance_usd = float(blockchain.get_usdc_balance(wallet_address) or 0.0)
        return {
            "wallet_connected": True,
            "wallet_address": wallet_address,
            "available_balance_usd": round(available_balance_usd, 2),
            "balance_source": "polygon_wallet",
            "wallet_error": None,
        }
    except Exception as exc:
        log.warning("Failed to fetch Polygon wallet balance for %s: %s", wallet_address, exc)
        return {
            "wallet_connected": False,
            "wallet_address": wallet_address,
            "available_balance_usd": 0.0,
            "balance_source": "polygon_wallet",
            "wallet_error": str(exc),
        }


def get_live_account_overview(
    refresh_unrealized: bool = False,
    runtime_scope: str = RUNTIME_SCOPE_PENNY,
) -> dict:
    """Return live wallet-backed account reporting for penny runtime."""
    runtime_scope = normalize_runtime_scope(runtime_scope, default=RUNTIME_SCOPE_PENNY)
    if runtime_scope != RUNTIME_SCOPE_PENNY:
        runtime_scope = RUNTIME_SCOPE_PENNY

    if refresh_unrealized:
        strategy_breakdown = get_strategy_performance(refresh_unrealized=True, runtime_scope=runtime_scope)
        scoped_account = get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
    else:
        scoped_account = get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
        strategy_breakdown = get_strategy_performance(refresh_unrealized=False, runtime_scope=runtime_scope)

    wallet_snapshot = _get_live_wallet_snapshot()
    wallet_balance = float(wallet_snapshot.get("available_balance_usd") or 0.0)
    open_position_value = float(scoped_account.get("open_position_value") or 0.0)
    total_equity = wallet_balance + open_position_value
    deployed_capital = float(scoped_account.get("committed_capital") or 0.0)
    wallet_exposure_pct = (open_position_value / total_equity * 100) if total_equity > 0 else 0.0

    return {
        "runtime_scope": runtime_scope,
        "account_mode": "live_wallet",
        "balance_source": wallet_snapshot.get("balance_source"),
        "wallet_connected": bool(wallet_snapshot.get("wallet_connected")),
        "wallet_address": wallet_snapshot.get("wallet_address"),
        "wallet_error": wallet_snapshot.get("wallet_error"),
        "available_balance_usd": round(wallet_balance, 2),
        "deployed_capital_usd": round(deployed_capital, 2),
        "open_position_value_usd": round(open_position_value, 2),
        "realized_pnl_usd": round(float(scoped_account.get("realized_pnl") or 0.0), 2),
        "realized_gains_usd": round(float(scoped_account.get("realized_gains") or 0.0), 2),
        "cumulative_losses_usd": round(float(scoped_account.get("cumulative_losses") or 0.0), 2),
        "unrealized_pnl_usd": round(float(scoped_account.get("unrealized_pnl") or 0.0), 2),
        "total_equity_usd": round(total_equity, 2),
        "open_positions": int(scoped_account.get("open_trades") or 0),
        "wallet_exposure_pct": round(wallet_exposure_pct, 1),
        "reporting_scope": f"live_wallet_scope::{runtime_scope}",
        "data_quality": dict(scoped_account.get("data_quality") or {}),
        "strategy_breakdown": strategy_breakdown,
        "runtime_scope_detail": (
            "Penny mode reports the Polygon wallet cash balance plus only penny-scoped open and closed trades."
        ),
    }


def get_runtime_account_overview(
    refresh_unrealized: bool = False,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
) -> dict:
    """Return the account overview matching the requested runtime scope."""
    runtime_scope = normalize_runtime_scope(runtime_scope)
    if runtime_scope == RUNTIME_SCOPE_PENNY:
        return get_live_account_overview(refresh_unrealized=refresh_unrealized, runtime_scope=runtime_scope)
    overview = get_paper_account_overview(refresh_unrealized=refresh_unrealized, runtime_scope=runtime_scope)
    overview["account_mode"] = "paper_bankroll"
    return overview


def _sanitize_operator_reason(reason, fallback: str = "Decision recorded.") -> str:
    if reason is None:
        return fallback
    text = " ".join(str(reason).replace("\n", " ").split()).strip()
    if not text:
        return fallback
    return text[:240]


def _resolve_trade_state_fields(metadata: dict | None = None) -> dict:
    metadata = metadata or {}
    default_scope = (
        RUNTIME_SCOPE_PENNY
        if _normalize_trade_state_mode(metadata.get("trade_state_mode")) in {TRADE_STATE_WALLET, TRADE_STATE_LIVE}
        else RUNTIME_SCOPE_PAPER
    )
    return {
        "trade_state_mode": _normalize_trade_state_mode(metadata.get("trade_state_mode")),
        "reconciliation_mode": _normalize_reconciliation_mode(metadata.get("reconciliation_mode")),
        "runtime_scope": normalize_runtime_scope(metadata.get("runtime_scope"), default=default_scope),
        "canonical_ref": metadata.get("canonical_ref"),
        "external_position_id": metadata.get("external_position_id"),
        "external_order_id_a": metadata.get("external_order_id_a"),
        "external_order_id_b": metadata.get("external_order_id_b"),
        "external_source": metadata.get("external_source"),
    }


def record_paper_trade_attempt(
    *,
    source: str,
    strategy: str,
    outcome: str,
    reason_code: str | None = None,
    reason: str | None = None,
    event: str | None = None,
    signal_id: int | None = None,
    weather_signal_id: int | None = None,
    trade_id: int | None = None,
    token_id: str | None = None,
    wallet: str | None = None,
    condition_id: str | None = None,
    autonomy_level: str | None = None,
    runtime_scope: str | None = None,
    phase: str | None = None,
    size_usd: float | None = None,
    details: dict | None = None,
) -> int:
    conn = get_conn()
    try:
        if not _table_exists(conn, "paper_trade_attempts"):
            log.warning("paper_trade_attempts table unavailable; skipping attempt log write")
            return 0

        conn.execute(
            """
            INSERT INTO paper_trade_attempts (
                timestamp, source, strategy, outcome, reason_code, reason, event,
                signal_id, weather_signal_id, trade_id, token_id, wallet, condition_id,
                autonomy_level, runtime_scope, phase, size_usd, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                source,
                strategy,
                outcome,
                reason_code,
                _sanitize_operator_reason(reason, fallback="Decision recorded."),
                event,
                signal_id,
                weather_signal_id,
                trade_id,
                token_id,
                wallet.lower() if wallet else None,
                condition_id,
                autonomy_level,
                normalize_runtime_scope(
                    runtime_scope,
                    default=(
                        RUNTIME_SCOPE_PENNY
                        if (autonomy_level or "").lower() in {"penny", "book"}
                        else RUNTIME_SCOPE_PAPER
                    ),
                ),
                phase,
                round(float(size_usd), 2) if size_usd is not None else None,
                json.dumps(details) if details else None,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(row_id)
    except sqlite3.Error as exc:
        log.warning("Failed to record paper trade attempt: %s", exc)
        return 0
    finally:
        conn.close()


def update_watched_wallet_poll_status(
    address: str,
    *,
    checked_at: float | None = None,
    positions_count: int | None = None,
) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE watched_wallets
            SET last_checked_at = COALESCE(?, last_checked_at),
                last_positions_count = COALESCE(?, last_positions_count)
            WHERE address = ?
            """,
            (
                checked_at,
                positions_count,
                (address or "").lower(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def record_wallet_monitor_event(
    *,
    source: str,
    wallet: str,
    label: str | None,
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
    timestamp: float | None = None,
    checked_at: float | None = None,
    positions_count: int | None = None,
) -> int:
    wallet = (wallet or "").lower()
    event_ts = timestamp or time.time()
    conn = get_conn()
    try:
        if not _table_exists(conn, "wallet_monitor_events"):
            log.warning("wallet_monitor_events table unavailable; skipping event log write")
            return 0

        conn.execute(
            """
            INSERT INTO wallet_monitor_events (
                timestamp, source, wallet, label, event_type, status,
                reason_code, reason, condition_id, outcome_name, market_title,
                price, position_value_usd, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_ts,
                source,
                wallet,
                label,
                event_type,
                status,
                reason_code,
                _sanitize_operator_reason(reason, fallback="Wallet monitor event recorded."),
                condition_id,
                outcome_name,
                market_title,
                price,
                position_value_usd,
                json.dumps(details) if details else None,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            UPDATE watched_wallets
            SET last_event_at = ?,
                last_event_type = ?,
                last_event_status = ?,
                last_event_reason = ?,
                last_checked_at = COALESCE(?, last_checked_at),
                last_positions_count = COALESCE(?, last_positions_count)
            WHERE address = ?
            """,
            (
                event_ts,
                event_type,
                status,
                _sanitize_operator_reason(reason, fallback="Wallet monitor event recorded."),
                checked_at,
                positions_count,
                wallet,
            ),
        )
        conn.commit()
        return int(row_id)
    except sqlite3.Error as exc:
        log.warning("Failed to record wallet monitor event: %s", exc)
        return 0
    finally:
        conn.close()


def get_wallet_monitor_events(limit: int = 50, wallet: str | None = None) -> list[dict]:
    limit = _normalize_query_limit(limit, "get_wallet_monitor_events")
    conn = get_conn()
    try:
        if not _table_exists(conn, "wallet_monitor_events"):
            return []
        params: list = []
        query = """
            SELECT *
            FROM wallet_monitor_events
        """
        if wallet:
            query += " WHERE wallet = ?"
            params.append(wallet.lower())
        query += " ORDER BY timestamp DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item.get("details_json"):
                try:
                    item["details"] = json.loads(item["details_json"])
                except Exception:
                    item["details"] = None
            else:
                item["details"] = None
            out.append(item)
        return out
    except sqlite3.Error as exc:
        log.warning("Failed to query wallet monitor events: %s", exc)
        return []
    finally:
        conn.close()


def get_wallet_monitor_event_summary(limit: int = 50, wallet: str | None = None) -> dict:
    events = get_wallet_monitor_events(limit=limit, wallet=wallet)
    counts = {}
    for item in events:
        key = item.get("status") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return {
        "available": True,
        "recent_count": len(events),
        "status_counts": counts,
    }


def get_paper_trade_attempts(limit: int = 50, runtime_scope: str | None = None) -> list[dict]:
    limit = _normalize_query_limit(limit, "get_paper_trade_attempts")
    conn = get_conn()
    try:
        if not _table_exists(conn, "paper_trade_attempts"):
            return []

        query = """
            SELECT *
            FROM paper_trade_attempts
        """
        params: list = []
        where_clause, where_params = _runtime_scope_clause(runtime_scope)
        query += where_clause
        params.extend(where_params)
        query += " ORDER BY timestamp DESC, id DESC"
        if limit is None:
            rows = conn.execute(query, params).fetchall()
        else:
            rows = conn.execute(query + " LIMIT ?", [*params, limit]).fetchall()
    except sqlite3.Error as exc:
        log.warning("Failed to query paper trade attempts: %s", exc)
        return []
    finally:
        conn.close()

    attempts = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item["details_json"]) if item.get("details_json") else None
        item.pop("details_json", None)
        attempts.append(item)
    return attempts


def record_trade_monitor_event(
    *,
    source: str,
    trade_id: int,
    trade_status: str | None,
    event_type: str,
    status: str,
    classification: str | None = None,
    reason_code: str | None = None,
    reason: str | None = None,
    remediation_action: str | None = None,
    details: dict | None = None,
    timestamp: float | None = None,
) -> int:
    conn = get_conn()
    try:
        if not _table_exists(conn, "trade_monitor_events"):
            log.warning("trade_monitor_events table unavailable; skipping trade monitor event")
            return 0
        conn.execute(
            """
            INSERT INTO trade_monitor_events (
                timestamp, source, trade_id, trade_status, event_type, status,
                classification, reason_code, reason, remediation_action, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp or time.time(),
                source,
                trade_id,
                trade_status,
                event_type,
                status,
                classification,
                reason_code,
                _sanitize_operator_reason(reason, fallback="Trade monitor event recorded."),
                remediation_action,
                json.dumps(details) if details else None,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(row_id)
    except sqlite3.Error as exc:
        log.warning("Failed to record trade monitor event: %s", exc)
        return 0
    finally:
        conn.close()


def get_trade_monitor_events(
    limit: int = 50,
    trade_id: int | None = None,
    open_only: bool = False,
) -> list[dict]:
    limit = _normalize_query_limit(limit, "get_trade_monitor_events")
    conn = get_conn()
    try:
        if not _table_exists(conn, "trade_monitor_events"):
            return []
        params: list = []
        query = """
            SELECT tme.*
            FROM trade_monitor_events tme
        """
        clauses = []
        if open_only:
            query += " JOIN trades t ON t.id = tme.trade_id"
            clauses.append("t.status='open'")
        if trade_id is not None:
            clauses.append("tme.trade_id = ?")
            params.append(int(trade_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY tme.timestamp DESC, tme.id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item.get("details_json"):
                try:
                    item["details"] = json.loads(item["details_json"])
                except Exception:
                    item["details"] = None
            else:
                item["details"] = None
            out.append(item)
        return out
    except sqlite3.Error as exc:
        log.warning("Failed to query trade monitor events: %s", exc)
        return []
    finally:
        conn.close()


def get_latest_trade_monitor_states(open_only: bool = True) -> list[dict]:
    conn = get_conn()
    try:
        if not _table_exists(conn, "trade_monitor_events"):
            return []
        query = """
            SELECT latest.*
            FROM trade_monitor_events latest
            JOIN (
                SELECT trade_id, MAX(id) AS max_id
                FROM trade_monitor_events
                GROUP BY trade_id
            ) picked ON picked.max_id = latest.id
        """
        if open_only:
            query += " JOIN trades t ON t.id = latest.trade_id WHERE t.status='open'"
        query += " ORDER BY latest.timestamp DESC, latest.id DESC"
        rows = conn.execute(query).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if item.get("details_json"):
                try:
                    item["details"] = json.loads(item["details_json"])
                except Exception:
                    item["details"] = None
            else:
                item["details"] = None
            out.append(item)
        return out
    except sqlite3.Error as exc:
        log.warning("Failed to load latest trade monitor states: %s", exc)
        return []
    finally:
        conn.close()


def get_trade_monitor_summary(open_only: bool = True) -> dict:
    events = get_latest_trade_monitor_states(open_only=open_only)
    by_classification = {}
    by_status = {}
    flagged = 0
    for item in events:
        classification = item.get("classification") or "unknown"
        status = item.get("status") or "unknown"
        by_classification[classification] = by_classification.get(classification, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        if status not in {"open_ok", "no_issue"}:
            flagged += 1
    return {
        "available": True,
        "open_only": open_only,
        "tracked_trades": len(events),
        "flagged_trades": flagged,
        "by_classification": by_classification,
        "by_status": by_status,
    }

def get_paper_trade_attempt_summary(limit: int = 50, runtime_scope: str | None = None) -> dict:
    attempts = get_paper_trade_attempts(limit=limit, runtime_scope=runtime_scope)
    blocked = sum(1 for item in attempts if item.get("outcome") == "blocked")
    allowed = sum(1 for item in attempts if item.get("outcome") == "allowed")
    errors = sum(1 for item in attempts if item.get("outcome") == "error")
    reason_counts = {}
    for item in attempts:
        if item.get("outcome") == "allowed":
            continue
        code = item.get("reason_code") or "unknown"
        reason_counts[code] = reason_counts.get(code, 0) + 1
    top_blockers = [
        {"reason_code": code, "count": count}
        for code, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    ][:5]
    return {
        "available": True,
        "runtime_scope": normalize_runtime_scope(runtime_scope, default="") or None,
        "recent_count": len(attempts),
        "allowed": allowed,
        "blocked": blocked,
        "errors": errors,
        "top_blockers": top_blockers,
    }


def record_paper_sizing_decision(
    *,
    source: str,
    strategy: str,
    mode: str,
    rollout_state: str | None = None,
    active_policy: str | None = None,
    selected_policy: str | None = None,
    applied: bool = False,
    signal_id: int | None = None,
    weather_signal_id: int | None = None,
    trade_id: int | None = None,
    event: str | None = None,
    baseline_size_usd: float | None = None,
    confidence_size_usd: float | None = None,
    selected_size_usd: float | None = None,
    confidence_score: float | None = None,
    available_cash: float | None = None,
    committed_capital: float | None = None,
    total_equity: float | None = None,
    current_total_utilization_pct: float | None = None,
    projected_total_utilization_pct: float | None = None,
    current_strategy_utilization_pct: float | None = None,
    projected_strategy_utilization_pct: float | None = None,
    constraints: dict | list | None = None,
    details: dict | None = None,
) -> int:
    conn = get_conn()
    try:
        if not _table_exists(conn, "paper_sizing_decisions"):
            log.warning("paper_sizing_decisions table unavailable; skipping sizing decision write")
            return 0
        conn.execute(
            """
            INSERT INTO paper_sizing_decisions (
                timestamp, source, strategy, mode, rollout_state, active_policy,
                selected_policy, applied, signal_id, weather_signal_id, trade_id, event,
                baseline_size_usd, confidence_size_usd, selected_size_usd, confidence_score,
                available_cash, committed_capital, total_equity,
                current_total_utilization_pct, projected_total_utilization_pct,
                current_strategy_utilization_pct, projected_strategy_utilization_pct,
                constraints_json, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                source,
                strategy,
                mode,
                rollout_state,
                active_policy,
                selected_policy,
                1 if applied else 0,
                signal_id,
                weather_signal_id,
                trade_id,
                event,
                round(float(baseline_size_usd), 2) if baseline_size_usd is not None else None,
                round(float(confidence_size_usd), 2) if confidence_size_usd is not None else None,
                round(float(selected_size_usd), 2) if selected_size_usd is not None else None,
                round(float(confidence_score), 4) if confidence_score is not None else None,
                round(float(available_cash), 2) if available_cash is not None else None,
                round(float(committed_capital), 2) if committed_capital is not None else None,
                round(float(total_equity), 2) if total_equity is not None else None,
                round(float(current_total_utilization_pct), 2) if current_total_utilization_pct is not None else None,
                round(float(projected_total_utilization_pct), 2) if projected_total_utilization_pct is not None else None,
                round(float(current_strategy_utilization_pct), 2) if current_strategy_utilization_pct is not None else None,
                round(float(projected_strategy_utilization_pct), 2) if projected_strategy_utilization_pct is not None else None,
                json.dumps(constraints) if constraints is not None else None,
                json.dumps(details) if details else None,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return int(row_id)
    except sqlite3.Error as exc:
        log.warning("Failed to record paper sizing decision: %s", exc)
        return 0
    finally:
        conn.close()


def get_paper_sizing_decisions(limit: int = 50) -> list[dict]:
    limit = _normalize_query_limit(limit, "get_paper_sizing_decisions")
    conn = get_conn()
    try:
        if not _table_exists(conn, "paper_sizing_decisions"):
            return []
        query = """
            SELECT *
            FROM paper_sizing_decisions
            ORDER BY timestamp DESC, id DESC
        """
        if limit is None:
            rows = conn.execute(query).fetchall()
        else:
            rows = conn.execute(query + " LIMIT ?", (limit,)).fetchall()
    except sqlite3.Error as exc:
        log.warning("Failed to query paper sizing decisions: %s", exc)
        return []
    finally:
        conn.close()

    decisions = []
    for row in rows:
        item = dict(row)
        try:
            item["constraints"] = json.loads(item["constraints_json"]) if item.get("constraints_json") else []
        except Exception:
            item["constraints"] = []
        try:
            item["details"] = json.loads(item["details_json"]) if item.get("details_json") else None
        except Exception:
            item["details"] = None
        item.pop("constraints_json", None)
        item.pop("details_json", None)
        item["applied"] = bool(item.get("applied"))
        decisions.append(item)
    return decisions


def get_paper_sizing_summary(limit: int = 200) -> dict:
    decisions = get_paper_sizing_decisions(limit=limit)
    by_strategy = {}
    applied_count = 0
    shadow_count = 0

    for item in decisions:
        strategy = item.get("strategy") or "unknown"
        bucket = by_strategy.setdefault(strategy, {
            "strategy": strategy,
            "decisions": 0,
            "applied": 0,
            "shadow": 0,
            "avg_baseline_size_usd": 0.0,
            "avg_confidence_size_usd": 0.0,
            "avg_selected_size_usd": 0.0,
            "avg_confidence_score": 0.0,
        })
        bucket["decisions"] += 1
        bucket["applied"] += 1 if item.get("applied") else 0
        bucket["shadow"] += 1 if (item.get("rollout_state") or "shadow") == "shadow" else 0
        bucket["avg_baseline_size_usd"] += float(item.get("baseline_size_usd") or 0.0)
        bucket["avg_confidence_size_usd"] += float(item.get("confidence_size_usd") or 0.0)
        bucket["avg_selected_size_usd"] += float(item.get("selected_size_usd") or 0.0)
        bucket["avg_confidence_score"] += float(item.get("confidence_score") or 0.0)
        applied_count += 1 if item.get("applied") else 0
        shadow_count += 1 if (item.get("rollout_state") or "shadow") == "shadow" else 0

    strategies = []
    for strategy in sorted(by_strategy):
        bucket = by_strategy[strategy]
        count = bucket["decisions"] or 1
        bucket["avg_baseline_size_usd"] = round(bucket["avg_baseline_size_usd"] / count, 2)
        bucket["avg_confidence_size_usd"] = round(bucket["avg_confidence_size_usd"] / count, 2)
        bucket["avg_selected_size_usd"] = round(bucket["avg_selected_size_usd"] / count, 2)
        bucket["avg_confidence_score"] = round(bucket["avg_confidence_score"] / count, 3)
        strategies.append(bucket)

    return {
        "available": True,
        "recent_count": len(decisions),
        "shadow_decisions": shadow_count,
        "applied_decisions": applied_count,
        "strategies": strategies,
    }


def _normalize_optional_cap(value) -> int | None:
    try:
        if value is None:
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    except Exception:
        return None


def _paper_position_policy_dict() -> dict:
    return {
        "position_policy": "uncapped_cash_limited",
        "position_policy_label": "No hard paper position cap",
        "position_policy_detail": (
            "Paper trading is uncapped by position count. Available cash and any "
            "explicit operator-enabled caps are the only open-position constraints."
        ),
    }


def inspect_pairs_trade_open(signal_id, size_usd=100, conn=None, runtime_scope: str = RUNTIME_SCOPE_PAPER):
    """Return a structured pairs-trade open decision."""
    owns_conn = conn is None
    conn = conn or get_conn()
    runtime_scope = normalize_runtime_scope(runtime_scope)
    try:
        sig = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not sig:
            return {
                "ok": False,
                "reason_code": "signal_not_found",
                "reason": f"Signal {signal_id} not found.",
                **_paper_position_policy_dict(),
            }

        existing = conn.execute(
            "SELECT id FROM trades WHERE signal_id=? AND status='open' AND runtime_scope=?",
            (signal_id, runtime_scope),
        ).fetchone()
        if existing:
            trade_id = int(existing["id"])
            return {
                "ok": False,
                "reason_code": "signal_already_open",
                "reason": f"Signal {signal_id} is already open as trade #{trade_id}.",
                "existing_trade_id": trade_id,
                **_paper_position_policy_dict(),
            }

        account_check = can_open_paper_trade(size_usd, runtime_scope=runtime_scope)
        if not account_check["ok"]:
            return {
                "ok": False,
                "reason_code": "insufficient_cash",
                "reason": (
                    f"Insufficient paper cash: ${account_check['available_cash']:.2f} available, "
                    f"${account_check['requested_size_usd']:.2f} requested."
                ),
                "available_cash": account_check["available_cash"],
                "requested_size_usd": account_check["requested_size_usd"],
                "shortfall_usd": account_check["shortfall_usd"],
                "account": account_check["account"],
                **_paper_position_policy_dict(),
            }

        return {
            "ok": True,
            "reason_code": "ready",
            "reason": "Ready to open paper pairs trade.",
            "signal": dict(sig),
            "runtime_scope": runtime_scope,
            "requested_size_usd": round(float(size_usd), 2),
            "admission_path": sig["admission_path"],
            "experiment_name": sig["experiment_name"],
            "experiment_status": sig["experiment_status"],
            **_paper_position_policy_dict(),
        }
    finally:
        if owns_conn:
            conn.close()


def inspect_whale_trade_open(
    whale_alert_id,
    size_usd=20,
    mode="paper",
    conn=None,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
):
    """Return a structured decision for opening a whale trade."""
    owns_conn = conn is None
    conn = conn or get_conn()
    runtime_scope = normalize_runtime_scope(runtime_scope)
    try:
        requested = max(0.0, float(size_usd or 0.0))
        if requested <= 0:
            return {
                "ok": False,
                "reason_code": "invalid_size",
                "reason": "Trade size must be greater than zero.",
                "requested_size_usd": requested,
                **_paper_position_policy_dict(),
            }

        alert_row = conn.execute(
            "SELECT * FROM whale_alerts WHERE id=?",
            (whale_alert_id,),
        ).fetchone()
        if not alert_row:
            return {
                "ok": False,
                "reason_code": "alert_not_found",
                "reason": f"Whale alert {whale_alert_id} not found.",
                "requested_size_usd": requested,
                **_paper_position_policy_dict(),
            }

        alert_dict = dict(alert_row)
        if not alert_dict.get("token_id"):
            return {
                "ok": False,
                "reason_code": "token_missing",
                "reason": "Whale alert is missing a valid CLOB token id.",
                "token_id": None,
                "alert": alert_dict,
                "requested_size_usd": requested,
                **_paper_position_policy_dict(),
            }

        existing = conn.execute(
            "SELECT id FROM trades WHERE whale_alert_id=? AND status='open' AND runtime_scope=?",
            (whale_alert_id, runtime_scope),
        ).fetchone()
        if existing:
            return {
                "ok": False,
                "reason_code": "alert_already_open",
                "reason": f"Whale alert {whale_alert_id} already has an open trade.",
                "existing_trade_id": int(existing["id"]),
                "alert": alert_dict,
                "token_id": alert_dict.get("token_id"),
                "requested_size_usd": requested,
                **_paper_position_policy_dict(),
            }

        if mode == "paper":
            account_check = can_open_paper_trade(requested, runtime_scope=runtime_scope)
            if not account_check["ok"]:
                return {
                    "ok": False,
                    "reason_code": "insufficient_cash",
                    "reason": (
                        f"Insufficient paper cash: ${account_check['available_cash']:.2f} available, "
                        f"${account_check['requested_size_usd']:.2f} requested."
                    ),
                    "available_cash": account_check["available_cash"],
                    "shortfall_usd": account_check["shortfall_usd"],
                    "requested_size_usd": requested,
                    "account": account_check["account"],
                    **_paper_position_policy_dict(),
                }

        return {
            "ok": True,
            "reason_code": "ready",
            "reason": "Ready to open whale trade.",
            "alert": alert_dict,
            "token_id": alert_dict.get("token_id"),
            "runtime_scope": runtime_scope,
            "requested_size_usd": requested,
            **_paper_position_policy_dict(),
        }
    finally:
        if owns_conn:
            conn.close()


def _wallet_copy_block_decision(
    wallet: str,
    condition_id: str,
    wallet_meta: sqlite3.Row | None,
) -> dict | None:
    """Return a blocking decision if the watched wallet is no longer eligible."""
    if not wallet_meta:
        return None
    ai_verdict_raw = wallet_meta["ai_verdict"] if wallet_meta["ai_verdict"] else ""
    ai_verdict = ai_verdict_raw.strip().lower() if ai_verdict_raw else ""
    breakdown_text = wallet_meta["score_breakdown"] or ""
    breakdown_data: dict = {}
    if breakdown_text:
        try:
            breakdown_data = json.loads(breakdown_text)
        except Exception:
            breakdown_data = {}
    realised_pnl = float(breakdown_data.get("realised_pnl") or 0)
    unrealised_pnl = float(breakdown_data.get("unrealised_pnl") or 0)
    wallet_total_pnl = realised_pnl + unrealised_pnl
    if wallet_total_pnl < 0:
        return {
            "ok": False,
            "reason_code": "wallet_negative_pnl",
            "reason": (
                f"Wallet total PnL ${wallet_total_pnl:.2f} is negative — skip copy trades until it recovers."
            ),
            "wallet": wallet,
            "condition_id": condition_id,
            "wallet_total_pnl": round(wallet_total_pnl, 2),
            **_paper_position_policy_dict(),
        }
    if ai_verdict and ai_verdict != "copy":
        return {
            "ok": False,
            "reason_code": "brain_verdict_block",
            "reason": (
                f"Brain verdict '{ai_verdict_raw}' forbids copying this wallet."
            ),
            "wallet": wallet,
            "condition_id": condition_id,
            "ai_verdict": ai_verdict_raw,
            **_paper_position_policy_dict(),
        }
    return None


def inspect_copy_trade_open(
    wallet: str,
    position: dict,
    size_usd: float = 20.0,
    *,
    max_wallet_open: int | None = None,
    max_total_open: int | None = None,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
) -> dict:
    """Return a structured copy-trade open decision."""
    wallet = (wallet or "").lower()
    runtime_scope = normalize_runtime_scope(runtime_scope)
    identifiers = get_position_identity(position, wallet=wallet)
    condition_id = identifiers["condition_id"] or ""
    external_position_id = identifiers["external_position_id"]
    canonical_ref = identifiers["canonical_ref"]
    outcome = identifiers["outcome"]
    max_wallet_open = _normalize_optional_cap(max_wallet_open)
    max_total_open = _normalize_optional_cap(max_total_open)

    if not condition_id:
        return {
            "ok": False,
            "reason_code": "position_not_found",
            "reason": "Copy position is missing a conditionId.",
            "wallet": wallet,
            **_paper_position_policy_dict(),
        }

    existing_trade = find_open_copy_trade(
        wallet,
        canonical_ref=canonical_ref,
        external_position_id=external_position_id,
        condition_id=condition_id,
        outcome=outcome,
        runtime_scope=runtime_scope,
    )
    if existing_trade:
        return {
            "ok": False,
            "reason_code": "position_already_open",
            "reason": f"Already mirrored by open copy trade #{existing_trade['id']}.",
            "wallet": wallet,
            "condition_id": condition_id,
            "external_position_id": external_position_id,
            "canonical_ref": canonical_ref,
            "existing_trade_id": existing_trade["id"],
            **_paper_position_policy_dict(),
        }

    wallet_open = count_open_copy_trades(wallet, runtime_scope=runtime_scope) if max_wallet_open is not None else None
    if max_wallet_open is not None and wallet_open >= max_wallet_open:
        return {
            "ok": False,
            "reason_code": "wallet_cap_reached",
            "reason": f"Copy wallet cap reached ({wallet_open}/{max_wallet_open}).",
            "wallet": wallet,
            "condition_id": condition_id,
            "wallet_open": wallet_open,
            "max_wallet_open": max_wallet_open,
            **_paper_position_policy_dict(),
        }

    total_open = count_open_trades(runtime_scope=runtime_scope) if max_total_open is not None else None
    if max_total_open is not None and total_open >= max_total_open:
        return {
            "ok": False,
            "reason_code": "total_cap_reached",
            "reason": f"Copy total open cap reached ({total_open}/{max_total_open}).",
            "wallet": wallet,
            "condition_id": condition_id,
            "total_open": total_open,
            "max_total_open": max_total_open,
            **_paper_position_policy_dict(),
        }

    account_check = can_open_paper_trade(size_usd, runtime_scope=runtime_scope)
    if not account_check["ok"]:
        return {
            "ok": False,
            "reason_code": "insufficient_cash",
            "reason": (
                f"Insufficient paper cash: ${account_check['available_cash']:.2f} available, "
                f"${account_check['requested_size_usd']:.2f} requested."
            ),
            "wallet": wallet,
            "condition_id": condition_id,
            "external_position_id": external_position_id,
            "available_cash": account_check["available_cash"],
            "requested_size_usd": account_check["requested_size_usd"],
            "shortfall_usd": account_check["shortfall_usd"],
            "account": account_check["account"],
            **_paper_position_policy_dict(),
        }

    price = position.get("curPrice") or position.get("avgPrice") or 0
    entry_price = _normalize_probability_price(price)
    if entry_price is None or entry_price <= 0:
        return {
            "ok": False,
            "reason_code": "invalid_entry_price",
            "reason": f"Invalid copy-trade entry price: {price!r}.",
            "wallet": wallet,
            "condition_id": condition_id,
            "external_position_id": external_position_id,
            **_paper_position_policy_dict(),
        }
    if entry_price < COPY_ENTRY_PRICE_MIN or entry_price > COPY_ENTRY_PRICE_MAX:
        return {
            "ok": False,
            "reason_code": "entry_price_range_violation",
            "reason": (
                f"Entry price {entry_price:.2f} is outside the {COPY_ENTRY_PRICE_MIN:.2f}-{COPY_ENTRY_PRICE_MAX:.2f}"
                " range the copy strategy considers acceptable."
            ),
            "wallet": wallet,
            "condition_id": condition_id,
            "entry_price": entry_price,
            "allowed_range": (
                COPY_ENTRY_PRICE_MIN,
                COPY_ENTRY_PRICE_MAX,
            ),
            **_paper_position_policy_dict(),
        }

    wallet_meta = None
    meta_conn = get_conn()
    try:
        wallet_meta = meta_conn.execute(
            "SELECT score_breakdown, ai_verdict FROM watched_wallets WHERE address=?",
            (wallet,),
        ).fetchone()
    finally:
        meta_conn.close()
    block_decision = _wallet_copy_block_decision(wallet, condition_id, wallet_meta)
    if block_decision:
        return block_decision

    return {
        "ok": True,
        "reason_code": "ready",
        "reason": "Ready to open copy trade.",
        "wallet": wallet,
        "condition_id": condition_id,
        "external_position_id": external_position_id,
        "canonical_ref": canonical_ref,
        "entry_price": entry_price,
        "runtime_scope": runtime_scope,
        "requested_size_usd": round(float(size_usd), 2),
        "max_wallet_open": max_wallet_open,
        "max_total_open": max_total_open,
        **_paper_position_policy_dict(),
    }


# --- Trades ---

def open_trade(signal_id, size_usd=100, metadata=None):
    """Open a paper trade from a signal.

    DB-level guard: returns None (no insert) if an open trade already exists
    for this signal_id, preventing duplicates from concurrent autonomy runs.
    """
    conn = get_conn()
    metadata = metadata or {}
    state_fields = _resolve_trade_state_fields(metadata)
    decision = inspect_pairs_trade_open(
        signal_id,
        size_usd=size_usd,
        conn=conn,
        runtime_scope=state_fields["runtime_scope"],
    )
    if not decision["ok"]:
        log.info("Paper pairs trade blocked for signal %s: %s", signal_id, decision["reason"])
        conn.close()
        return None
    sig = decision["signal"]

    # Determine sides from z-score direction
    if sig["z_score"] < 0:
        side_a, side_b = "BUY", "SELL"
    else:
        side_a, side_b = "SELL", "BUY"

    ev = metadata.get("ev") or {}
    slippage = metadata.get("slippage") or {}
    guardrails = metadata.get("guardrails") or {}

    conn.execute("""
        INSERT INTO trades (signal_id, opened_at, side_a, side_b,
            entry_price_a, entry_price_b, size_usd, status, strategy_name,
            entry_grade_label, admission_path, experiment_name, experiment_status,
            entry_z_score, entry_ev_pct, entry_half_life, entry_liquidity,
            entry_slippage_pct_a, entry_slippage_pct_b,
            reversion_exit_z, stop_z_threshold, max_hold_hours,
            regime_break_threshold, regime_break_flag,
            token_id_a, token_id_b, event, market_a,
            trade_state_mode, reconciliation_mode, runtime_scope, canonical_ref,
            external_position_id, external_order_id_a, external_order_id_b, external_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal_id, time.time(), side_a, side_b,
        sig["price_a"], sig["price_b"], size_usd, "open",
        metadata.get("strategy_name", "cointegration"),
        metadata.get("entry_grade_label") or sig["grade_label"],
        metadata.get("admission_path") or sig["admission_path"],
        metadata.get("experiment_name") or sig["experiment_name"],
        metadata.get("experiment_status") or sig["experiment_status"],
        metadata.get("entry_z_score", sig["z_score"]),
        metadata.get("entry_ev_pct", ev.get("ev_pct")),
        metadata.get("entry_half_life", sig["half_life"]),
        metadata.get("entry_liquidity", sig["liquidity"]),
        ((slippage.get("leg_a") or {}).get("slippage_pct")),
        ((slippage.get("leg_b") or {}).get("slippage_pct")),
        guardrails.get("reversion_exit_z"),
        guardrails.get("stop_z_threshold"),
        guardrails.get("max_hold_hours"),
        guardrails.get("regime_break_threshold"),
        0,
        sig.get("token_id_a"),
        sig.get("token_id_b"),
        sig.get("event"),
        sig.get("market_a"),
        state_fields["trade_state_mode"],
        state_fields["reconciliation_mode"],
        state_fields["runtime_scope"],
        state_fields["canonical_ref"],
        state_fields["external_position_id"],
        state_fields["external_order_id_a"],
        state_fields["external_order_id_b"],
        state_fields["external_source"],
    ))
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE signals SET status=? WHERE id=?", ("traded", signal_id))
    conn.commit()
    conn.close()
    return trade_id


def has_open_weather_trade(token_id, runtime_scope: str | None = None):
    """Return True if there is already an open weather trade for this token."""
    conn = get_conn()
    runtime_scope = normalize_runtime_scope(runtime_scope)
    row = conn.execute(
        "SELECT id FROM trades WHERE token_id_a=? AND trade_type='weather' AND status='open' AND runtime_scope=?",
        (token_id, runtime_scope)
    ).fetchone()
    conn.close()
    return row is not None


def has_open_copy_trade(
    wallet: str,
    condition_id: str,
    external_position_id: str | None = None,
    runtime_scope: str | None = None,
) -> bool:
    """Return True if we already have an open copy trade for this wallet+market."""
    return find_open_copy_trade(
        wallet,
        condition_id=condition_id,
        external_position_id=external_position_id,
        runtime_scope=runtime_scope,
    ) is not None


def count_open_copy_trades(wallet: str | None = None, runtime_scope: str | None = None) -> int:
    conn = get_conn()
    scope = normalize_runtime_scope(runtime_scope, default="")
    params: list = []
    extra_scope = ""
    if scope in {RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY}:
        extra_scope = " AND runtime_scope=?"
        params.append(scope)
    if wallet:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE copy_wallet=? AND trade_type='copy' AND status='open'"
            + extra_scope,
            [wallet.lower(), *params],
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_type='copy' AND status='open'"
            + extra_scope,
            params,
        ).fetchone()
    conn.close()
    return int(row[0] if row else 0)


def count_open_trades(runtime_scope: str | None = None) -> int:
    conn = get_conn()
    params: list = []
    query = "SELECT COUNT(*) FROM trades WHERE status='open'"
    scope = normalize_runtime_scope(runtime_scope, default="")
    if scope in {RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY}:
        query += " AND runtime_scope=?"
        params.append(scope)
    row = conn.execute(query, params).fetchone()
    conn.close()
    return int(row[0] if row else 0)


def _fetch_weather_token_probation(conn, token_id):
    if not token_id:
        return None
    row = conn.execute(
        """
        SELECT token_id, reopen_count, last_reopened_at, last_closed_at, last_exit_reason
        FROM weather_token_probation
        WHERE token_id=?
        """,
        (token_id,),
    ).fetchone()
    return dict(row) if row else None


def get_weather_token_probation(token_id, conn=None):
    owns_conn = conn is None
    conn = conn or get_conn()
    try:
        return _fetch_weather_token_probation(conn, token_id)
    finally:
        if owns_conn:
            conn.close()


def _record_weather_token_close(conn, token_id, exit_reason, closed_at=None):
    if not token_id:
        return
    timestamp = closed_at if closed_at is not None else time.time()
    conn.execute(
        """
        INSERT INTO weather_token_probation (token_id, last_closed_at, last_exit_reason)
        VALUES (?, ?, ?)
        ON CONFLICT(token_id) DO UPDATE SET
            last_closed_at=excluded.last_closed_at,
            last_exit_reason=excluded.last_exit_reason
        """,
        (token_id, timestamp, exit_reason),
    )


def record_weather_token_close(token_id, exit_reason):
    conn = get_conn()
    _record_weather_token_close(conn, token_id, exit_reason)
    conn.commit()
    conn.close()


def _record_weather_token_reopen(conn, token_id, reopened_at=None):
    if not token_id:
        return 0
    timestamp = reopened_at if reopened_at is not None else time.time()
    conn.execute(
        """
        INSERT INTO weather_token_probation (token_id, reopen_count, last_reopened_at)
        VALUES (?, 1, ?)
        ON CONFLICT(token_id) DO UPDATE SET
            reopen_count = weather_token_probation.reopen_count + 1,
            last_reopened_at = excluded.last_reopened_at
        """,
        (token_id, timestamp),
    )
    row = _fetch_weather_token_probation(conn, token_id)
    return int(row["reopen_count"] or 0) if row else 0


def increment_weather_token_reopen(token_id):
    conn = get_conn()
    count = _record_weather_token_reopen(conn, token_id)
    conn.commit()
    conn.close()
    return count


def _evaluate_weather_token_reopen(conn, token_id, review):
    if not token_id or not review or not review.get("relax_token_guard"):
        return {"allowed": False}
    try:
        limit = int(review.get("probation_limit", DEFAULT_WEATHER_REOPEN_PROBATION_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_WEATHER_REOPEN_PROBATION_LIMIT
    probation = _fetch_weather_token_probation(conn, token_id)
    reopen_count = int(probation.get("reopen_count") or 0) if probation else 0
    if reopen_count >= limit:
        return {
            "allowed": False,
            "reason_code": "token_probation_blocked",
            "reason": (
                f"Weather token {token_id} exhausted probation ({reopen_count}/{limit}) "
                f"for review {review.get('id') or 'approved'}."
            ),
            "reopen_count": reopen_count,
            "probation_limit": limit,
        }
    return {
        "allowed": True,
        "reopen_count": reopen_count,
        "probation_limit": limit,
        "review_id": review.get("id"),
        "review_name": review.get("name"),
    }


def inspect_weather_trade_open(
    weather_signal_id,
    size_usd=100,
    max_total_open=None,
    conn=None,
    mode=None,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
):
    """Return a structured weather-trade open decision.

    This centralizes duplicate suppression, paper cash checks, and optional
    total-open gating so autonomy, API endpoints, and the dashboard can all
    surface the same blocking reason.
    """
    owns_conn = conn is None
    conn = conn or get_conn()
    runtime_scope = normalize_runtime_scope(runtime_scope)
    try:
        max_total_open = _normalize_optional_cap(max_total_open)
        sig = conn.execute(
            "SELECT * FROM weather_signals WHERE id=?", (weather_signal_id,)
        ).fetchone()
        if not sig:
            return {
                "ok": False,
                "reason_code": "signal_not_found",
                "reason": f"Weather signal {weather_signal_id} not found.",
                **_paper_position_policy_dict(),
            }

        signal_row = dict(sig)
        review = weather_risk_review.get_review_for_signal(signal_row, mode=mode)

        action = sig["action"]
        entry_token = sig["yes_token"] if action == "BUY_YES" else sig["no_token"]
        existing_signal_trade = conn.execute(
            "SELECT id FROM trades WHERE weather_signal_id=? AND status='open' AND runtime_scope=?",
            (weather_signal_id, runtime_scope),
        ).fetchone()
        if existing_signal_trade:
            trade_id = int(existing_signal_trade["id"])
            return {
                "ok": False,
                "reason_code": "signal_already_open",
                "reason": f"Weather signal {weather_signal_id} is already open as trade #{trade_id}.",
                "existing_trade_id": trade_id,
                "entry_token": entry_token,
                **_paper_position_policy_dict(),
            }

        latest_signal_trade = conn.execute(
            """
            SELECT id, status, closed_at, exit_reason
            FROM trades
            WHERE weather_signal_id=?
              AND runtime_scope=?
            ORDER BY opened_at DESC, id DESC
            LIMIT 1
            """,
            (weather_signal_id, runtime_scope),
        ).fetchone()
        if latest_signal_trade and latest_signal_trade["status"] == "closed":
            trade_id = int(latest_signal_trade["id"])
            detail = latest_signal_trade["exit_reason"] or "Weather signal already completed."
            return {
                "ok": False,
                "reason_code": "signal_already_closed",
                "reason": f"Weather signal {weather_signal_id} already completed as trade #{trade_id}.",
                "existing_trade_id": trade_id,
                "latest_trade_status": "closed",
                "latest_trade_exit_reason": detail,
                "entry_token": entry_token,
                **_paper_position_policy_dict(),
            }

        existing_token_trade = None
        if entry_token:
            existing_token_trade = conn.execute(
                """
                SELECT id, weather_signal_id
                FROM trades
                WHERE trade_type='weather' AND token_id_a=? AND status='open'
                  AND runtime_scope=?
                ORDER BY opened_at DESC, id DESC
                LIMIT 1
                """,
                (entry_token, runtime_scope),
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
                **_paper_position_policy_dict(),
            }
        closed_token_trade = None
        if entry_token:
            closed_token_trade = conn.execute(
                """
                SELECT id, weather_signal_id, closed_at, exit_reason
                FROM trades
                WHERE trade_type='weather' AND token_id_a=? AND status='closed'
                  AND runtime_scope=?
                ORDER BY closed_at DESC, id DESC
                LIMIT 1
                """,
                (entry_token, runtime_scope),
            ).fetchone()
        reopen_context = None
        if closed_token_trade:
            trade_id = int(closed_token_trade["id"])
            other_signal_id = closed_token_trade["weather_signal_id"]
            latest_reason = closed_token_trade["exit_reason"] or "Weather contract already completed."
            reopen_decision = _evaluate_weather_token_reopen(conn, entry_token, review)
            if not reopen_decision["allowed"]:
                reason_code = reopen_decision.get("reason_code") or "token_already_closed"
                reason = reopen_decision.get(
                    "reason",
                    (
                        f"Weather contract already completed as trade #{trade_id}"
                        f"{f' via signal {other_signal_id}' if other_signal_id else ''}; "
                        "do not reopen the same token after exit."
                    ),
                )
                return {
                    "ok": False,
                    "reason_code": reason_code,
                    "reason": reason,
                    "existing_trade_id": trade_id,
                    "existing_signal_id": other_signal_id,
                    "latest_trade_status": "closed",
                    "latest_trade_exit_reason": latest_reason,
                    "entry_token": entry_token,
                    **_paper_position_policy_dict(),
                }
            reopen_context = {
                "existing_trade_id": trade_id,
                "existing_signal_id": other_signal_id,
                "review_id": reopen_decision.get("review_id"),
                "review_name": reopen_decision.get("review_name"),
                "reopen_count": reopen_decision.get("reopen_count"),
                "probation_limit": reopen_decision.get("probation_limit"),
            }
            log.info(
                "Weather token %s reopened on probation (%d/%d) review=%s mode=%s",
                entry_token,
                reopen_decision.get("reopen_count"),
                reopen_decision.get("probation_limit"),
                reopen_decision.get("review_id") or "approved",
                mode,
            )

        current_open = count_open_trades(runtime_scope=runtime_scope)
        if max_total_open is not None and current_open >= max_total_open:
            return {
                "ok": False,
                "reason_code": "max_open_reached",
                "reason": f"At max open trades ({current_open}/{max_total_open}).",
                "entry_token": entry_token,
                **_paper_position_policy_dict(),
            }

        account_check = can_open_paper_trade(size_usd, runtime_scope=runtime_scope)
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
                **_paper_position_policy_dict(),
            }

        entry_price = sig["market_price"] if action == "BUY_YES" else round(1.0 - (sig["market_price"] or 0), 4)
        return {
            "ok": True,
            "reason_code": "ready",
            "reason": "Ready to open weather trade.",
            "signal": signal_row,
            "entry_token": entry_token,
            "entry_price": entry_price,
            "action": action,
            "runtime_scope": runtime_scope,
            "requested_size_usd": round(float(size_usd), 2),
            "reopen_context": reopen_context,
            **_paper_position_policy_dict(),
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
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
) -> int | None:
    """Open a paper copy trade mirroring a watched wallet's position.

    position dict should have: conditionId, outcome, curPrice, title, asset
    Returns trade_id or None if duplicate.
    """
    wallet = wallet.lower()
    identifiers = get_position_identity(position, wallet=wallet)
    condition_id = identifiers["condition_id"] or ""
    decision = inspect_copy_trade_open(
        wallet,
        position,
        size_usd=size_usd,
        max_wallet_open=max_wallet_open,
        max_total_open=max_total_open,
        runtime_scope=runtime_scope,
    )
    if not decision["ok"]:
        log.info("Paper copy trade blocked for wallet %s: %s", wallet, decision["reason"])
        return None
    outcome = position.get("outcome", "")
    side = "BUY_YES" if outcome.lower() not in ("no",) else "BUY_NO"
    entry_price = decision["entry_price"]
    outcome_norm = str(outcome or "").strip().lower()

    conn = get_conn()
    cursor = conn.execute(
        """
        INSERT INTO trades (
            trade_type, opened_at, side_a, side_b,
            entry_price_a, entry_price_b, token_id_a, size_usd, status, strategy_name,
            copy_wallet, copy_label, copy_condition_id, copy_outcome,
            event, market_a, trade_state_mode, reconciliation_mode,
            runtime_scope, canonical_ref, external_position_id, external_source
        )
        SELECT 'copy', ?, ?, '', ?, 0, ?, ?, 'open', 'copy', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM trades
            WHERE trade_type='copy'
              AND status='open'
              AND runtime_scope=?
              AND copy_wallet=?
              AND (
                    canonical_ref=?
                 OR (copy_condition_id=? AND LOWER(COALESCE(copy_outcome, ''))=?)
                 OR external_position_id=?
              )
        )
        """,
        (
            time.time(), side, entry_price,
            position.get("asset", ""), size_usd,
            wallet, label, condition_id, outcome,
            position.get("title"),
            outcome,
            TRADE_STATE_WALLET,
            RECONCILIATION_WALLET,
            normalize_runtime_scope(runtime_scope, default=RUNTIME_SCOPE_PENNY),
            decision.get("canonical_ref") or identifiers["canonical_ref"],
            decision.get("external_position_id") or identifiers["external_position_id"],
            "watched_wallet",
            normalize_runtime_scope(runtime_scope, default=RUNTIME_SCOPE_PENNY),
            wallet,
            decision.get("canonical_ref") or identifiers["canonical_ref"],
            condition_id,
            outcome_norm,
            decision.get("external_position_id") or identifiers["external_position_id"],
        ),
    )
    if cursor.rowcount <= 0:
        conn.close()
        return None
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return trade_id


def open_weather_trade(
    weather_signal_id,
    size_usd=100,
    mode=None,
    runtime_scope: str = RUNTIME_SCOPE_PAPER,
):
    """Open a single-leg paper trade from a weather signal.

    DB-level guard: returns None if an open trade already exists for this signal.
    """
    conn = get_conn()
    decision = inspect_weather_trade_open(
        weather_signal_id,
        size_usd=size_usd,
        conn=conn,
        mode=mode,
        runtime_scope=runtime_scope,
    )
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
    strategy_name = (
        decision["signal"].get("strategy_name")
        or decision["signal"].get("market_family")
        or "weather"
    )

    conn.execute("""
        INSERT INTO trades (signal_id, weather_signal_id, trade_type, opened_at,
            side_a, side_b, entry_price_a, entry_price_b,
            token_id_a, size_usd, status, strategy_name, event, market_a,
            trade_state_mode, reconciliation_mode, runtime_scope)
        VALUES (NULL, ?, 'weather', ?, ?, '', ?, 0, ?, ?, 'open', ?, ?, ?, ?, ?, ?)
    """, (
        weather_signal_id, time.time(), action, entry_price, token, size_usd,
        strategy_name,
        decision["signal"].get("event"),
        decision["signal"].get("market"),
        TRADE_STATE_PAPER,
        RECONCILIATION_INTERNAL,
        decision["runtime_scope"],
    ))
    trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("UPDATE weather_signals SET status='traded' WHERE id=?", (weather_signal_id,))
    reopen_context = decision.get("reopen_context")
    if reopen_context and token:
        _record_weather_token_reopen(conn, token)
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

    exit_reason = notes
    closed_z_score = None
    if trade_type == "pairs":
        signal = get_signal_by_id(trade["signal_id"]) if trade["signal_id"] else None
        if signal:
            beta = signal.get("beta", 1.0) or 1.0
            spread = exit_price_a - beta * exit_b
            spread_mean = signal.get("spread_mean", 0) or 0
            spread_std = signal.get("spread_std", 1) or 1
            closed_z_score = round((spread - spread_mean) / spread_std, 4) if spread_std > 0 else 0.0

    token_id = trade["token_id_a"]
    conn.execute("""
        UPDATE trades SET closed_at=?, exit_price_a=?, exit_price_b=?,
            pnl=?, status='closed', notes=?, exit_reason=?, closed_z_score=?
        WHERE id=?
    """, (time.time(), exit_price_a, exit_b, pnl_usd, notes, exit_reason, closed_z_score, trade_id))
    if trade_type == "weather" and trade["weather_signal_id"]:
        conn.execute(
            "UPDATE weather_signals SET status='closed' WHERE id=?",
            (trade["weather_signal_id"],),
        )
        if token_id:
            _record_weather_token_close(conn, token_id, exit_reason)
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
        t.copy_outcome, t.whale_alert_id, t.strategy_name, t.entry_grade_label,
        t.admission_path, t.experiment_name, t.experiment_status, t.entry_z_score,
        t.entry_ev_pct, t.entry_half_life, t.entry_liquidity, t.entry_slippage_pct_a,
        t.entry_slippage_pct_b, t.reversion_exit_z, t.stop_z_threshold, t.max_hold_hours,
        t.closed_z_score, t.exit_reason, t.max_unrealized_profit, t.max_unrealized_drawdown,
        t.regime_break_threshold, t.regime_break_flag, t.regime_break_notes,
        t.trade_state_mode, t.reconciliation_mode, t.runtime_scope, t.canonical_ref,
        t.external_position_id, t.external_order_id_a, t.external_order_id_b, t.external_source,
        ww.active AS copy_wallet_active,
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


def get_trades(status=None, limit=50, runtime_scope: str | None = None):
    conn = get_conn()
    clauses = []
    params: list = []
    scope = normalize_runtime_scope(runtime_scope, default="")
    if status:
        clauses.append("t.status=?")
        params.append(status)
    if scope in {RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY}:
        clauses.append("t.runtime_scope=?")
        params.append(scope)
    query = _TRADES_SELECT
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY t.opened_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
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
    limit = _normalize_query_limit(limit, "get_snapshots")
    conn = get_conn()
    if limit is None:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE trade_id=? ORDER BY timestamp ASC",
            (trade_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE trade_id=? ORDER BY timestamp ASC LIMIT ?",
            (trade_id, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_pairs_trade_metrics(
    trade_id,
    current_pnl=None,
    current_z_score=None,
    regime_break=False,
    regime_break_note=None,
):
    """Track rolling drawdown/profit and regime-break flags for an open pairs trade."""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT max_unrealized_profit, max_unrealized_drawdown, regime_break_flag, regime_break_notes
        FROM trades
        WHERE id=?
        """,
        (trade_id,),
    ).fetchone()
    if not row:
        conn.close()
        return False

    values = {
        "max_unrealized_profit": row["max_unrealized_profit"] or 0.0,
        "max_unrealized_drawdown": row["max_unrealized_drawdown"] or 0.0,
        "regime_break_flag": row["regime_break_flag"] or 0,
        "regime_break_notes": row["regime_break_notes"],
    }
    if current_pnl is not None:
        values["max_unrealized_profit"] = max(values["max_unrealized_profit"], float(current_pnl))
        values["max_unrealized_drawdown"] = min(values["max_unrealized_drawdown"], float(current_pnl))
    if regime_break:
        values["regime_break_flag"] = 1
        if regime_break_note and regime_break_note != values["regime_break_notes"]:
            values["regime_break_notes"] = regime_break_note

    conn.execute(
        """
        UPDATE trades
        SET max_unrealized_profit=?,
            max_unrealized_drawdown=?,
            regime_break_flag=?,
            regime_break_notes=?,
            closed_z_score=COALESCE(closed_z_score, ?)
        WHERE id=?
        """,
        (
            round(values["max_unrealized_profit"], 2),
            round(values["max_unrealized_drawdown"], 2),
            values["regime_break_flag"],
            values["regime_break_notes"],
            current_z_score,
            trade_id,
        ),
    )
    conn.commit()
    conn.close()
    return True


# --- Weather Signals ---

def save_weather_signal(opp):
    """Save a weather-edge opportunity."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO weather_signals (
            timestamp, event, market, strategy_name, market_family, market_id, yes_token, no_token,
            city, lat, lon, target_date, threshold_f, direction,
            resolution_source, station_id, station_label, settlement_unit,
            settlement_precision, station_timezone, outcome_label,
            market_price,
            noaa_forecast_f, noaa_prob, noaa_sigma_f,
            om_forecast_f, om_prob,
            combined_prob, combined_edge, combined_edge_pct,
            selected_prob, selected_edge, selected_edge_pct, correction_mode, correction_json,
            source_meta_json,
            sources_agree, sources_available,
            hours_ahead, ev_pct, kelly_fraction, action, tradeable, liquidity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        time.time(), opp["event"], opp["market"],
        opp.get("strategy_name") or opp.get("market_family") or "weather_threshold",
        opp.get("market_family") or "weather_threshold",
        opp.get("market_id"),
        opp.get("yes_token"), opp.get("no_token"),
        opp["city"], opp["lat"], opp["lon"],
        opp["target_date"], opp["threshold_f"], opp["direction"],
        opp.get("resolution_source"),
        opp.get("station_id"),
        opp.get("station_label"),
        opp.get("settlement_unit"),
        opp.get("settlement_precision"),
        opp.get("station_timezone"),
        opp.get("outcome_label"),
        opp["market_price"],
        opp.get("noaa_forecast_f"), opp.get("noaa_prob"), opp.get("noaa_sigma_f"),
        opp.get("om_forecast_f"), opp.get("om_prob"),
        opp["combined_prob"], opp["combined_edge"], opp["combined_edge_pct"],
        opp.get("selected_prob", opp.get("combined_prob")),
        opp.get("selected_edge", opp.get("combined_edge")),
        opp.get("selected_edge_pct", opp.get("combined_edge_pct")),
        opp.get("correction_mode"),
        json.dumps(opp.get("correction")) if opp.get("correction") else None,
        json.dumps(opp.get("source_meta")) if opp.get("source_meta") else None,
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
    limit = _normalize_query_limit(limit, "get_weather_signals")
    base = """
        SELECT ws.*
        FROM weather_signals ws
    """
    conn = get_conn()
    if tradeable_only and limit is None:
        rows = conn.execute(
            base + " WHERE ws.tradeable=1 ORDER BY ws.timestamp DESC"
        ).fetchall()
    elif tradeable_only:
        rows = conn.execute(
            base + " WHERE ws.tradeable=1 ORDER BY ws.timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    elif limit is None:
        rows = conn.execute(
            base + " ORDER BY ws.timestamp DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            base + " ORDER BY ws.timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    results = []
    try:
        for row in rows:
            item = _deserialize_weather_signal_row(row)
            latest_trade = conn.execute(
                """
                SELECT id, status, closed_at, exit_reason
                FROM trades
                WHERE weather_signal_id=?
                ORDER BY opened_at DESC, id DESC
                LIMIT 1
                """,
                (item["id"],),
            ).fetchone()
            exact_open_trade = conn.execute(
                """
                SELECT id
                FROM trades
                WHERE weather_signal_id=? AND status='open'
                ORDER BY opened_at DESC, id DESC
                LIMIT 1
                """,
                (item["id"],),
            ).fetchone()
            decision = inspect_weather_trade_open(item["id"], size_usd=20, conn=conn)
            exact_open_trade_id = int(exact_open_trade["id"]) if exact_open_trade else None
            latest_trade_id = int(latest_trade["id"]) if latest_trade else None
            latest_trade_status = latest_trade["status"] if latest_trade else None
            item["open_trade_id"] = exact_open_trade_id
            item["has_open_trade"] = exact_open_trade_id is not None
            blocked_reason_codes = {
                "signal_already_open",
                "token_already_open",
                "signal_already_closed",
                "token_already_closed",
                "token_probation_blocked",
            }
            item["blocked_by_trade_id"] = (
                decision.get("existing_trade_id")
                if not decision["ok"]
                and decision.get("reason_code") in blocked_reason_codes
                and decision.get("existing_trade_id") != exact_open_trade_id
                else None
            )
            item["latest_trade_id"] = latest_trade_id
            item["latest_trade_status"] = latest_trade_status
            item["latest_trade_exit_reason"] = latest_trade["exit_reason"] if latest_trade else None
            item["latest_trade_closed_at"] = latest_trade["closed_at"] if latest_trade else None
            item["can_open_trade"] = bool(item.get("tradeable")) and decision["ok"]
            item["blocking_reason"] = None if decision["ok"] else decision.get("reason")
            item["blocking_reason_code"] = None if decision["ok"] else decision.get("reason_code")
            item["entry_token"] = decision.get("entry_token")
            if exact_open_trade_id is not None:
                item["status"] = "open"
                item["status_detail"] = f"Open as trade #{exact_open_trade_id}."
            elif latest_trade_status == "closed":
                item["status"] = "closed"
                item["status_detail"] = latest_trade["exit_reason"] or f"Closed as trade #{latest_trade_id}."
            elif item.get("blocking_reason"):
                item["status"] = "blocked"
                item["status_detail"] = item["blocking_reason"]
            else:
                item["status"] = item.get("status") or "new"
                item["status_detail"] = None
            results.append(item)
    finally:
        conn.close()
    return results


def get_weather_signal_by_id(signal_id):
    """Fetch a single weather signal by id."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM weather_signals WHERE id=?", (signal_id,)).fetchone()
    conn.close()
    return _deserialize_weather_signal_row(row) if row else None


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
    limit = _normalize_query_limit(limit, "get_locked_arb")
    conn = get_conn()
    if tradeable_only and limit is None:
        rows = conn.execute(
            "SELECT * FROM locked_arb WHERE tradeable=1 ORDER BY timestamp DESC"
        ).fetchall()
    elif tradeable_only:
        rows = conn.execute(
            "SELECT * FROM locked_arb WHERE tradeable=1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    elif limit is None:
        rows = conn.execute(
            "SELECT * FROM locked_arb ORDER BY timestamp DESC"
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
    cap_enabled = bool(settings.get("cap_enabled", False))
    per_wallet_cap = _normalize_optional_cap(settings.get("per_wallet_cap"))
    total_open_cap = _normalize_optional_cap(settings.get("total_open_cap"))
    return {
        "cap_enabled": cap_enabled,
        "per_wallet_cap": per_wallet_cap,
        "total_open_cap": total_open_cap,
        "effective_per_wallet_cap": per_wallet_cap if cap_enabled else None,
        "effective_total_open_cap": total_open_cap if cap_enabled else None,
        "caps_active": bool(cap_enabled and (per_wallet_cap is not None or total_open_cap is not None)),
        **_paper_position_policy_dict(),
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
    limit = _normalize_query_limit(limit, "get_longshot_signals")
    conn = get_conn()
    if tradeable_only and limit is None:
        rows = conn.execute(
            "SELECT * FROM longshot_signals WHERE tradeable=1 ORDER BY timestamp DESC"
        ).fetchall()
    elif tradeable_only:
        rows = conn.execute(
            "SELECT * FROM longshot_signals WHERE tradeable=1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    elif limit is None:
        rows = conn.execute(
            "SELECT * FROM longshot_signals ORDER BY timestamp DESC"
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
    limit = _normalize_query_limit(limit, "get_near_certainty_signals")
    conn = get_conn()
    if tradeable_only and limit is None:
        rows = conn.execute(
            "SELECT * FROM near_certainty_signals WHERE tradeable=1 ORDER BY timestamp DESC"
        ).fetchall()
    elif tradeable_only:
        rows = conn.execute(
            "SELECT * FROM near_certainty_signals WHERE tradeable=1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    elif limit is None:
        rows = conn.execute(
            "SELECT * FROM near_certainty_signals ORDER BY timestamp DESC"
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
    limit = _normalize_query_limit(limit, "get_whale_alerts")
    conn = get_conn()
    where = "WHERE suspicion_score >= ?"
    params = [min_score]
    if undismissed_only:
        where += " AND dismissed = 0"
    if limit is None:
        rows = conn.execute(
            f"SELECT * FROM whale_alerts {where} ORDER BY timestamp DESC",
            tuple(params),
        ).fetchall()
    else:
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
    limit = _normalize_query_limit(limit, "get_latest_copy_trades")
    conn = get_conn()
    if limit is None:
        rows = conn.execute("""
            SELECT id, opened_at, status, copy_wallet, copy_label,
                   copy_condition_id, copy_outcome, size_usd
            FROM trades
            WHERE trade_type='copy'
            ORDER BY opened_at DESC
        """).fetchall()
    else:
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
    limit = _normalize_query_limit(limit, "get_scan_runs")
    conn = get_conn()
    if limit is None:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY timestamp DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scan_runs ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Stats ---

def get_stats(runtime_scope: str | None = None):
    """Dashboard summary stats."""
    conn = get_conn()
    scope = normalize_runtime_scope(runtime_scope, default="")
    scope_clause = ""
    params: list = []
    if scope in {RUNTIME_SCOPE_PAPER, RUNTIME_SCOPE_PENNY}:
        scope_clause = " AND runtime_scope=?"
        params.append(scope)
    total_trades = conn.execute("SELECT COUNT(*) FROM trades" + (" WHERE runtime_scope=?" if scope_clause else ""), params).fetchone()[0]
    open_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'" + scope_clause, params).fetchone()[0]
    _excl = "AND (notes IS NULL OR notes != 'manual close - dedup cleanup')"
    closed_trades = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE status='closed' {scope_clause} {_excl}",
        params,
    ).fetchone()[0]
    total_pnl = conn.execute(
        f"SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed' {scope_clause} {_excl}",
        params,
    ).fetchone()[0]
    wins = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0 {scope_clause} {_excl}",
        params,
    ).fetchone()[0]
    losses = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl <= 0 {scope_clause} {_excl}",
        params,
    ).fetchone()[0]

    total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    total_scans = conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]

    # Cumulative P&L series — only closed trades with real P&L, sorted by close time
    pnl_rows = conn.execute("""
        SELECT closed_at, pnl FROM trades
        WHERE status='closed' AND pnl IS NOT NULL AND pnl != 0
    """ + ("" if not scope_clause else " AND runtime_scope=?") + """
        ORDER BY closed_at ASC
    """, params).fetchall()

    conn.close()

    win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0
    runtime_scope_value = scope or RUNTIME_SCOPE_PAPER
    runtime_account = get_runtime_account_overview(refresh_unrealized=True, runtime_scope=runtime_scope_value)
    strategy_breakdown = runtime_account["strategy_breakdown"]
    paper_sizing = get_paper_sizing_summary(limit=200) if runtime_scope_value == RUNTIME_SCOPE_PAPER else None

    # Build cumulative series: each point is the running total after that trade closes
    cumulative = 0.0
    pnl_series = []
    for closed_at, pnl in pnl_rows:
        cumulative += pnl
        pnl_series.append({"t": closed_at, "pnl": round(cumulative, 2)})

    return {
        "runtime_scope": scope or RUNTIME_SCOPE_PAPER,
        "total_trades": total_trades,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "total_pnl": round(total_pnl, 2),
        "realized_pnl": runtime_account.get("realized_pnl", runtime_account.get("realized_pnl_usd", 0.0)),
        "unrealized_pnl": runtime_account.get("unrealized_pnl", runtime_account.get("unrealized_pnl_usd", 0.0)),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_signals": total_signals,
        "total_scans": total_scans,
        "pnl_series": pnl_series,
        "runtime_account": runtime_account,
        "strategy_breakdown": strategy_breakdown,
        "paper_sizing": paper_sizing,
        "cointegration_trial": get_cointegration_trial_summary(),
    }


def _latest_pairs_snapshot_rows(conn):
    return conn.execute(
        """
        SELECT t.id, t.status, t.opened_at, t.closed_at, t.pnl, t.size_usd,
               t.entry_price_a, t.entry_price_b, t.side_a, t.entry_grade_label,
               t.admission_path, t.experiment_name, t.experiment_status,
               t.max_unrealized_profit, t.max_unrealized_drawdown,
               t.regime_break_flag, t.regime_break_notes,
               t.closed_z_score, t.exit_reason,
               s.price_a AS snap_price_a, s.price_b AS snap_price_b
        FROM trades t
        LEFT JOIN snapshots s ON s.id = (
            SELECT s2.id
            FROM snapshots s2
            WHERE s2.trade_id = t.id
            ORDER BY s2.timestamp DESC, s2.id DESC
            LIMIT 1
        )
        WHERE t.trade_type='pairs'
          AND t.strategy_name='cointegration'
          AND (
            t.entry_grade_label='A+'
            OR (t.entry_grade_label='A' AND t.admission_path='paper_a_trial')
          )
        ORDER BY t.opened_at ASC, t.id ASC
        """
    ).fetchall()


def _empty_trial_bucket(label):
    return {
        "label": label,
        "trade_count": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "avg_hold_hours": 0.0,
        "avg_open_hold_hours": 0.0,
        "avg_mae_usd": 0.0,
        "worst_mae_usd": 0.0,
        "avg_mfe_usd": 0.0,
        "best_mfe_usd": 0.0,
        "regime_break_trades": 0,
        "regime_break_rate": 0.0,
        "regime_break_notes": [],
    }


def get_cointegration_trial_summary():
    """Return A-trial vs A+ paper performance and rejection instrumentation."""
    conn = get_conn()
    now = time.time()
    rows = _latest_pairs_snapshot_rows(conn)
    rejection_rows = conn.execute(
        """
        SELECT COALESCE(experiment_reason_code, 'unknown') AS reason_code, COUNT(*) AS count
        FROM signals
        WHERE grade_label='A'
        GROUP BY COALESCE(experiment_reason_code, 'unknown')
        ORDER BY count DESC, reason_code ASC
        """
    ).fetchall()
    signal_counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN grade_label='A' THEN 1 ELSE 0 END) AS a_signals,
            SUM(CASE WHEN grade_label='A' AND experiment_status='eligible' THEN 1 ELSE 0 END) AS a_eligible,
            SUM(CASE WHEN grade_label='A' AND experiment_status='rejected' THEN 1 ELSE 0 END) AS a_rejected,
            SUM(CASE WHEN grade_label='A+' THEN 1 ELSE 0 END) AS a_plus_signals
        FROM signals
        """
    ).fetchone()
    conn.close()

    buckets = {
        "A+": _empty_trial_bucket("A+"),
        "A": _empty_trial_bucket("A"),
    }
    hold_totals = {"A+": 0.0, "A": 0.0}
    open_hold_totals = {"A+": 0.0, "A": 0.0}
    mae_totals = {"A+": 0.0, "A": 0.0}
    mfe_totals = {"A+": 0.0, "A": 0.0}

    for row in rows:
        cohort = "A" if row["entry_grade_label"] == "A" else "A+"
        bucket = buckets[cohort]
        bucket["trade_count"] += 1
        mae = min(0.0, float(row["max_unrealized_drawdown"] or 0.0))
        mfe = max(0.0, float(row["max_unrealized_profit"] or 0.0))
        mae_totals[cohort] += mae
        mfe_totals[cohort] += mfe
        bucket["worst_mae_usd"] = min(bucket["worst_mae_usd"], mae)
        bucket["best_mfe_usd"] = max(bucket["best_mfe_usd"], mfe)
        if row["regime_break_flag"]:
            bucket["regime_break_trades"] += 1
            note = row["regime_break_notes"]
            if note and note not in bucket["regime_break_notes"]:
                bucket["regime_break_notes"].append(note)

        if row["status"] == "closed":
            bucket["closed_trades"] += 1
            pnl = float(row["pnl"] or 0.0)
            bucket["realized_pnl"] += pnl
            if pnl > 0:
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1
            hold_hours = ((row["closed_at"] or now) - (row["opened_at"] or now)) / 3600
            hold_totals[cohort] += max(0.0, hold_hours)
        else:
            bucket["open_trades"] += 1
            hold_hours = (now - (row["opened_at"] or now)) / 3600
            open_hold_totals[cohort] += max(0.0, hold_hours)
            if row["snap_price_a"] is not None and row["snap_price_b"] is not None:
                valuation = calculate_pairs_mark_to_market(
                    row["size_usd"],
                    row["entry_price_a"],
                    row["snap_price_a"],
                    row["entry_price_b"],
                    row["snap_price_b"],
                    row["side_a"],
                )
                if valuation["ok"]:
                    bucket["unrealized_pnl"] += float(valuation["pnl_usd"])

    for cohort, bucket in buckets.items():
        count = bucket["trade_count"]
        closed = bucket["closed_trades"]
        opens = bucket["open_trades"]
        bucket["win_rate"] = round((bucket["wins"] / closed * 100) if closed else 0.0, 1)
        bucket["realized_pnl"] = round(bucket["realized_pnl"], 2)
        bucket["unrealized_pnl"] = round(bucket["unrealized_pnl"], 2)
        bucket["avg_hold_hours"] = round((hold_totals[cohort] / closed) if closed else 0.0, 2)
        bucket["avg_open_hold_hours"] = round((open_hold_totals[cohort] / opens) if opens else 0.0, 2)
        bucket["avg_mae_usd"] = round((mae_totals[cohort] / count) if count else 0.0, 2)
        bucket["worst_mae_usd"] = round(bucket["worst_mae_usd"], 2)
        bucket["avg_mfe_usd"] = round((mfe_totals[cohort] / count) if count else 0.0, 2)
        bucket["best_mfe_usd"] = round(bucket["best_mfe_usd"], 2)
        bucket["regime_break_rate"] = round((bucket["regime_break_trades"] / count * 100) if count else 0.0, 1)

    rejections = [{"reason_code": row["reason_code"], "count": int(row["count"])} for row in rejection_rows]
    return {
        "signals_seen": {
            "a_plus": int(signal_counts["a_plus_signals"] or 0),
            "a": int(signal_counts["a_signals"] or 0),
            "a_trial_eligible": int(signal_counts["a_eligible"] or 0),
            "a_trial_rejected": int(signal_counts["a_rejected"] or 0),
        },
        "cohorts": {
            "a_plus": buckets["A+"],
            "a_trial": buckets["A"],
        },
        "rejection_reasons": rejections,
        "summary_generated_at": now,
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
    runtime_scope = normalize_runtime_scope(trade_data.get("runtime_scope"))
    account_check = can_open_paper_trade(trade_data.get("size_usd", 0), runtime_scope=runtime_scope)
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
                strategy_name, whale_alert_id, event, market_a, notes,
                trade_state_mode, reconciliation_mode, runtime_scope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            trade_data.get('strategy_name', 'whale'),
            trade_data.get('whale_alert_id'),
            trade_data['event'],
            trade_data['market_a'],
            trade_data.get('notes') or f"Suspicion: {trade_data.get('suspicion_score', 0)}/100",
            trade_data.get('trade_state_mode', TRADE_STATE_PAPER),
            trade_data.get('reconciliation_mode', RECONCILIATION_INTERNAL),
            runtime_scope,
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
