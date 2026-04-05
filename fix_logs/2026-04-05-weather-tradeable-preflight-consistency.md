# 2026-04-05 Weather Tradeable / Preflight Consistency

## Source
- Operator request dated 2026-04-05: threshold-weather `tradeable` and autonomy preflight were drifting at the 48h horizon boundary, producing cases like `Signal horizon now 48.0h, below required 48h minimum.` and killing a freshly tradeable candidate with the same policy pass.
- Followed the repo contract in `AGENTS.md`:
  - `Trading Modes / Weather strategy parity`: paper and penny weather must share the same horizon, disagreement, liquidity, edge, and blocker logic before execution.
  - `Trading Modes / Pre-execution parity`: do not mark penny weather as ready/openable if the shared admission path will immediately veto it.
  - `Always Do`: log every trading decision and log bug-fix work under `fix_logs/`.

## Before
- `weather_scanner.py` computed threshold-weather `tradeable` with local guard checks.
- `db.inspect_weather_trade_open()` re-ran a separate horizon check later using elapsed wall-clock time and raw float comparison.
- That split let a signal be counted as `tradeable` in scan/autonomy and then fail immediate preflight with the same policy, especially around the 48h boundary and rounding display.
- When a post-scan block did happen, logs did not clearly prove whether it was a real state change or just admission drift.

## Changes Made
- Added `weather_admission.py` as the shared threshold-weather admission helper for:
  - horizon comparison precision
  - tradeable/blocker classification
  - blocker reason codes and messages
  - state-change proof metadata
- Updated `weather_scanner.py` to compute threshold-weather `tradeable` from that shared helper and persist the scan-time admission snapshot in `source_meta`.
- Updated `db.inspect_weather_trade_open()` so threshold-weather preflight uses the same shared helper and only blocks a stored `tradeable` signal when it can prove a material change, currently:
  - horizon decayed enough to cross the rounded boundary
  - guard thresholds changed after scan
- Fixed the 48h mismatch by comparing horizon values at the same `0.1h` precision used in logs, so a displayed `48.0h` no longer fails a `48.0h` minimum.
- When a stored `tradeable` signal is still blocked later, the decision now carries proof fields such as:
  - `material_state_change`
  - `state_change_reason_code`
  - `state_change_summary`
  - `stored_hours_ahead_cmp`
  - `remaining_hours_cmp`
- Updated autonomy and execution logging so weather preflight/live blocks explicitly show whether the block came from:
  - a proved state change
  - a named blocker in the shared admission path
  - a later live-only safeguard

## After
- Threshold-weather `tradeable` and immediate preflight now share one canonical admission path.
- A weather opportunity marked `tradeable` at scan time stays executable in both paper and penny unless:
  - the signal materially aged or policy thresholds changed after scan, or
  - a named external/live-only safeguard fails later in execution.
- Autonomy weather counts now remain aligned with the shared admission result instead of silently drifting at the handoff from scan to trade.

## Verification
- `python3 -m unittest tests.test_weather_signal_lifecycle tests.test_runtime_scope_split`
- `python3 -m py_compile weather_admission.py weather_scanner.py db.py autonomy.py execution.py`
