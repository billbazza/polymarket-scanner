# 2026-04-02 Weather Intraday Correction

## Context
- Repo task dated 2026-04-02: add an intraday weather-correction layer for the existing threshold weather strategy, using morning observed temperatures to adjust forecast confidence over the day.
- Per [AGENTS.md](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/AGENTS.md), the change had to stay paper-safe, preserve graceful fallback behavior, compare baseline versus corrected outputs instead of blindly replacing the existing model, and log the behavior change in `fix_logs/`.

## Changes
- Added [weather_correction.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/weather_correction.py) with:
  - shared baseline sigma/probability helpers for threshold weather markets
  - normalization for optional same-day observation payloads
  - conservative source-level intraday correction using forecast high/low, morning observation, and optional warming/cooling trend
  - compare-only selection logic by default (`correction_mode='shadow'`)
  - a labeled-sample evaluation helper for baseline-versus-corrected backtests
- Added [weather_backtest.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/weather_backtest.py) so the intraday correction sample set can be re-run from the repo.
- Updated [weather_sources.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/weather_sources.py) so threshold source results now expose `low_f` alongside the existing target-day high, which the correction layer uses to estimate intraday warmup progress.
- Updated [weather_scanner.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/weather_scanner.py) to:
  - keep the baseline combined probability and trade gate intact
  - compute corrected and selected probabilities in parallel
  - surface correction observability (`correction_status`, `correction_reason`, `correction_confidence`, compare-only mode, corrected vs selected probabilities)
  - default to `shadow` mode so paper/live behavior does not silently change
- Updated [server.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/server.py) so `/api/scan/weather` accepts:
  - `correction_mode=shadow|blend|corrected`
  - optional `intraday_observations_json`
- Updated [db.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/db.py) with nullable weather-signal observability fields:
  - `selected_prob`
  - `selected_edge`
  - `selected_edge_pct`
  - `correction_mode`
  - `correction_json`
  Existing baseline weather fields remain unchanged.

## Backtest
- Added labeled intraday samples in [tests/fixtures/weather_intraday_backtest.json](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/tests/fixtures/weather_intraday_backtest.json).
- Ran `python3 weather_backtest.py` on 2026-04-02 with the repo-local fixture set.
- Result summary:
  - samples: `8`
  - baseline Brier: `0.5412`
  - corrected Brier: `0.1422`
  - baseline log loss: `1.3744`
  - corrected log loss: `0.3998`
  - baseline realized edge on thresholded trades: `-0.5050`
  - corrected realized edge on thresholded trades: `+0.3643`
- Interpretation:
  - the correction improved bucket/outcome calibration on the curated sample set
  - the default rollout still remains compare-only, because the sample set is local and intentionally limited rather than a full production historical archive

## Tests
- Added [tests/test_weather_correction.py](/Users/will/.cline/worktrees/b4c21/polymarket-scanner/tests/test_weather_correction.py) covering:
  - successful same-day correction when observations are valid
  - fallback to baseline when the observation date does not match the target date
  - scanner integration that exposes corrected outputs while keeping shadow mode baseline-selected by default
  - backtest regression that asserts corrected performance beats baseline on the fixture set
- Re-ran:
  - `python3 -m unittest tests.test_weather_correction tests.test_weather_sources tests.test_weather_signal_lifecycle`
  - `python3 weather_backtest.py`
