# 2026-04-02 Weather Risk Controls

## Context
- Daily-report follow-up dated 2026-04-02 requested a weather risk-control review before tightening the stop-loss.
- AGENTS.md requires validating weather trade opens, closes, and P&L attribution before changing policy and logging the outcome in `fix_logs/`.

## Audit Summary
- Verified against `scanner.db`, `logs/scanner.log`, and `logs/journal.jsonl` that recent weather losses were real and correctly attributed.
- Verified that sampled stop-loss exits (`412`, `414`, `416`, `419`, `425`) were closed against the prior `20%` stop, not the current `15%` stop. The recorded floor values in the exit notes match `20%` below entry for each trade.
- For the stop that was actually in force, breach-to-close latency was roughly `14.9-17.8` seconds, so delayed close behavior was not the main driver.
- Trade-state accounting is trustworthy for weather positions: single-leg realized P&L matches shares-based attribution from `size_usd`, `entry_price_a`, and `exit_price_a`.
- The drawdown cluster was amplified by same-token re-entry after a prior weather exit. Closed-token totals in the live DB show:
  - Los Angeles April 4 token: `3` closed losses, `$-18.27`
  - Dallas April 2 token: `2` closed losses, `$-9.27`
- Counterfactual replay on the recorded closed weather sample still favors `15%` over `20%` and avoids the extra winner attrition seen at `10%`.

## Changes
- Updated the dated review note in `reports/diagnostics/2026-04-02-weather-risk-review.md` to correct the evidence trail:
  - the recent drawdown sample used the old `20%` stop
  - `15%` remains the supported policy
  - the recommendation is to hold `15%`, not tighten further to `10%`
- Added regression coverage in `tests/test_weather_signal_lifecycle.py` for single-leg weather P&L attribution and close-state persistence.

## Tests
- Regression coverage in `tests/test_weather_signal_lifecycle.py` now covers:
  - duplicate weather rows cannot reopen a token after a prior closed trade
  - weather stop-loss auto-close fires at the `15%` floor, not `20%`
  - weather close P&L uses shares-based single-leg attribution and marks the signal closed
