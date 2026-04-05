# 2026-04-05 Penny-Mode UI/Data Isolation

## Source
- Operator bug report dated 2026-04-05: switching the dashboard from paper to penny still showed paper positions, paper P&L, and shared totals.
- Followed [AGENTS.md](/Users/will/.cline/worktrees/0f34c/polymarket-scanner/AGENTS.md): treat as a correctness issue, keep audit logging intact, and log the bug-fix work in `fix_logs/`.

## Findings
- The backend already scoped several core reads by `runtime_scope`, but the dashboard still fetched closed-trade history without `runtime_scope`, so the History tab could show mixed paper+penny rows after a mode switch.
- The dashboard also had no dedicated scoped autonomy-runtime payload for the selected mode, so the penny view could not show its own `level`, `max_open`, or runtime-state file explicitly.
- Browser-side mode switches were vulnerable to stale async responses: a slower paper fetch could finish after the operator switched to penny and repaint the page with paper-scoped metrics.

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

## Acceptance Checks
- UI switch correctness:
  - Start in Paper view with at least one paper trade and one penny trade present.
  - Click `Penny`.
  - Confirm Open Trades shows only penny-scoped rows.
  - Confirm Trade History shows only penny-scoped closed rows.
  - Confirm the hero/account metrics show penny-only `available_cash`, `committed_capital`, `realized_pnl`, `unrealized_pnl`, and `open_trades`.
  - Confirm the Scoped Runtime panel shows the penny level, penny `max_open`, and `autonomy_state.penny.json`.
  - Click back to `Paper` and confirm the same panels revert to paper-only values and `autonomy_state.paper.json`.
- Backend separation:
  - `GET /api/trades?status=open&runtime_scope=paper` and `runtime_scope=penny` must return disjoint trade sets.
  - `GET /api/trades?status=closed&runtime_scope=paper` and `runtime_scope=penny` must return disjoint history sets.
  - `GET /api/stats?runtime_scope=paper|penny` must return the corresponding scoped account totals rather than shared aggregates.
  - `GET /api/autonomy/runtime?runtime_scope=paper|penny` must return the selected scope’s persisted state and level config, including the correct `max_open`.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m py_compile server.py db.py autonomy.py`
