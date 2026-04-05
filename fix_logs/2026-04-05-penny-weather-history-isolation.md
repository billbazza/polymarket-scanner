# 2026-04-05 Penny Weather History Isolation

- Scoped weather dedupe, closed-token reopen checks, and probation counters to the active `runtime_scope` so penny weather no longer inherits paper weather history.
- Restricted weather history lookups to `trade_type='weather'` so cointegration trades and positions cannot block weather re-entry even when token ids overlap.
- Added explicit decision/attempt audit metadata (`decision_source`, `history_source`, `attempt_source`) and scoped `/api/weather` plus dashboard weather fetches to the selected runtime lane so operators can see whether a block came from `penny-weather`, `paper-weather`, `penny-cointegration`, or `paper-cointegration`.
