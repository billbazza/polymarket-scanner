# 2026-04-04 Weather Stop-Loss Tuning

## Context
- Daily-report follow-up dated 2026-04-03 flagged stop-loss pain centered around weather trades that closed for -$4 to -$5 (18% drawdowns) shortly after entry.
- The existing 48-hour horizon gate still lets noisy, near-event books slip through, so we need an extra buffer plus better telemetry to tune how much intraday lookback to trust.

## Analysis & Changes
- Raised the minimum horizon gate in `weather_scanner.py` from 48 to 60 hours so only trades that are ~2.5+ days out make the cut; the extra buffer keeps the scanner out of the most volatile late-entry regimes where stops are breathing hard.
- Added helpers in `tracker.py` that, whenever a weather stop-loss fires, capture the associated signal, entry/stop prices, edge, hours ahead, and any intraday observation/ lookback metadata so the logs can be mined later to tune the lookback window.
- Stop logs now carry a structured `context` payload which includes the observation time, temps, previous temps, lookback delta, and trend so we can validate whether the stop reflected forecast drift or late-window noise.

## Tests
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
