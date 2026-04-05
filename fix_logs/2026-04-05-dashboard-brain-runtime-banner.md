# 2026-04-05 Dashboard Brain Runtime Banner

## Summary
- Added a compact dashboard banner that reuses `/api/brain/runtime` to show the current brain provider, provider disablements caused by missing config or exhausted credit/quota, and per-provider runtime chips.
- Kept the display truthful: remaining credit/quota only appears when the backend exposed a real observed value, and quota observations without a supported remaining value are labeled as unknown instead of guessed.

## Behavior Changes
- `dashboard.html` now renders a top-of-page brain-runtime card with:
  - the active provider plus whether it was observed live or inferred from configured preference
  - a concise disabled-provider summary for `credits_exhausted`, `quota_exhausted`, and `missing_config`
  - wrapped provider chips that surface real quota/credit observations when present
- Mobile styling now lets the runtime chips stack to full width so the banner stays readable without dominating the page.

## Safety Notes
- No backend provider-selection logic changed; the dashboard is read-only and only consumes the existing runtime API.
- No fake balance, credit, or quota numbers are shown.

## Verification
- `python3 -m py_compile server.py brain.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
- Local browser render check at desktop and mobile widths against the running dashboard
