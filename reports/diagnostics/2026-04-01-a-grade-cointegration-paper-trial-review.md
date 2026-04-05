# A-Grade Cointegration Paper Trial Review

Historical review. The paper-only trial restriction discussed here is superseded by the 2026-04-05 penny-parity updates.

Date: 2026-04-01
Decision: keep_experimental

## Scope
- Review whether grade A cointegration signals should be promoted beyond A+ in the trial path.
- This review predates the parity-first repo contract and should not be used to justify a paper-only restriction.

## Current Readout
- Historical DB snapshot on 2026-04-01:
  - total trades: 108
  - open trades: 22
  - closed trades: 86
  - recorded signals: 4,647
- Cointegration trial summary after rollout:
  - historical A+ signals seen: 334
  - historical A signals seen: 3,080
  - instrumented A+ cohort trades: 0
  - instrumented A-trial cohort trades: 0
  - aggregated rejection reasons from legacy pre-trial A signals: `unknown=3080`

## Interpretation
- There is no post-instrumentation A-trial trade sample yet.
- Existing historical cointegration rows predate the new cohort metadata, so they are not suitable for a clean A vs A+ performance comparison.
- Because of that, there is not enough evidence yet to promote or reject A-grade cointegration trading on performance grounds.

## Trial Guardrails Added
- Historical note: A-grade trades were paper-only at the time of this review. That restriction is no longer active.
- Size is reduced relative to the standard paper position.
- Liquidity and slippage gates are stricter than the A+ default path.
- Trial trades now carry explicit:
  - reversion exit z
  - stop z threshold
  - max hold hours
  - regime-break threshold

## Promotion Criteria
- Keep the trial experimental until there are at least 20 closed A-trial trades with comparable A+ control data from the same post-rollout period.
- Consider promotion only if all of these are true:
  - realized P&L is positive
  - win rate is not materially worse than A+
  - worst/average MAE is not materially worse than A+
  - regime-break rate is not materially worse than A+
- Reject the trial if it produces persistent negative realized P&L or materially worse drawdown/regime-break behavior.

## Recommendation
- Keep experimental.
- Revisit after a fresh parity sample is collected through the A-trial path and reviewed via `GET /api/cointegration/trial`.
