# 2026-04-03 Whale History Cleanup

## Context
- A handful of manual whale trades including IDs 282, 347, 409, and 410 were still recorded in `scanner.db` and the audit logs, and they referenced synthetic “Test whale drawdown” events that should never have entered the shared history view.

## Analysis & Changes
- Deleted every row with `trade_type='whale'` from `trades` so those manual test trades no longer appear in the historical ledger, and verified there are zero remaining whale rows or `trade_type` references in the table.
- Trimmed the associated audit log noise by removing the `AUTO-CLOSE whale guardrail` lines and `Whale aggregate drawdown alert` warnings from `logs/scanner.log`, and removed the matching `trade_closed` journal entries (`trade_id` 347, 409, 410) so the JSONL audit trail no longer reflects the synthetic trades.

## Tests / Verification
- `sqlite3 scanner.db "SELECT COUNT(*) FROM trades WHERE trade_type='whale'"` → `0`
- `rg -n -i 'whale' logs/scanner.log` → exit `1` (no matches)
- `grep -n '"trade_type": "whale"' logs/journal.jsonl` → exit `1` (no matches)
