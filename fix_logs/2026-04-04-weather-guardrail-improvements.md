# 2026-04-04 Weather Guardrail Improvements

## Context
- Risk reviewers asked for tighter reopen controls, horizon sanity checks before fills, and better telemetry on weather stop losses without losing the journal/diagnostic audit trail.

## Summary
- Added a probation counter for reopened weather tokens (configurable via `reports/diagnostics/weather-token-reopen-approved.json`) and enforced it inside `db.inspect_weather_trade_open()` so approved experiments can still reopen while repeated reissues hit a `token_probation_blocked` gate.
- Revalidated each signal's horizon just before execution, capped weather holds to ~72h, and persisted stop contexts per token/stop event so the new JSONL trace (`reports/diagnostics/weather-stop-contexts.jsonl`) plus the journal share the same structured payload.
- Wired the new guardrails through the paper execution pipeline, updated the weather tracker logging, and added a dedicated journal helper so automated and manual flows all mention the same stop-context metadata.

## Testing
- `python3 -m pytest tests/test_weather_signal_lifecycle.py`
