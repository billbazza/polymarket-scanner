# 2026-04-04 Weather Guard State Audit

## Context
- Per the April 4 directive, we need to keep every currently tradeable weather signal within the low-guardrail regime until we observe real failures and to capture which filters would have blocked the opportunity.
- The existing guard hierarchy was hard-coded, so once the scanner enforced the April 1 gate the strategy could not admit naturally resolving markets even when they were still worth a look.

## Summary
- Added `weather_guard_state.py`, a tiered guard-state machine with a persisted failure counter so the system stays in the minimal tier (liq>=0, horizon>=0h, disagreement<=100pp) until three stop-loss hits escalate it to the next tier, and records each transition/failure with the reason.
- Rewired `weather_scanner.scan()` to consult the guard state for its noise guard thresholds, tag every opportunity with the current tier, keep the tradeable flag on even when legacy filters would have blocked, and log both the live blocking filters and the legacy blocker counts so the audit trail still surfaces which constraints went off.
- Updated the execution horizon revalidation and the tracker stop-loss path to use the guard state (and to register a failure when the weather stop-loss hits) so a string of failed outcomes gradually tightens the guard while we keep the low-guardrail regime active during healthy runs.

## Plan
- Keep the guard at the minimal tier (same-day + any disagreement + zero liquidity gate) and rely on `reports/diagnostics/weather-guard-state.json` to record the failure count. After `weather_guard_state.register_failure()` hits the configured threshold, the guard escalates automatically to the guarded tiers described in the file, and future scans will build on those thresholds while the logs still show what filters would have blocked previously.

## Testing
- Not run (behavioral change verified via reasoning and future scans/logs will confirm the new guard state outputs).
