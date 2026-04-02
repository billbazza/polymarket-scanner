# 2026-04-02 Weather Risk Controls

## Context
- Daily-report follow-up dated 2026-04-02 requested a weather risk-control review before tightening the stop-loss.
- AGENTS.md requires validating weather trade opens, closes, and P&L attribution before changing policy and logging the outcome in `fix_logs/`.

## Audit Summary
- Verified against `scanner.db`, `logs/scanner.log`, and `logs/journal.jsonl` that recent weather losses were real and correctly attributed.
- Sampled stop-loss exits (`412`, `414`, `416`, `419`, `425`) breached their floors and then closed within roughly `15-18` seconds, so delayed close behavior was not the main driver.
- The drawdown cluster was amplified by same-token re-entry after a prior weather exit:
  - Denver April 4 reopened as trade `418` after trade `412` stopped out
  - Atlanta April 3 reopened as trade `424` after trade `416` stopped out
  - Los Angeles April 4 reopened as trade `423` after three prior closed losses on the same token
- Counterfactual on the recorded closed weather sample favored `15%` over the existing `20%` stop and avoided the larger winner attrition seen at `10%`.

## Changes
- Updated [tracker.py](/Users/will/.cline/worktrees/93e9a/polymarket-scanner/tracker.py) to tighten `WEATHER_STOP_LOSS_PCT` from `0.20` to `0.15`.
- Updated [db.py](/Users/will/.cline/worktrees/93e9a/polymarket-scanner/db.py) so weather-trade preflight blocks reopening an outcome token that already has a closed weather trade, returning `reason_code=token_already_closed`.
- Added [reports/diagnostics/2026-04-02-weather-risk-review.md](/Users/will/.cline/worktrees/93e9a/polymarket-scanner/reports/diagnostics/2026-04-02-weather-risk-review.md) with the dated review note and recommendation.

## Tests
- Added regression coverage in [tests/test_weather_signal_lifecycle.py](/Users/will/.cline/worktrees/93e9a/polymarket-scanner/tests/test_weather_signal_lifecycle.py):
  - duplicate weather rows cannot reopen a token after a prior closed trade
  - weather stop-loss auto-close now fires at the `15%` floor, not `20%`
