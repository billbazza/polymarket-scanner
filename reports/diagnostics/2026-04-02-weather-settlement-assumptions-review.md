# 2026-04-02 Weather Settlement Assumptions Review

## Scope
- Operator request dated 2026-04-02: audit the repo's current NOAA + Open-Meteo weather-edge workflow before changing strategy logic for Shanghai / ZSPD / Wunderground-style temperature markets.
- Goal: document which assumptions are generic, which are settlement-source-specific, and which changes can be introduced without breaking the existing weather scanner.
- This review is paper-safe only. No live-trading policy or execution logic is changed here.

## Current Repo Workflow
- The current weather strategy in [weather_scanner.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/weather_scanner.py) is a forecast-to-market edge model for binary threshold questions.
- It parses a market question into:
  - city
  - target date
  - direction (`above` / `below`)
  - threshold, normalized internally to Fahrenheit
- It then estimates the target day's high temperature from:
  - NOAA `forecastHourly` periods for US cities, reduced to `max(hourly temperature on target_date)`
  - Open-Meteo `daily.temperature_2m_max` for all supported cities
- Probability is then derived from a normal-error model around that point forecast, and the trade gate is still binary-threshold oriented:
  - `combined_prob = mean(available source probs)`
  - `sources_agree = both sources point the same way vs current YES price`
  - tradeable requires dual-source agreement, positive EV, positive Kelly, edge >= `15pp`, and buy price >= `35c`

## Embedded Assumptions In Current Code

### Settlement Source
- The scanner does not model the market's explicit settlement source.
- It assumes forecast skill on a city/day high is close enough to the thing being settled.
- There is no field in `weather_signals` for `resolution_source`, `station_id`, `settlement_unit`, `precision`, `timezone`, or `finalization_rule`; [db.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/db.py) stores only forecast outputs and derived edge metrics.

### Unit Handling
- `_parse_question()` converts any Celsius threshold into Fahrenheit and stores only `threshold_f`.
- NOAA values are converted to Fahrenheit if the API returns Celsius.
- Open-Meteo is explicitly requested with `temperature_unit=fahrenheit`.
- This is acceptable for binary threshold pricing, but it erases the original settlement unit and precision.
- That becomes a real mismatch for markets that settle in whole `°C`, whole `°F`, or one-decimal `°C` increments.

### Timestamp And Day Boundary Handling
- NOAA high is computed from hourly periods whose `startTime` begins with the target ISO date.
- Open-Meteo daily values are requested with `timezone=auto`, so daily max values are returned in local time for the location.
- The scanner does not persist the timezone used for a given estimate.
- The scanner also does not model a settlement feed's "finalized" state; it only prices pre-resolution.

### Peak-Hour Behavior
- The current repo stores only a daily high estimate.
- It does not track the hour of the peak, the intraday path, or which exact observation created the day's final max.
- That is sufficient for the current binary "will it exceed X" workflow, but not for settlement models where one station's specific observation timestamp matters operationally.

### Location Dependency
- `CITIES` maps city names to generic city coordinates, not explicit settlement stations.
- That works for general forecast retrieval.
- It is not enough for station-settled markets where the market names one source and one station:
  - station identity may differ from city centroid
  - the settlement station may be an airport rather than the city center
  - different cities may use different publishers entirely

## Proposed Shanghai / ZSPD / Wunderground-Style Model
- The user-proposed shape is materially different from the repo's current weather edge:
  - settlement is tied to an explicit station, not a city-level forecast proxy
  - settlement comes from a named source page, not from "best available forecast"
  - units and precision are part of the market rules
  - finalization timing matters because Polymarket waits for the source to finalize
  - some markets are multinomial exact-temperature markets rather than binary threshold markets

## Confirmed External Settlement Assumptions
- A Shanghai market page on Polymarket states that the market resolves to the range containing the highest temperature recorded at the Shanghai Pudong International Airport Station, sourced from Wunderground history for station `ZSPD`, measured in whole degrees Celsius, and not considered final until the source data is finalized.
- A NYC market page states that the market resolves to the highest temperature recorded at LaGuardia Airport Station, sourced from Wunderground history for station `KLGA`, measured in whole degrees Fahrenheit, and ignoring revisions after finalization.
- A Hong Kong market page states that the market resolves from the Hong Kong Observatory "Absolute Daily Max" daily extract and uses Celsius to one decimal place.
- A Seoul market page shows another location-specific dependency: Polymarket has used Wunderground station `RKSI` (Incheon Intl Airport), which is not simply "Seoul city center."

## Comparison: Reusable Vs Market-Specific

### Reusable From The Existing Weather Scanner
- Event fetching and weather-topic filtering in [weather_scanner.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/weather_scanner.py).
- Paper-safe persistence flow through [server.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/server.py), [db.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/db.py), and the existing weather signal lifecycle.
- Generic forecast-provider access patterns:
  - Open-Meteo fetch and caching
  - NOAA hourly fetch and caching for US locations
- The probability/error-model skeleton can still be reused, but only after the forecast target is redefined to match the settlement target.
- Existing risk controls and paper-trade admission for `trade_type='weather'`.

### Market-Specific Or Not Safely Reusable
- `_parse_question()` as the primary market parser for exact-outcome temperature ladders.
  - It expects direction words like `above` / `below`.
  - It does not represent multinomial bins such as `19°C`, `20°C`, `21°C or higher`.
- `threshold_f` as the canonical single target representation.
  - Exact-temperature markets need a bin model, not a simple threshold model.
- `sources_agree` as the main trust gate.
  - Settlement-source markets should first align to the settlement source and station before comparing providers.
- City centroid coordinates as a location abstraction.
  - Settlement markets need explicit station/source metadata.
- Daily-high-only abstraction with no explicit precision or finalization rules.

## Main Mismatches To Resolve Before Strategy Changes

### Settlement Source Mismatch
- Current scanner prices "forecast city high."
- Shanghai-style markets settle "finalized station high from a named source page."
- Those are related but not equivalent targets.

### Unit And Precision Mismatch
- Current scanner normalizes everything into Fahrenheit floats.
- Settlement can be:
  - whole `°C`
  - whole `°F`
  - one-decimal `°C`
- Any exact-bin strategy must preserve the original settlement unit and precision through parsing, modeling, and outcome mapping.

### Timestamp / Peak-Hour Mismatch
- Current scanner cares only about the final daily maximum proxy.
- Station-settled markets can be sensitive to:
  - local timezone boundaries
  - the exact station day's definition
  - when the max occurs and whether later observations can still move the day's final max before source finalization

### Location Dependency Mismatch
- Current city list is broad and useful for forecasts.
- Station-settled markets need a curated mapping layer:
  - `city/event -> settlement_source`
  - `station_id`
  - `station_timezone`
  - `unit`
  - `precision`
  - `forecast_coordinate` and optionally `station_coordinate`

## Recommendation
- Do not fold Shanghai / ZSPD / Wunderground-style logic directly into the current `weather_scanner.scan()` path.
- Recommended shape:
  - keep the current NOAA + Open-Meteo binary-threshold scanner intact
  - add a separate weather sub-strategy for station-settled exact-temperature markets
  - extract a small shared weather abstraction layer only for clearly reusable pieces

## Why This Is The Safest Shape
- It avoids breaking the existing binary weather scanner, which is already wired into scan jobs, storage, and paper-trading flow.
- It prevents exact-outcome settlement rules from contaminating the current threshold parser and probability model.
- It creates a clean path to support multiple settlement regimes:
  - Wunderground airport-station markets
  - Hong Kong Observatory markets
  - future source-specific variants

## Concrete Implementation Recommendation

### 1. Add A Settlement-Spec Extension Point
- Introduce a small shared module, for example `weather_settlement.py`, with a structured spec such as:
  - `market_family`
  - `source_kind`
  - `source_url_template`
  - `station_id`
  - `station_label`
  - `timezone`
  - `settlement_unit`
  - `settlement_precision`
  - `settlement_metric` (`daily_high`)
  - `finalization_rule`
- Keep it read-only at first; do not change existing trading logic to depend on it yet.

### 2. Keep The Existing Scanner As `weather_threshold`
- Treat the current [weather_scanner.py](/Users/will/.cline/worktrees/7997d/polymarket-scanner/weather_scanner.py) as the threshold/binary strategy.
- Do not change its parser or trade gate in the same patch that introduces station-settled support.

### 3. Add A Separate Scanner For Exact Temperature Markets
- Add a new module, for example `weather_station_scanner.py` or `weather_exact_temp_scanner.py`.
- This scanner should:
  - parse multinomial exact-temperature ladders
  - preserve market unit and precision
  - map the event to a settlement spec
  - generate outcome-bin probabilities from one or more forecast providers
  - remain paper-only until validated

### 4. Refactor Only The Shared Forecast Pieces
- Safe extractions from the current scanner:
  - provider fetchers and caches
  - common city/station registry helpers
  - temperature normalization helpers
  - forecast error calibration helpers
- Do not share the existing binary parser or `sources_agree` gate by default.

### 5. Extend Persistence Without Breaking Current Rows
- Add only nullable metadata for the new path, for example:
  - `market_family`
  - `resolution_source`
  - `station_id`
  - `settlement_unit`
  - `settlement_precision`
  - `timezone`
  - `source_meta_json`
- Existing weather rows should continue to save and load unchanged.

## Paper-Safe Rollout Order
1. Add settlement-spec metadata and the separate exact-temp scanner behind a manual/API-only entry path.
2. Persist signals but do not auto-trade them.
3. Compare forecasted outcome distributions versus finalized settlement prints for a sample of Shanghai, Seoul, NYC, and Hong Kong markets.
4. Only after that, decide whether the new sub-strategy deserves paper automation.

## Bottom Line
- This should be implemented as a separate weather sub-strategy with a shared settlement-spec extension point, not as a direct rewrite of the current weather scanner.
- A light refactor of shared weather abstractions is worthwhile, but only around provider access and settlement metadata.
- The current scanner's assumptions are reusable for binary threshold weather markets; they are not settlement-faithful enough to be reused unchanged for Shanghai / ZSPD / Wunderground-style exact-temperature markets.
