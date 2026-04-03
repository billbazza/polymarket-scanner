# 2026-04-03 Whale Drawdown Cleanup

## Context
- Four whale trades with `event` labels like "Test whale drawdown event 1-4" were closing paths used for tooling checks but still appeared in the closed-trade history UI, confusing operators.

## Analysis & Changes
- Removed the orphaned trade rows (`438-441`) and their associated `snapshots` records from `scanner.db` so the UI no longer surfaces the test drawdown events.
- Confirmed there are no remaining rows in `trades` or `snapshots` for those trade IDs and that `event LIKE 'Test whale drawdown event%'` now returns zero results.

## Tests / Verification
- `sqlite3 scanner.db "SELECT COUNT(*) FROM trades WHERE event LIKE 'Test whale drawdown event%';"` → `0`
- `sqlite3 scanner.db "SELECT COUNT(*) FROM snapshots WHERE trade_id BETWEEN 438 AND 441;"` → `0`
