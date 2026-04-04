# 2026-04-05 Weather Closed-Token UI Sync

## Context
- Operator reports showed the Weather tab and paper-trade attempts log still highlighting `token_already_open` for signals whose linked trades had already closed, so Atlanta trades (signal 1210 / trade #425) stayed stale and new candidates were silently blocked.
- The closed-token guard in `db.inspect_weather_trade_open()` already returned the closed trade id/reason, but the signal API dropped that trade id when the block reason was `token_already_closed` / `token_probation_blocked`, so the UI had no way to show the same trade that actually resolved the token.

## Changes
- Expanded `db.get_weather_signals()` so the API now exposes `blocked_by_trade_id` for every inspect reason that references an existing weather trade, including closed-token and probation blocks, not just concurrent opens.
- Added a regression test that confirms a child signal blocked by a closed Atlanta trade now surfaces `blocked_by_trade_id` so the dashboard/journal can point back to trade #425 instead of pretending the token is still open.

## Tests
- `python3 -m pytest tests/test_weather_signal_lifecycle.py`
