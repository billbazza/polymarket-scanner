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

## 2026-04-02 Follow-on Update
- Tightened the reporting scope in [db.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/db.py) so paper bankroll/account utilization now uses only `trade_state_mode=paper_research`.
  - `wallet_attached` copy rows and `live_exchange` rows remain visible in strategy attribution, but they are excluded from paper cash, committed capital, and utilization.
  - Strategy rows now expose `paper_open_trades`, `external_open_trades`, wallet/live open counts, `net_pnl`, and a `data_quality_status`.
- Added explicit data-quality reporting in [db.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/db.py):
  - inferred or missing trade-state rows
  - open trades missing usable marks
  - external open trades excluded from paper-utilization math
- Updated insert paths in [db.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/db.py) so weather, whale, and copy trades persist explicit `strategy_name` / trade-state metadata instead of relying on reporting-time inference.
- Fixed copy-position identity matching in [db.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/db.py) so YES and NO positions on the same watched-wallet condition remain distinct for attribution and reconciliation.
- Updated [analysis.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/analysis.py) to print the same strategy-level attribution/data-quality audit from the CLI report.
- Updated [dashboard.html](/Users/will/.cline/worktrees/60034/polymarket-scanner/dashboard.html) so operators can see:
  - net P&L by strategy
  - paper-only capital in use
  - paper-vs-external open trade mix
  - coverage warnings when attribution is degraded

## Data-Quality Gaps Surfaced
- Clean paper utilization still depends on open paper trades having valid marks in `snapshots`; missing marks are now reported instead of silently disappearing.
- Older/manual rows with missing or invalid `trade_state_mode` are still inferred best-effort for reporting, and that inference is now counted as a gap.
- Strategy P&L can span paper plus external rows; paper bankroll sizing should use the paper-account scope and `paper_*` utilization fields rather than raw cross-state totals.

## Tests Added
- Added [tests/test_strategy_performance.py](/Users/will/.cline/worktrees/9ef75/polymarket-scanner/tests/test_strategy_performance.py):
  - verifies mixed-strategy aggregation across cointegration, weather, whale, and copy trades
  - verifies `/api/stats` and `/api/paper-account` expose the strategy breakdown expected by the dashboard
- Extended [tests/test_strategy_performance.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/tests/test_strategy_performance.py) to verify wallet-attached copy trades stay visible in strategy attribution while remaining excluded from paper utilization/account totals.
- Kept [tests/test_trade_state_architecture.py](/Users/will/.cline/worktrees/60034/polymarket-scanner/tests/test_trade_state_architecture.py) green after tightening copy-position identity matching.

## Verification
- `python3 -m unittest tests.test_strategy_performance`
- `python3 -m py_compile db.py server.py`
- `python3 -m py_compile db.py server.py analysis.py test_all.py tests/test_strategy_performance.py tests/test_trade_state_architecture.py`
- `python3 -m unittest tests.test_strategy_performance tests.test_trade_state_architecture`
