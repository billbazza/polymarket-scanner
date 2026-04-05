# 2026-04-05 Paper/Penny Runtime Isolation

## Source
- Operator request dated 2026-04-05 to run paper experimentation alongside penny trading without shared `max_open` blocking or mixed dashboard/account views.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/cac67/polymarket-scanner/AGENTS.md), including audit logging, fix-log requirements, and doc sync.

## Findings
- Autonomy persisted a single mutable runtime state file, so the dashboard and background runner only had one shared paper/penny status lane.
- Trade queries, duplicate-open checks, and `max_open` gating treated all open trades as one pool, so paper positions could block penny openings.
- The dashboard always rendered one mixed trade/account view, which made it hard to tell whether an operator was looking at paper experimentation or penny runtime health.

## Fixes Applied
- Added `runtime_scope` persistence in [db.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/db.py) for trades and paper-trade attempt logs, with migration logic that backfills live/wallet states into the `penny` scope and paper states into the `paper` scope.
- Scoped duplicate-open checks, trade counts, trade listings, account summaries, strategy summaries, and attempt feeds in [db.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/db.py) so paper and penny can hold the same signal independently and so paper trades no longer consume penny `max_open` capacity.
- Split autonomy runtime persistence in [autonomy.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/autonomy.py) into `logs/autonomy_state.paper.json` and `logs/autonomy_state.penny.json`, with legacy shared-file migration retained for backward compatibility.
- Scoped refresh/reconciliation/auto-close reads in [tracker.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/tracker.py) and [trade_monitor.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/trade_monitor.py) so a paper cycle only mutates paper-scoped lifecycle state and a penny cycle only mutates penny-scoped lifecycle state.
- Updated [execution.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/execution.py) so paper fills stamp the `paper` scope and live fills stamp the `penny` scope in the audit/accounting path.
- Extended [server.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/server.py) APIs to accept `runtime_scope`, split background autonomy status by scope, and return scoped trades/account summaries for the dashboard.
- Updated [dashboard.html](/Users/will/.cline/worktrees/cac67/polymarket-scanner/dashboard.html) with an explicit paper/penny switch, scoped API requests, and mode-specific copy so operators can jump between paper and penny views without losing track of the active runtime.
- Synced [AGENTS.md](/Users/will/.cline/worktrees/cac67/polymarket-scanner/AGENTS.md), [CLAUDE.md](/Users/will/.cline/worktrees/cac67/polymarket-scanner/CLAUDE.md), and [GEMINI.md](/Users/will/.cline/worktrees/cac67/polymarket-scanner/GEMINI.md) to reflect the per-scope runtime-state files and the new isolation rule.
- Added regression coverage in [tests/test_runtime_scope_split.py](/Users/will/.cline/worktrees/cac67/polymarket-scanner/tests/test_runtime_scope_split.py) for scoped duplicate checks, scoped stats/trades APIs, and split autonomy-state files.

## Verification
- `python3 -m py_compile autonomy.py db.py execution.py server.py tracker.py trade_monitor.py`
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m unittest tests.test_runtime_scope_split tests.test_strategy_performance`
