# A-Grade Cointegration Paper Trial

Date: 2026-04-01

## Summary
- Added an explicit paper-only A-grade cointegration trial path.
- Kept live-trading behavior unchanged: only the paper autonomy path can auto-admit eligible A-grade cointegration trades.
- Instrumented signals and trades so operators can compare A-trial vs A+ cohorts through the DB and API.

## Behavior Change
- `autonomy.py` now annotates every cointegration opportunity with a trial admission decision before saving it.
- A+ signals remain the control cohort and continue to open normally.
- A-grade signals can only open through `admission_path=paper_a_trial` when all of these hold:
  - paper mode
  - trial enabled
  - single allowed miss is `ev_pass`
  - tighter `|z|`, half-life, liquidity, and slippage guardrails pass
- Trial trades use smaller size and store explicit stop/reversion/max-hold guardrails on the trade row.

## Instrumentation
- Signals now persist:
  - `paper_tradeable`
  - `filters_json`
  - `admission_path`
  - `experiment_*` metadata
- Cointegration trades now persist:
  - cohort/admission metadata
  - entry risk context
  - max unrealized profit
  - max unrealized drawdown
  - regime-break threshold/flag/notes
  - explicit exit reason and close z-score
- `tracker.py` updates drawdown/profit and flags regime-break behavior while open.
- `db.get_cointegration_trial_summary()` aggregates:
  - trade counts
  - realized and unrealized P&L
  - win rate
  - average hold time
  - MAE/MFE proxies
  - rejection reasons
  - regime-break counts/rates

## Operator Surface
- Added `GET /api/cointegration/trial`.
- Added trial summary into `GET /api/stats`.
- Autonomy logs now print:
  - A-trial candidate/eligible/rejected counts
  - grouped rejection reasons

## Safety Notes
- No live-trading path was widened.
- `execution.py` still preserves existing live balance/HMRC/slippage behavior.
- Trial stop/max-hold logic only applies to paper cointegration trades that carry explicit trial guardrails.

## Verification
- `python3 -m unittest tests.test_cointegration_trial`
- `python3 -m py_compile cointegration_trial.py autonomy.py execution.py tracker.py server.py cron_scan.py db.py tests/test_cointegration_trial.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`

## Follow-Up
- Historical rows are not backfilled with the new cohort metadata, so old trades/signals are visible as legacy history rather than clean A-trial vs A+ comparisons.
- Promotion decisions should wait for fresh post-rollout paper samples.
