# 2026-04-05 Weather Stop-Loss Investigation

## Context
- Daily-report follow-up dated 2026-04-05 asked for a focused investigation into the recurring weather stop-losses, especially the frequent `-$4` to `-$5` exits, without changing any entry thresholds.
- `AGENTS.md` requires a dated fix log for behavior changes and an updated daily-report trail.

## Findings
- The live code had drifted from the previously supported weather stop policy: `tracker.py` was using an `18%` stop even though the earlier review and risk-controls notes had already converged on `15%` as the supported setting.
- The current loss cluster is broader than the original `-$4/-$5` pattern. Recent stop-outs recorded in `scanner.db`, `logs/scanner.log`, and `logs/journal.jsonl` include:
  - trade `433` Chicago 2026-04-04: `$-11.71`
  - trade `431` San Francisco 2026-04-04: `$-8.45`
  - trade `448` Denver 2026-04-09: `$-8.64`
- Recent stopped weather trades still skew toward shorter horizons:
  - `0-24h`: `4` stops, `-$30.36` total, `-$7.59` average
  - `25-48h`: `3` stops, `-$13.04` total, `-$4.35` average
  - `49-72h`: `2` stops, `-$9.53` total, `-$4.77` average
  - `73h+`: `1` stop, `-$8.64` total, `-$8.64` average
- Repeat city/date pain still shows up in the closed sample even with token-level reopen protection:
  - Atlanta 2026-04-03: `2` stop-outs, `-$10.34`
  - Denver 2026-04-04: `2` stop-outs, `-$8.42`
- The new stop telemetry was only partially reliable. The structured `weather_stop_loss` journal entries were present, but the standalone `reports/diagnostics/weather-stop-contexts.jsonl` file was missing in this worktree because the path was not tied to the active DB/report environment.

## Changes
- Restored the supported weather stop-loss policy in `tracker.py` from `18%` back to `15%`.
- Expanded the weather stop context payload to include `city`, `target_date`, `strategy_name`, `sources_agree`, `entry_age_hours`, `hold_hours`, and an explicit `trigger_type` / `gap_through_stop` classification.
- Routed the stop-context JSONL sink through the active DB/report environment so production runs and isolated tests write to the correct diagnostics directory.
- Kept all weather entry thresholds unchanged.

## Tests
- `python3 -m pytest tests/test_weather_signal_lifecycle.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
