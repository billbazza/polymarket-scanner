# 2026-04-01 Paper Trade Blocking Visibility

## Source
- Operator request to make paper-trade open blockers visible in the dashboard instead of requiring `scanner.log` or `journal.jsonl` inspection.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/28018/polymarket-scanner/AGENTS.md), especially Architecture, Autonomy Loop, Database, and Always Do.

## Problem
- Manual pairs, weather, and copy-trade APIs returned structured blockers, but operators had to trigger the action and inspect toast text or API payloads to see them.
- Autonomy recorded many real gate decisions only in `logs/journal.jsonl`, which left no concise operator-facing dashboard view for duplicate suppression, cash exhaustion, copy caps, scan/filter skips, stale refresh failures, or cycle errors.
- The dashboard exposed a blocker inline for weather rows, but there was no unified recent-attempt feed across strategies and autonomy stages.

## Fixes Applied
- Added `paper_trade_attempts` persistence in [db.py](/Users/will/.cline/worktrees/28018/polymarket-scanner/db.py) with a migration, normalized operator-safe reason text, and query/summary helpers for recent allowed, blocked, and error outcomes.
- Updated [server.py](/Users/will/.cline/worktrees/28018/polymarket-scanner/server.py) so manual paper-open endpoints for pairs, weather, and copy trades write explicit attempt records for blocked, allowed, and post-preflight open failures.
- Added `/api/paper-trade-attempts` in [server.py](/Users/will/.cline/worktrees/28018/polymarket-scanner/server.py) to expose the recent operator feed plus a blocker summary for the dashboard.
- Updated [autonomy.py](/Users/will/.cline/worktrees/28018/polymarket-scanner/autonomy.py) to persist paper-facing gate decisions for:
  - strategy admission / non-tradeable scan outcomes
  - duplicate signal or event suppression
  - brain rejections
  - weather and copy-trader cap / duplicate / cash blockers
  - refresh, maker-order, auto-close, weather-scan, copy-fetch, and autonomy-cycle failures
- Updated [dashboard.html](/Users/will/.cline/worktrees/28018/polymarket-scanner/dashboard.html) with a new `Paper Gate` panel that shows recent attempts with source, strategy, decision, reason, and key refs, plus a concise allowed/blocked/error summary and top blockers.
- Added focused regression coverage in [tests/test_paper_trade_attempts.py](/Users/will/.cline/worktrees/28018/polymarket-scanner/tests/test_paper_trade_attempts.py) for attempt-summary accounting and the blocked manual pairs endpoint path.

## Verification
- `python3 -m py_compile db.py server.py autonomy.py`
- `python3 -m unittest tests.test_paper_trade_attempts`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
