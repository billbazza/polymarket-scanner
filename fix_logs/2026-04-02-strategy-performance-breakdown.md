# 2026-04-02 Strategy Performance Breakdown

## Source
- Daily-report follow-up dated 2026-04-02: break out weather, whale, copy, and cointegration contributions instead of relying on aggregate paper-account metrics.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/AGENTS.md), especially:
  - `Architecture` for keeping reporting centered on `db.py` and `server.py`
  - `Always Do` for logging behavior changes in `fix_logs/`
  - `Database` for preserving auto-migrating SQLite/reporting patterns

## Changes Applied
- Added shared strategy-level performance aggregation in [db.py](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/db.py).
  - Breaks out `cointegration`, `weather`, `whale`, and `copy`
  - Reports realized P&L, unrealized P&L, open/closed trade counts, wins/losses, win rate, committed capital, deployed capital, average size, and bankroll utilization
  - Uses the existing mark-to-market math so strategy views stay consistent with paper-account totals
- Added [db.get_paper_account_overview()](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/db.py) so API consumers can fetch paper-account totals and strategy contribution metrics together without duplicate refresh work.
- Extended [server.py](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/server.py) `GET /api/paper-account` to return the strategy breakdown alongside the existing paper-account fields.
- Extended [db.get_stats()](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/db.py) so `GET /api/stats` now includes `strategy_breakdown`, allowing daily-report context and the dashboard to consume the same canonical metrics.
- Updated [dashboard.html](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/dashboard.html) with a concise operator-facing strategy table showing:
  - realized contribution
  - open mark-to-market
  - win rate with W/L context
  - total/open/closed trade counts
  - capital currently in use with bankroll utilization

## Tests Added
- Added [tests/test_strategy_performance.py](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/tests/test_strategy_performance.py):
  - verifies mixed-strategy aggregation across cointegration, weather, whale, and copy trades
  - verifies `/api/stats` and `/api/paper-account` expose the strategy breakdown expected by the dashboard

## Verification
- `python3 -m unittest tests.test_strategy_performance`
- `python3 -m py_compile db.py server.py`
