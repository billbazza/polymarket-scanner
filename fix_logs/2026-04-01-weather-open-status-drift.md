# 2026-04-01 Weather Open Status Drift

## Context
- Operator report on 2026-04-01: the Weather tab repeatedly showed fresh Atlanta, Denver, Dallas, and Los Angeles rows as `open` across multiple scans, while the real open-trade list had far fewer entries and new candidates appeared not to open for hours.

## Findings
- The main drift was in [db.py](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/db.py): `get_weather_signals()` reused `inspect_weather_trade_open()` and promoted any returned `existing_trade_id` into `open_trade_id`.
- For duplicate weather rows, `inspect_weather_trade_open()` correctly returned `reason_code=token_already_open` with the older trade id that already owned the token.
- The API then exposed that blocking trade id as if it were an exact link for the current row, and [dashboard.html](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/dashboard.html) rendered `● open`.
- Live DB audit on 2026-04-01 showed the falling total open-trade count was real rather than hidden by the query layer:
  - `db.get_stats()` returned `16` open trades.
  - `db.get_trades(status="open", limit=None)` returned `13` weather trades and `3` whale trades.
  - Recent weather open attempts were being blocked by duplicate-token suppression, not silently opening then disappearing.
- Weather signal lifecycle was also incomplete:
  - opening a weather trade set `weather_signals.status='traded'`
  - closing the linked trade did not move the signal to a closed state
  - a previously closed weather signal could be reopened because preflight only blocked currently-open linked trades

## Changes
- Updated [db.py](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/db.py) so weather preflight now blocks reopening a signal that already completed as a closed trade with `reason_code=signal_already_closed`.
- Updated [db.py](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/db.py) so closing a weather trade also updates the linked weather signal to `status='closed'`.
- Reworked weather feed annotation in [db.py](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/db.py):
  - `open_trade_id` now means only an exact currently-open trade linked to that signal
  - `has_open_trade` is explicit
  - duplicate suppression is exposed separately as `blocked_by_trade_id`
  - latest linked trade status, close time, and exit reason are returned
  - row `status` is derived as `open`, `closed`, `blocked`, or the underlying stored value
  - `status_detail` carries the operator-facing reason
- Updated [dashboard.html](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/dashboard.html) so the Weather tab only renders `● open` from `has_open_trade/open_trade_id` and shows closed/blocking detail from the new API fields.

## Tests
- Added [tests/test_weather_signal_lifecycle.py](/Users/will/.cline/worktrees/6b6e8/polymarket-scanner/tests/test_weather_signal_lifecycle.py):
  - duplicate weather rows stay blocked and do not inherit an unrelated `open` badge
  - closing a weather trade updates the linked signal lifecycle and blocks reopen of the stale signal
