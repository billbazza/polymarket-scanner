# 2026-04-01 Copy Trader Paper Gate Last Checked At

## Source
- Operator-facing failure in the copy-trader paper gate flow: `copy_trader_step_failed` with `Copy trader step failed: no such column: last_checked_at in paper gate tab`.
- Followed the repo contract from [AGENTS.md](/Users/will/.cline/worktrees/9f473/polymarket-scanner/AGENTS.md), especially Database auto-migration behavior and the requirement to log bug-fix work under `fix_logs/`.

## Findings
- Watched-wallet polling state is persisted in [db.py](/Users/will/.cline/worktrees/9f473/polymarket-scanner/db.py) via `update_watched_wallet_poll_status()` and `record_wallet_monitor_event()`, both of which update `watched_wallets.last_checked_at` and related monitor-status columns.
- Operator-facing copy-trader status surfaces in [server.py](/Users/will/.cline/worktrees/9f473/polymarket-scanner/server.py) and [dashboard.html](/Users/will/.cline/worktrees/9f473/polymarket-scanner/dashboard.html) expect those fields for watched-wallet status cards and polling telemetry.
- Existing local `scanner.db` files could still miss those columns if `schema_migrations` already recorded the historical backfill migration as applied before the current watched-wallet monitor fields were added. In that state, startup skipped the backfill forever and later writes failed with `no such column: last_checked_at`.

## Fixes Applied
- Added an explicit watched-wallet schema-heal pass in [db.py](/Users/will/.cline/worktrees/9f473/polymarket-scanner/db.py) that runs during `init_db()` after normal migrations and forward-fills the watched-wallet monitor columns when the table exists but the historical migration will not rerun.
- Kept the repair idempotent and backward-compatible for existing local `scanner.db` files by using the repo’s `ALTER TABLE ... ADD COLUMN if missing` pattern instead of requiring manual migration steps or schema resets.
- Added a regression in [tests/test_watched_wallet_monitoring.py](/Users/will/.cline/worktrees/9f473/polymarket-scanner/tests/test_watched_wallet_monitoring.py) that builds a partially migrated SQLite file where `002_backfill_columns` is already marked applied but `watched_wallets.last_checked_at` is absent, then verifies `init_db()` repairs the schema and watched-wallet poll updates succeed.

## Verification
- `python3 -m pytest tests/test_watched_wallet_monitoring.py -q`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, wallet_monitor, server; print('OK')"`
