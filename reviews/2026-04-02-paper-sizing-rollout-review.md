# 2026-04-02 Paper Sizing Rollout Review

## Scope
- Review dynamic paper position sizing and higher bankroll utilization only after the signal-admission and trade-state/accounting fixes logged on 2026-04-01.
- Followed the Architecture, Rules, and Always Do sections of [AGENTS.md](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/AGENTS.md).

## Preconditions Reviewed
- Paper trade admission is now centralized and visible through structured blockers for pairs and weather in [db.py](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/db.py).
- Paper trade state is explicitly split from wallet-attached and live exchange states per [reviews/2026-04-01-trade-state-architecture-review.md](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/reviews/2026-04-01-trade-state-architecture-review.md).
- Paper accounting and strategy bankroll utilization are now derived from consistent mark-to-market logic in [db.py](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/db.py) and surfaced via `/api/paper-account`.

## Recommendation
- Do not increase default paper trade sizes or broaden bankroll utilization yet.
- Run shadow-mode paper sizing first: keep execution on fixed sizing, compute confidence-aware recommendations per strategy, and compare the recommendation stream before any rollout.
- Keep the framework paper-only. Live sizing should remain unchanged until a fresh paper review confirms:
  - admission blockers are stable
  - trade-state reconciliation stays correct
  - paper-account metrics remain internally consistent under higher projected utilization

## Framework Gate
- Active execution policy remains `fixed`.
- Confidence-aware sizing is calculated for `cointegration` and `weather` only.
- Rollout state remains `shadow`, so recommendations are logged but not used to change fills by default.
- If confidence-aware sizing is ever activated for paper, rollback is one setting change back to `fixed`.

## What To Review Before Broader Rollout
- Compare fixed vs confidence-aware recommendations by strategy in `/api/paper-sizing`.
- Confirm projected utilization stays within the configured total and per-strategy caps.
- Review whether confidence recommendations materially improve EV capture without creating blocker churn from cash exhaustion or strategy crowding.
- Produce a new dated review note from fresh paper samples before changing `rollout_state` away from `shadow`.
