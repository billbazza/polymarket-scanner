# 2026-04-02 Brain Provider Migration

## Summary
- Added a staged AI-provider migration path in `brain.py` so the repo can continue using Anthropic while credits remain and fall forward to OpenAI/Codex when Anthropic becomes unavailable or exhausted.
- Kept graceful degradation intact: if no AI provider is available, validation still defaults to the existing math-first safe path and paper mode remains the default operating mode.

## Behavior Changes
- Added `BRAIN_PROVIDER=auto|anthropic|openai`.
- Added OpenAI standby/cutover support via `OPENAI_API_KEY`.
- Added optional `OPENAI_BASE_URL` override plus a read-only `/api/brain/runtime` endpoint so operators can confirm cutover readiness without changing trading behavior.
- Added provider-specific model override env vars for default and complex brain tasks.
- Changed `brain.py` model alias resolution to read env config at call time, so provider/model cutover settings are reversible after deploy instead of being frozen at import.
- Updated operator-facing docs and UI copy from Claude-specific wording to provider-neutral brain wording.

## Safety Notes
- No live-trading execution path was widened or enabled.
- `brain.validate_signal()` still degrades to allowing the statistical signal when the AI layer is unavailable or errors.
- Paper mode remains the default and no explicit real-money confirmation paths were changed.

## Verification
- Added regression tests covering provider ordering, no-provider graceful degradation, and Anthropic-to-OpenAI fallback on credit/quota-style errors.
- Added regression tests for runtime model override selection, runtime-status reporting, and pinned-provider graceful degradation when the selected provider is not configured.
