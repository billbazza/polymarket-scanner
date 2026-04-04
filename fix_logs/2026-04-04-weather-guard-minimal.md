# 2026-04-04 Weather Guard Minimal Trial

## Context
- The April 1 guardraise locked the weather scanner behind liquidity/horizon/consensus thresholds that are now preventing the strategy from re-discovering naturally resolving opportunities, so we are deliberately stepping the guard down to its minimal defaults for now.
- AGENTS.md insists we record every behavior change in `fix_logs/`, and this change also needs an explicit plan for how and when the guardrails will go back up so live trading doesn’t over-analyze situations that are currently healthy.

## Summary
- Set the relaxed guard to `liquidity>=0`, `horizon>=0h`, and `source disagreement<=1.0` so the scan can highlight any weather opportunity and not be blocked by the April 1 gate until we have more data.
- Extended each opportunity with `filter_status` + `blocking_filters`, logged aggregated blocking counts per scan, and exposed the specific blocking filters during verbose output so we can see, for example, whether liquidity or disagreement is still the frequent blocker.
- Plan: keep the guard at this minimal level until we observe real failed weather trades (logged via the new blocking-filter instrumentation); after that signal, dial each threshold back up one-at-a-time (e.g., raise horizon first, then liquidity) and document those increments through additional fix logs.

## Testing
- Not run (behavioral change verified by inspecting the new guardlog output during the next weather scan).
