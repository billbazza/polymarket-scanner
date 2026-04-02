# 2026-04-02 Cointegration Admission Filters And Diagnostics

## Source
- Daily-report follow-up dated 2026-04-02: cointegration produced 3,540 signals, including too many low-value candidates, and admission/rejection behavior needed to be made trustworthy before any downstream sizing changes.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/8010f/polymarket-scanner/AGENTS.md), especially Architecture, Scoring Pipeline, Database, Always Do, and Testing Changes.

## Problem
- The scanner admitted raw cointegration candidates using the active `z_threshold` and `p_threshold`, but `math_engine.score_opportunity()` still hardcoded `|z| >= 1.5` and `p < 0.10`. That let scan-time and score-time truth drift apart.
- Cointegration rows were persisted with grades and filter JSON, but there was no concise operator-facing blocker summary explaining whether a candidate was tradeable, a paper-trial near miss, or low-quality and rejected.
- The dashboard/API surfaced all saved rows the same way, so low-quality rejected candidates crowded out the operator-meaningful set.
- Near-resolution price-band rejection already existed in scoring but not in the scanner preflight, so the scanner still spent work and produced rows for pairs that could not pass the operational price filter.

## Fixes Applied
- Updated [math_engine.py](/Users/will/.cline/worktrees/8010f/polymarket-scanner/math_engine.py) so `score_opportunity()` now:
  - uses the active run thresholds for `z_pass` and `coint_pass`
  - returns structured `admission` diagnostics with failed filters, primary blocker code, human-readable reason, thresholds, and observed values
  - preserves safe default tradeability by keeping A+ as the only fully tradeable class
- Updated [scanner.py](/Users/will/.cline/worktrees/8010f/polymarket-scanner/scanner.py) and [async_scanner.py](/Users/will/.cline/worktrees/8010f/polymarket-scanner/async_scanner.py) so cointegration scans now:
  - skip pairs already outside the 5%-95% operating band before price-history work
  - pass the active scan thresholds into scoring instead of relying on math-layer defaults
  - report skip counts and admission/rejection summaries in scan stats and logs
- Updated [db.py](/Users/will/.cline/worktrees/8010f/polymarket-scanner/db.py) with signal admission persistence plus API-friendly observability fields:
  - migration `014_signal_admission_diagnostics`
  - saved `admission_json` for new rows
  - derived `failed_filters`, `monitorable_signal`, `admission_reason_code`, `admission_reason`, and `admission_summary`
  - `get_signals(..., include_rejected=False)` support so operator surfaces can default to meaningful rows without deleting historical rejects
- Updated [server.py](/Users/will/.cline/worktrees/8010f/polymarket-scanner/server.py) so `/api/signals` defaults to operator-meaningful rows and scan-job payloads now include admission and skip summaries.
- Updated [dashboard.html](/Users/will/.cline/worktrees/8010f/polymarket-scanner/dashboard.html) so the cointegration tab shows operator-meaningful signal counts and blocker text instead of a flat undifferentiated feed.
- Updated [guides/scoring.md](/Users/will/.cline/worktrees/8010f/polymarket-scanner/guides/scoring.md) to match the current 8-filter scoring/admission model and the new rejection diagnostics.
- Added focused regression coverage in [tests/test_cointegration_admission.py](/Users/will/.cline/worktrees/8010f/polymarket-scanner/tests/test_cointegration_admission.py) for:
  - score-time threshold alignment with the active scan run
  - hiding low-quality rejected rows while keeping operator-meaningful near misses visible
  - skipping pairs already outside the operating price band

## Verification
- `python3 -m py_compile math_engine.py scanner.py async_scanner.py db.py server.py tests/test_cointegration_admission.py tests/test_cointegration_trial.py`
- `python3 -m unittest tests.test_cointegration_admission tests.test_cointegration_trial`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
