# 2026-04-05 Core Penny Live-Ledger Separation

## Source
- Operator request dated 2026-04-05: restore penny mode as a separate live-money ledger/book backed by the Polygon wallet rather than a relabeled paper runtime.
- Followed `AGENTS.md` Trading Modes and Always Do rules: keep audit logging intact, record the fix in `fix_logs/`, and sync repo guidance when runtime semantics change.

## Findings
- Penny reporting still treated `runtime_scope="penny"` as sufficient, so a mis-stamped `paper_research` trade could leak into penny positions, PnL, history, and max-open accounting.
- `get_stats()`, `get_trades()`, `count_open_trades()`, and the runtime/account reconciliation helpers each had their own scoped-trade logic, which made it possible for penny surfaces to diverge from the true live ledger.
- Duplicate-open checks for penny-scoped trades also trusted scope alone, so a stray paper-state row inside the penny scope could block real live openings.

## Fixes Applied
- Added a canonical runtime-ledger filter in `db.py`:
  - paper scope admits only `paper_research` trades
  - penny scope admits only `wallet_attached` / `live_exchange` trades
  - non-ledger rows are counted as excluded diagnostics instead of contributing to balances or max-open usage
- Updated penny-facing helpers in `db.py` to use that ledger filter consistently:
  - `get_trades()`
  - `get_strategy_performance()`
  - `get_paper_account_state()` as the scoped trade accumulator feeding live overview math
  - `get_runtime_scope_trade_reconciliation()`
  - `count_open_trades()`
  - penny duplicate-open checks for pairs / whale / weather trade preflights
- Updated `get_stats()` to derive penny trade totals from the exact scoped ledger reconciliation instead of separate ad hoc SQL totals.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` so the runtime contract now states explicitly that penny surfaces must exclude `paper_research` rows even if they were accidentally stamped with `runtime_scope="penny"`.

## Acceptance Checks
- `GET /api/trades?status=open&runtime_scope=penny` must exclude penny-scoped `paper_research` rows.
- `GET /api/stats?runtime_scope=penny` must report counts/PnL only from wallet/live penny trades and return reconciliation diagnostics for excluded non-ledger rows.
- `GET /api/runtime/account?runtime_scope=penny` must keep wallet cash separate and use only wallet/live penny trades for deployed capital, open positions, and PnL.
- `GET /api/autonomy/runtime?runtime_scope=penny` must count only wallet/live penny trades toward `open_positions`, `max_open_usage`, and `slots_remaining`.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m unittest tests.test_strategy_performance`
- `python3 -m py_compile db.py server.py autonomy.py execution.py`
