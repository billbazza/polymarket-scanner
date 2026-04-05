# 2026-04-05 Penny Live Slot Accounting

## Issue
- Penny weather execution was reaching the live-trade path, but the weather phase could still end with `trade_status=limited_by_slots` and `traded=0` because slot accounting visibility was too loose.
- `AGENTS.md` requires penny runtime/accounting views to reconcile exactly to the penny live ledger and to stay isolated from paper research positions.

## Changes
- Added `db.get_runtime_slot_usage()` as the canonical penny slot snapshot. It only counts open trades returned by the penny live ledger filter, so stray `runtime_scope="penny"` paper rows do not consume live `max_open` capacity.
- Centralized slot consumers in `db._get_runtime_slot_consumers()` and switched `count_open_trades()` to the same source so cointegration/weather max-open blocking and the operator-visible slot dashboard cannot diverge.
- Updated `/api/autonomy/runtime` to return the same canonical slot snapshot used for operator status: current usage, available slots, and the exact open penny trades consuming capacity.
- Updated `/api/runtime/account` to return the scoped penny `max_open`, `max_open_usage`, `slots_remaining`, `slot_usage`, and `slot_limit_state`, so the live wallet runtime view exposes the same penny slot consumers and remaining-capacity data as the autonomy runtime view.
- Updated `autonomy.py` to log penny slot usage before pairs and weather admission, attach the starting/ending weather slot snapshot to the cycle summary, and include blocking trade details when weather is slot-limited.
- Updated the dashboard scoped-runtime panel so penny shows shared live-slot usage, available slots, and the open weather/cointegration trades consuming the current penny budget.

## Safeguards
- Weather and cointegration still intentionally share one penny live slot budget.
- Only penny live-ledger trades consume that budget; paper research rows remain isolated even if they carry the penny runtime scope by mistake.
- Slot-usage details are operator-visible and audit-friendly through both the runtime API and the autonomy/weather log lines.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
