# 2026-04-02 Weather Provider Abstraction

## Context
- Repo task dated 2026-04-02: add a clean weather data-source abstraction so the current NOAA + Open-Meteo threshold workflow can expand to additional forecast and settlement providers without breaking existing scans.
- Per [AGENTS.md](/Users/will/.cline/worktrees/b74e9/polymarket-scanner/AGENTS.md), the change stays paper-safe, preserves graceful degradation for optional services, and is logged in `fix_logs/`.

## Changes
- Added [weather_sources.py](/Users/will/.cline/worktrees/b74e9/polymarket-scanner/weather_sources.py) as the shared weather provider layer.
- The new module centralizes:
  - provider registry metadata for weather data sources
  - NOAA NWS and Open-Meteo fetch/caching logic
  - threshold-scan source planning with explicit applicability decisions
  - structured provider results with `attempted`, `available`, `value_f`, and failure metadata
- Updated [weather_scanner.py](/Users/will/.cline/worktrees/b74e9/polymarket-scanner/weather_scanner.py) so the threshold scanner consumes the shared provider layer instead of calling NOAA/Open-Meteo directly.
- Preserved the current default path and persisted signal shape:
  - NOAA + Open-Meteo remain the default threshold sources
  - international markets still degrade to Open-Meteo-only visibility without becoming tradeable
  - existing persisted weather columns and scan outputs remain unchanged for current consumers

## Tests
- Added [tests/test_weather_sources.py](/Users/will/.cline/worktrees/b74e9/polymarket-scanner/tests/test_weather_sources.py) covering:
  - default source-plan ordering and NOAA applicability rules
  - graceful degradation when one provider returns no target-day value
  - scanner integration through the shared provider layer for US dual-source markets
  - scanner preservation of the existing single-source international non-tradeable path
