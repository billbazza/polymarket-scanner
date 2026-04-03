# 2026-04-03 xAI Brain Integration

## Summary
- Added Grok (xAI) as another brain provider so the scanner can extend the existing Anthropic→OpenAI fallback chain when credits allow.
- Extended documentation and runtime-status reporting so operators can see xAI readiness and configure it via `.env` like the other providers.

## Behavior Changes
- `BRAIN_PROVIDER` now accepts `xai` and `auto` orders the providers as Anthropic → OpenAI → xAI when the matching API keys exist.
- Introduced the `XAI_API_KEY`/`XAI_BASE_URL` tokens (plus `BRAIN_XAI_MODEL`/`BRAIN_XAI_COMPLEX_MODEL`) for configuring Grok, and added Grok defaults to the model alias map.
- `brain.py` now instantiates an `xai_sdk.Client`, layers Grok responses into `_brain_request`, and falls forward to Grok when earlier providers report credential, quota, rate-limit, or billing-style errors.

## Safety Notes
- No new live-trading paths were enabled and the brain layer still gracefully degrades to the statistical signal whenever Grok (or any provider) is unavailable.
- The new provider obeys the existing logging and Kelly/slippage guardrails (no auto live trades or FOK orders were introduced).

## Verification
- Added regression tests covering the new provider ordering, runtime status data for xAI, and OpenAI→xAI fallback.
