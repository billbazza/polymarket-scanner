# 2026-04-01 Paper Accounting Fix Log

## Source
- Paper-trading bankroll/accounting/dashboard clarity task.
- `AGENTS.md` was not present in this checkout, so repo contract guidance was taken from [CLAUDE.md](/Users/will/.cline/worktrees/ed369/polymarket-scanner/CLAUDE.md).

## Fixes Applied
- Replaced the in-memory paper balance tracker in [execution.py](/Users/will/.cline/worktrees/ed369/polymarket-scanner/execution.py) with SQLite-backed paper account reporting.
- Added a persistent `paper_accounts` table and paper-account helpers in [db.py](/Users/will/.cline/worktrees/ed369/polymarket-scanner/db.py) with a default starting bankroll of `$2000`.
- Made paper-account reporting explicit for starting bankroll, available cash, committed capital, realized P&L, unrealized P&L, cumulative losses, and total equity.
- Made paper trade opens consume available cash immediately by deriving cash from `starting bankroll + realized P&L - committed open trade size`.
- Blocked new paper trade opens when available cash is insufficient for pairs, weather, whale, and copy-trading paths.
- Extended [server.py](/Users/will/.cline/worktrees/ed369/polymarket-scanner/server.py) stats/trade responses with paper-account data and added a dedicated `/api/paper-account` endpoint.
- Reworked the dashboard summary in [dashboard.html](/Users/will/.cline/worktrees/ed369/polymarket-scanner/dashboard.html) so the bankroll breakdown and equity formula are readable and unambiguous.
- Updated [test_all.py](/Users/will/.cline/worktrees/ed369/polymarket-scanner/test_all.py) to validate the persisted paper-account lifecycle instead of process-local state.

## Verification
- Pending local verification via `python3 -m py_compile`, `node --check dashboard.html`, and `python3 test_all.py`.
