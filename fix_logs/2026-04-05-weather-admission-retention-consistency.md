# 2026-04-05 Weather Admission Retention Consistency

- Problem: threshold-weather signals could be saved with `tradeable=1`, later fail the shared admission/preflight path, and still remain retained as scan-tradeable in SQLite without proof of a material state change.
- Root cause: the scan snapshot and the effective persisted admission state were conflated, while `inspect_weather_trade_open()` only warned on some inconsistencies instead of correcting the stored row.
- Fix:
  - Persist the original scan admission snapshot separately as `source_meta.scan_threshold_admission`.
  - Treat `source_meta.threshold_admission` plus the `weather_signals.tradeable` column as the current effective retained state.
  - Re-evaluate persisted threshold-weather rows through one shared helper on DB reads and preflight.
  - Demote stale retained `tradeable=1` weather rows when shared admission now blocks them, preserving explicit blocker/state-change evidence in `source_meta.tradeable_retention_correction`.
  - Surface scan blocker set, current blocker set, and state-change evidence in paper/penny weather preflight logs.
- Result: paper and penny now use the same corrected retained weather admission state, and a later blocker without material-change proof no longer leaves the signal labeled as tradeable.
