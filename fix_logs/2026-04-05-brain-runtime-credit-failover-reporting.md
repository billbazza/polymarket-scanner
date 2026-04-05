# 2026-04-05 Brain Runtime Credit And Failover Reporting

## Summary
- Extended `brain.py` runtime reporting so `/api/brain/runtime` now shows the currently active provider, provider availability state, last fallback timestamp/reason, and the last safely observed quota/credit metadata captured from real provider responses or errors.
- Kept the endpoint read-only and truthful: it does not invent balances, and it only reports remaining-credit/quota values when an upstream SDK response/error exposed them.

## Behavior Changes
- `get_runtime_status()` now returns:
  - `active_provider`, `active_provider_source`, and `active_provider_at`
  - `last_fallback` with timestamp, source provider, destination provider, raw reason, and normalized reason kind
  - per-provider `availability` states that distinguish `missing_config`, `client_unavailable`, `available`, `active`, `credits_exhausted`, and `quota_exhausted`
  - per-provider `last_success_at`, `last_error_at`, `last_error_reason`, `last_error_kind`, `last_request_id`, plus `quota_observation` / `credit_observation` when the upstream surfaced them
- Brain requests now capture response headers through raw-response SDK paths when available so rate-limit/quota headers can be surfaced without adding new paid probes.
- Fallback events now update runtime state alongside the existing warning logs, preserving graceful degradation when all providers are unavailable.

## Safety Notes
- No live-trading execution paths changed.
- The brain layer still defaults back to the statistical signal when no provider is available.
- No fake balance or credit numbers are emitted; absent upstream balance data stays `null`.

## Verification
- `python3 -m unittest tests.test_brain_provider_migration`
- `python3 -m py_compile brain.py server.py tests/test_brain_provider_migration.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
