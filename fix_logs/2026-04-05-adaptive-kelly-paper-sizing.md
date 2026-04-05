# 2026-04-05 Adaptive Kelly Paper Sizing

## Source
- Follow-up to the 2026-04-05 request to use more capital in successful weather and cointegration paper strategies while keeping explicit Kelly, cost, and drawdown guardrails visible in the audit trail.

## Changes Applied
- Extended `paper_sizing.py` so paper weather and cointegration sizing now supports an adaptive Kelly policy with edge tiers at `18%`, `25%`, and `35%`.
- Added the default `4 / 6 / 8 / 10` Kelly-cap preset and constrained the final paper trade size to the requested `$5-$10` guardrail range.
- Added account-level `MAX_DRAWDOWN` enforcement based on paper total-equity drawdown, defaulting to `15%`, and persisted the threshold plus preset selection through the existing `paper_sizing_framework` setting.
- Exposed a write API in `server.py` for paper-sizing settings so the dashboard can update the drawdown threshold and active tier preset.
- Updated `dashboard.html` so operators can change `MAX_DRAWDOWN` and the adaptive tier preset directly from the paper-account card under Available Cash.
- Expanded the same dashboard control surface so operators can also raise or lower the adaptive min/max trade-size range on the fly, with the server applying the new range to both weather and cointegration paper sizing.
- Extended `tests/test_paper_sizing.py` with tier, floor/ceiling, drawdown-gate, and API-setting coverage.

## Verification
- `python3 -m unittest tests.test_paper_sizing`
- `python3 -m py_compile paper_sizing.py server.py`
