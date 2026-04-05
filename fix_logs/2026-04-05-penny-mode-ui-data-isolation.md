# 2026-04-05 Penny-Mode UI/Data Isolation

## Source
- Operator bug report dated 2026-04-05: switching the dashboard from paper to penny still showed paper positions, paper P&L, and shared totals.
- Followed [AGENTS.md](/Users/will/.cline/worktrees/0f34c/polymarket-scanner/AGENTS.md): treat as a correctness issue, keep audit logging intact, and log the bug-fix work in `fix_logs/`.

## Findings
- The backend already scoped several core reads by `runtime_scope`, but the dashboard still fetched closed-trade history without `runtime_scope`, so the History tab could show mixed paper+penny rows after a mode switch.
- The dashboard also had no dedicated scoped autonomy-runtime payload for the selected mode, so the penny view could not show its own `level`, `max_open`, or runtime-state file explicitly.
- Browser-side mode switches were vulnerable to stale async responses: a slower paper fetch could finish after the operator switched to penny and repaint the page with paper-scoped metrics.
- The account contract itself still used `paper_account` semantics for penny scope, so the browser was repainting a paper-shaped object with penny copy instead of showing a wallet-backed live account.
- Visible dashboard copy still exposed paper terminology in penny mode (`Paper View`, `Paper Gate`, paper bankroll labels), which made the penny lane look like a relabeled research dashboard instead of a separate live scope.

## Fixes Applied
- Added `GET /api/autonomy/runtime` in [server.py](/Users/will/.cline/worktrees/0f34c/polymarket-scanner/server.py) so the dashboard now receives the selected scope’s:
  - persisted autonomy state
  - active level config
  - `max_open` / human-readable cap label
  - scoped state-file path
  - scoped run status / last result
- Updated [dashboard.html](/Users/will/.cline/worktrees/0f34c/polymarket-scanner/dashboard.html) so scope-dependent fetches capture the requested scope and discard stale responses if the operator switches modes before the response returns.
- Updated the History-tab fetch in [dashboard.html](/Users/will/.cline/worktrees/0f34c/polymarket-scanner/dashboard.html) to call `/api/trades?status=closed&runtime_scope=<selected>`, matching the already-scoped open-trades path.
- Added a scoped-runtime panel in [dashboard.html](/Users/will/.cline/worktrees/0f34c/polymarket-scanner/dashboard.html) so penny view now shows penny-only runtime state, penny `max_open`, and the penny state file instead of implying one shared lane.
- Kept existing paper-trade attempt logging and journal/audit paths unchanged; this fix only corrected which scoped data the dashboard reads and renders.
- Added `db.get_live_account_overview()` plus `db.get_runtime_account_overview()` and switched [server.py](/Users/will/.cline/worktrees/69917/polymarket-scanner/server.py) `/api/stats` + new `GET /api/runtime/account` to return a mode-aware `runtime_account` object instead of reusing `paper_account` in penny scope.
- Penny account payloads now report:
  - Polygon wallet cash balance
  - wallet address / wallet-error state
  - penny-scoped deployed capital
  - penny-scoped realized/unrealized PnL
  - penny-scoped open positions and wallet-exposure percentage
- Updated [dashboard.html](/Users/will/.cline/worktrees/69917/polymarket-scanner/dashboard.html) to fetch `/api/runtime/account` directly, render wallet-backed penny metrics, hide research-only sizing controls and gate tab in penny mode, and swap visible UI labels from research/bankroll wording to live wallet wording when penny is active.
- Updated [AGENTS.md](/Users/will/.cline/worktrees/69917/polymarket-scanner/AGENTS.md), [CLAUDE.md](/Users/will/.cline/worktrees/69917/polymarket-scanner/CLAUDE.md), and [GEMINI.md](/Users/will/.cline/worktrees/69917/polymarket-scanner/GEMINI.md) so the repo contract now states that `/api/runtime/account` is canonical and that penny mode must never render paper or shared-total metrics.

## Acceptance Checks
- UI switch correctness:
  - Start in Paper view with at least one paper trade and one penny trade present.
  - Click `Penny`.
  - Confirm Open Trades shows only penny-scoped rows.
  - Confirm Trade History shows only penny-scoped closed rows.
  - Confirm the hero/account metrics show penny-only `available_cash`, `committed_capital`, `realized_pnl`, `unrealized_pnl`, and `open_trades`.
  - Confirm the Scoped Runtime panel shows the penny level, penny `max_open`, and `autonomy_state.penny.json`.
  - Click back to `Paper` and confirm the same panels revert to paper-only values and `autonomy_state.paper.json`.
- Penny live semantics:
  - `GET /api/runtime/account?runtime_scope=penny` must return `account_mode=live_wallet` and must not expose a `paper_account` wrapper.
  - The dashboard in penny mode must show wallet balance, wallet-backed equity, deployed capital, penny position count, and wallet exposure.
  - No visible penny-mode labels should mention paper balances, paper PnL, paper gate, or paper bankroll usage.
- Backend separation:
  - `GET /api/trades?status=open&runtime_scope=paper` and `runtime_scope=penny` must return disjoint trade sets.
  - `GET /api/trades?status=closed&runtime_scope=paper` and `runtime_scope=penny` must return disjoint history sets.
  - `GET /api/stats?runtime_scope=paper|penny` must return the corresponding scoped account totals in `runtime_account` rather than shared aggregates.
  - `GET /api/autonomy/runtime?runtime_scope=paper|penny` must return the selected scope’s persisted state and level config, including the correct `max_open`.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m unittest tests.test_strategy_performance`
- `python3 -m py_compile server.py db.py autonomy.py`
