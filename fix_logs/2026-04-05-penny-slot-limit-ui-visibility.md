# 2026-04-05 Penny Slot-Limit UI Visibility

## Issue
- Operator request dated 2026-04-05: when penny openings were blocked by `max_open`, the dashboard still required operators to infer the cause from raw log lines like `trade_status=limited_by_slots`.
- [AGENTS.md](/Users/will/.cline/worktrees/7fe39/polymarket-scanner/AGENTS.md) requires penny slot accounting to stay isolated to the live ledger and to remain operator-visible with clear audit trails.

## Changes
- Added a first-class `slot_limit_state` object to `GET /api/autonomy/runtime` so the dashboard receives the active penny max-open setting, current penny open-position count, remaining slots, blocking trades, and strategy-specific slot-pressure status.
- Extended `autonomy.py` cycle summaries to record a `pairs_phase` alongside the existing weather phase, including when cointegration is blocked or limited by penny slot capacity.
- Updated the dashboard scoped-runtime panel to show:
  - active penny `max_open`
  - current penny open-trade count
  - remaining available slots
  - a dedicated slot-limit state card
  - a dedicated blocked-reason line that says either why new penny trades are blocked or that no slot blocker is active
  - strategy-specific cointegration/weather slot-pressure text
  - the open penny trades currently consuming slot capacity

## Safeguards
- The slot-limit UI remains penny-only; paper stays uncapped and cash-limited.
- Slot counts still come exclusively from the penny live ledger, so paper rows cannot consume penny capacity even if mislabeled.
- Cointegration and weather continue to share one penny live slot budget, but the UI now states that directly and explains when both strategies are blocked by a full live book.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m py_compile server.py autonomy.py`
