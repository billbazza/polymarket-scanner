# 2026-04-05 Penny Ledger Stat Reconciliation

## Source
- Operator request dated 2026-04-05: when penny mode is active, every portfolio/stat surface must reflect only penny/live-ledger trades, including open trades, closed history, win rate, PnL, bankroll/exposure, and max-open usage.
- Followed [AGENTS.md](/Users/will/.cline/worktrees/743ab/polymarket-scanner/AGENTS.md): keep audit logging intact, log the bug-fix work in `fix_logs/`, and sync repo guidance when dashboard/runtime semantics change.

## Findings
- The repo already scoped most trade listings by `runtime_scope`, but the penny account summary still reused a paper-oriented accumulator path for closed-trade reporting and did not independently prove that the returned totals matched the penny ledger exactly.
- `get_stats()` excluded manual dedup-cleanup closes while `get_paper_account_state(runtime_scope="penny")` did not, which meant penny closed-trade cards could diverge from penny summary totals.
- The dashboard max-open card inferred penny usage from client-side account fields instead of receiving a scoped backend usage count from `/api/autonomy/runtime`.
- Strategy rows in penny mode counted scoped positions as primary capital but failed to preserve the live/wallet state counters consistently, which weakened penny-lane operator visibility.

## Fixes Applied
- Updated [db.py](/Users/will/.cline/worktrees/743ab/polymarket-scanner/db.py) so reporting consistently skips manual dedup-cleanup closes across account/state/stat paths.
- Added exact scoped-trade reconciliation in [db.py](/Users/will/.cline/worktrees/743ab/polymarket-scanner/db.py):
  - `get_runtime_scope_trade_reconciliation()` now recomputes open/closed counts, realized/unrealized PnL, committed capital, and win/loss totals directly from the selected scope’s trade set.
  - `get_stats()` now returns `trade_reconciliation` plus `acceptance_checks`, comparing the dashboard-facing stats/account fields against that scoped ledger.
  - Runtime account payloads now carry the same `trade_reconciliation` block so penny-mode debugging can validate the returned wallet/account summary against the penny ledger without reading mixed paper totals.
- Updated [server.py](/Users/will/.cline/worktrees/743ab/polymarket-scanner/server.py) `/api/autonomy/runtime` to return scoped `open_positions`, `max_open_usage`, and `slots_remaining`, so penny max-open usage is explicitly backend-scoped instead of implied by the browser.
- Updated [dashboard.html](/Users/will/.cline/worktrees/743ab/polymarket-scanner/dashboard.html) so the runtime panel renders backend-scoped max-open usage and surfaces a reconciliation warning if scoped stats ever drift from the scoped ledger.
- Synced [AGENTS.md](/Users/will/.cline/worktrees/743ab/polymarket-scanner/AGENTS.md), [CLAUDE.md](/Users/will/.cline/worktrees/743ab/polymarket-scanner/CLAUDE.md), and [GEMINI.md](/Users/will/.cline/worktrees/743ab/polymarket-scanner/GEMINI.md) to state that penny mode must exclude paper-derived closed-trade history and that `/api/stats`, `/api/runtime/account`, and `/api/autonomy/runtime` must reconcile exactly to the selected scope.

## Acceptance Checks
- `GET /api/stats?runtime_scope=penny` returns `acceptance_checks.all_passed = true` and `trade_reconciliation.total_trades` matches only the penny trade set.
- `GET /api/runtime/account?runtime_scope=penny` returns `trade_reconciliation` totals that match penny-only deployed capital, realized PnL, unrealized PnL, and open positions.
- `GET /api/autonomy/runtime?runtime_scope=penny` returns penny-only `open_positions`, `max_open_usage`, and `slots_remaining`.
- Switching the dashboard to Penny shows:
  - penny-only open trades
  - penny-only closed trade history
  - penny-only win rate / PnL / exposure cards
  - penny-only max-open usage in the runtime panel

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m unittest tests.test_strategy_performance`
- `python3 -m py_compile db.py server.py`
