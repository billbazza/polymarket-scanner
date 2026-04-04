# 2026-04-04 Filter Failure Visibility

## Context
- Operators wanted better insight into A-grade failures so they could quickly tell whether a blocked signal was a near miss or a broad filter failure before pushing it live.
- The dashboard only showed the grade label, while the grade count and failed filters lived only in the journal/paper-trade attempt logs.

## Analysis & Changes
- Added a persisted `grade` column to `signals`, plus the migration/repair helpers, so every saved opportunity records the aggregate pass count alongside the existing label.
- Captured the grade value in `save_signal()` (with runtime sanitization) so future reads include the number of passed filters without recomputing the evaluation.
- Dashboard tooltips and blocker badges now surface the grade score, failed-filter count, and the specific filters that tripped a blocked A-grade signal so the UI can highlight near misses vs major failures.

## Tests
- `python3 -m py_compile db.py`
