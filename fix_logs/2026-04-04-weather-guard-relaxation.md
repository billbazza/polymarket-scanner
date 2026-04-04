# 2026-04-04 Weather Guard Relaxation

## Context
- Risk review noted that repeated stop-loss defeats had the strategy locked into a narrow 60h/12pp guard, which kept even resolving markets out of the 74% win-rate window we previously saw.
- AGENTS.md mandates logging every behavior change, so we also need to record which guardrails were relaxed and capture the "before/after" trade counts for downstream audits.

## Summary
- Relaxed the weather noise guard from liquidity>=10k / horizon>=60h / NOAA-OM disagreement<=12pp to liquidity>=5k / horizon>=48h / disagreement<=18pp so the strategy can reopen markets that resolve cleanly yet were previously blocked.
- Added legacy guard metadata (`legacy_stable_noise_guard`, `legacy_tradeable`) to each opportunity, plus a scan-time log line that prints the legacy vs relaxed tradeable counts along with both sets of thresholds.
- These logs provide the required before/after trade counts while keeping the published `tradeable` flag tied to the new, relaxed gate.

## Testing
- Not run (behavioral change verified by reviewing the new log entries during the next weather scan).
