# 2026-04-05 Penny Weather History Isolation

## Context
- Penny weather dedupe/reopen/probation decisions must follow the repo's `Trading Modes` scoped-history rule: only weather trades from the active `runtime_scope` may block a weather open, and cointegration history must never bleed into that decision.
- Operator-facing skip logs also need to identify the active lane and the blocking source explicitly.

## Changes
- Centralized weather-history lookups in [db.py](/Users/will/.cline/worktrees/ecb9c/polymarket-scanner/db.py) behind a helper that always filters to `trade_type='weather'` plus the active `runtime_scope` before dedupe/reopen/probation decisions are made.
- Kept the decision audit payload explicit with `runtime_scope`, `decision_source`, `history_runtime_scope`, `history_strategy`, and `history_source`, so penny weather blocks resolve to `penny-weather` instead of paper or cointegration sources.
- Updated the weather skip/block log lines in [db.py](/Users/will/.cline/worktrees/ecb9c/polymarket-scanner/db.py), [autonomy.py](/Users/will/.cline/worktrees/ecb9c/polymarket-scanner/autonomy.py), and [server.py](/Users/will/.cline/worktrees/ecb9c/polymarket-scanner/server.py) to print `runtime_scope`, `decision_source`, and `history_source` directly in the operator-visible message.
- Added a regression test that combines paper weather history, penny cointegration history, and penny weather history on the same token and verifies penny weather reopen blocking still attributes only `penny-weather`.

## Verification
- `python3 -m unittest tests.test_weather_signal_lifecycle tests.test_runtime_scope_split`
