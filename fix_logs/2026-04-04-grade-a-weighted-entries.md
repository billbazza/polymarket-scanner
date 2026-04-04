# 2026-04-04 Grade A Weighted Entries

## Context
- Grade-A signals were previously trapped behind the strong math filters that only let the A+ cohort trade; the trial logged their failures but never let the data keep flowing.
- We wanted a safe, higher-risk lane to sample those near-misses so we can study their real-world behavior without bloating the live wallet.

## Changes
- Relaxed the trial thresholds (min_z_abs → 1.45, min_liquidity → $6k, max_slippage_pct → 2.0%, min_ev_pct → 0.25%) so richer guardrail metadata can be logged while admitting more opportunities.
- Added grade-weighted sizing so every admitted A-grade entry now uses 25–65% of the baseline size depending on how close the grade is to A+, and the guardrails now publish the `grade_weight` plus the final `weighted_entry_size_usd` alongside the stop/hold thresholds.
- `experiment_grade_weight` is persisted with the signal so downstream sizing, journal, and trade logs know why the legal entry is smaller, and existing A-grade signals now start populating the trial bucket with these weighted, higher-risk fills.

## Verification
- `python3 -m py_compile cointegration_trial.py autonomy.py`
- `python3 -m unittest tests.test_cointegration_trial`
