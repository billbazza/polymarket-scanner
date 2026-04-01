# 2026-04-01 Autonomy Paper Attempt Fallback

## Source
- Operator report from 2026-04-01: autonomy crashed with `AttributeError: module db has no attribute record_paper_trade_attempt` during `autonomy.py` `record_attempt`, and the same issue surfaced again in the server background autonomy thread.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/2239a/polymarket-scanner/AGENTS.md), especially Architecture, Database, Autonomy Loop, Never Do, and Always Do.

## Problem
- Recent paper-trade-attempt instrumentation assumed `db.record_paper_trade_attempt()` and the attempt-feed query helpers were always present.
- That assumption was too strong for mixed deployments where `autonomy.py` or `server.py` had been updated but `db.py` or the attempt-log migration was missing or incomplete.
- Because the logging call sat on the autonomy critical path, a missing helper turned a non-essential operator-visibility feature into a cycle-killing exception.

## Fixes Applied
- Hardened [autonomy.py](/Users/will/.cline/worktrees/2239a/polymarket-scanner/autonomy.py) so paper-trade-attempt writes are best-effort and never crash the autonomy cycle when the DB helper is missing or throws.
- Hardened [server.py](/Users/will/.cline/worktrees/2239a/polymarket-scanner/server.py) so manual trade endpoints, the background autonomy runner, and `/api/paper-trade-attempts` all degrade cleanly when attempt logging support is unavailable.
- Hardened [db.py](/Users/will/.cline/worktrees/2239a/polymarket-scanner/db.py) so attempt writes and reads safely return fallback values if the `paper_trade_attempts` table is absent or SQLite raises an operational error.
- Updated [dashboard.html](/Users/will/.cline/worktrees/2239a/polymarket-scanner/dashboard.html) so the `Paper Gate` panel explains when the attempt feed is unavailable instead of silently rendering an empty table.
- Added regression coverage in [tests/test_paper_trade_attempts.py](/Users/will/.cline/worktrees/2239a/polymarket-scanner/tests/test_paper_trade_attempts.py) for:
- missing `db.record_paper_trade_attempt` on blocked manual trade flow
- missing attempt-feed helpers on `/api/paper-trade-attempts`
- missing attempt logger during direct `autonomy.record_attempt()` calls

## Verification
- `python3 -m py_compile db.py server.py autonomy.py`
- `python3 -m unittest tests.test_paper_trade_attempts`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
