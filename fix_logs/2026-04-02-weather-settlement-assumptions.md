# 2026-04-02 Weather Settlement Assumptions

## Context
- Operator request dated 2026-04-02: audit and document weather-market settlement assumptions before changing strategy logic.
- AGENTS.md requires dated `fix_logs/` entries for behavior-change work and pre-change audits. This pass is documentation and recommendation only; no weather strategy logic was changed.

## Audit Summary
- The current repo weather edge in [weather_scanner.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/weather_scanner.py) is a binary threshold forecast model built around NOAA hourly highs plus Open-Meteo daily highs.
- It does not currently encode explicit settlement-source metadata such as station id, source URL, timezone, precision, or source-finalization rules.
- The proposed Shanghai / ZSPD / Wunderground-style markets are materially different:
  - settlement is station-specific
  - units and precision are part of the rules
  - finalization timing matters
  - some markets are exact-temperature ladders rather than binary thresholds
- Confirmed by live Polymarket market rules:
  - Shanghai has used Wunderground station `ZSPD` with whole-degree Celsius settlement
  - NYC has used Wunderground station `KLGA` with whole-degree Fahrenheit settlement
  - Hong Kong has used Hong Kong Observatory daily extracts with one-decimal Celsius settlement
  - Seoul has used Wunderground station `RKSI`, showing that settlement stations may not match city-center coordinates

## Recommendation
- Keep the current weather scanner intact as the existing binary threshold strategy.
- Implement Shanghai / station-settled exact-temperature logic as a separate weather sub-strategy.
- Add a small shared settlement-spec abstraction for reusable metadata and provider plumbing, but do not merge the exact-temp parser and settlement model into the current `weather_scanner.scan()` path.
- Keep the rollout paper-safe:
  - save and inspect signals first
  - validate against finalized settlement prints
  - delay any automated trading changes until the new path is independently verified

## Documentation
- Added [reports/diagnostics/2026-04-02-weather-settlement-assumptions-review.md](/Users/will/.cline/worktrees/7997d/polymarket-scanner/reports/diagnostics/2026-04-02-weather-settlement-assumptions-review.md) with the detailed comparison, reusable-vs-specific breakdown, and implementation recommendation.
