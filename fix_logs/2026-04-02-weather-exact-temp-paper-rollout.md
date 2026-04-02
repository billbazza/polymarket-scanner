# 2026-04-02 Weather Exact-Temp Paper Rollout

## Context
- Repo task dated 2026-04-02: integrate the approved station-settled exact-temperature weather logic as a paper-trading-first sub-strategy.
- AGENTS.md requires keeping the existing weather scanner intact, preserving structured return values, and logging dated behavior changes in `fix_logs/`.

## Changes
- Added `weather_settlement.py` as a read-only settlement-spec registry for station-settled weather markets.
- Added `weather_exact_temp_scanner.py` as a separate opt-in scanner for exact-temperature ladders.
  - It preserves settlement metadata such as `station_id`, `resolution_source`, `settlement_unit`, `settlement_precision`, and timezone.
  - It prices outcome bins from the shared forecast providers without changing the current threshold parser.
- Added `weather_strategy.py` so the default weather scan remains the threshold strategy and the exact-temp path is only included when explicitly enabled.
- Extended `weather_signals` in `db.py` with nullable metadata columns for the new sub-strategy and kept existing rows backward-compatible.
- Routed weather paper opens through `execution.execute_weather_trade()` so:
  - manual weather trade opens
  - autonomy weather paper opens
  - exact-temp paper opens
  all use the same structured preflight / execution path.
- Kept exact-temp rollout paper-safe:
  - `WEATHER_EXACT_TEMP_ENABLED=0` by default
  - `WEATHER_EXACT_TEMP_AUTOTRADE=0` by default
  - live execution remains blocked for weather exact-temp signals

## Verification
- Added `tests/test_weather_exact_temp.py` covering:
  - disabled-by-default exact-temp scanning
  - enabled exact-temp scan output and settlement metadata
  - combined weather scan opt-in behavior
  - paper-only execution for exact-temp weather trades
