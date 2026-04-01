# 2026-04-01 Autonomy Datatype Mismatch

## Source
- Server-triggered autonomy cycle crash on 2026-04-01: `ERROR [scanner.server] Autonomy cycle failed: datatype mismatch`.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/6c0c6/polymarket-scanner/AGENTS.md), especially Architecture, Database, Always Do, and Testing Changes.

## Root Cause
- The crash matched a SQLite-specific edge case: binding `None` into `LIMIT ?` raises `sqlite3.IntegrityError: datatype mismatch`.
- The autonomy loop intentionally uses `limit=None` when it reloads all open trades during the server-triggered cycle so deduping, accounting, and auto-close decisions see the full book.
- `db.get_trades(limit=None)` had already been hardened in the current worktree, which explains why the latest local reproduction no longer failed there, but the same `LIMIT ?` footgun still existed across several other DB readers touched by autonomy/server flows and dashboard follow-ups.
- Without central normalization, any future reader called with `limit=None` would surface the same opaque SQLite error instead of identifying the offending query.

## Fixes Applied
- Added `_normalize_query_limit()` in [db.py](/Users/will/.cline/worktrees/6c0c6/polymarket-scanner/db.py) so optional limits are normalized before SQLite execution.
- Updated limit-based readers in [db.py](/Users/will/.cline/worktrees/6c0c6/polymarket-scanner/db.py) to branch cleanly between bounded queries and unbounded queries instead of ever binding `NULL` into `LIMIT ?`:
  - `get_signals()`
  - `get_snapshots()`
  - `get_weather_signals()`
  - `get_locked_arb()`
  - `get_longshot_signals()`
  - `get_near_certainty_signals()`
  - `get_whale_alerts()`
  - `get_latest_copy_trades()`
  - `get_scan_runs()`
- Tightened failure reporting in [autonomy.py](/Users/will/.cline/worktrees/6c0c6/polymarket-scanner/autonomy.py) by tracking the current stage and logging uncaught exceptions with stage context and traceback.
- Updated [server.py](/Users/will/.cline/worktrees/6c0c6/polymarket-scanner/server.py) to use `log.exception(...)` for background autonomy failures so future crashes retain stack traces in `scanner.log`.
- Added regression coverage in [test_all.py](/Users/will/.cline/worktrees/6c0c6/polymarket-scanner/test_all.py) for `limit=None` across the DB readers used by autonomy/dashboard flows, plus an invalid-limit case that now raises a contextual `ValueError` instead of a raw SQLite error.

## Verification
- `python3 -m py_compile db.py autonomy.py server.py test_all.py`
- `python3 test_all.py`
