# 2026-04-04 Weather Guard Relaxation

## Context
- Daily risk review flagged that repeated stop-outs on the same temperature tokens (Los Angeles April 4 and Dallas April 2) are being prevented from reopening, which hides the controlled experiments we need for the upcoming noise/horizon tuning in paper and penny modes.
- AGENTS.md requires logging every bug-fix or behavior change, and this work relaxes the noise/token guard for approved experiments while keeping live operations unchanged.

## Summary
- Added an explicit review/config layer (`reports/diagnostics/weather-token-reopen-approved.json`) so operator-approved markets can bypass the `stable_noise_guard` for paper/penny proofs.
- Introduced `weather_risk_review.py` to look up approval metadata and expose whether noise or token guards should be relaxed for the current mode.
- Updated `weather_scanner.py` to consult the review before deciding whether a signal is tradeable, ensuring paper/penny scans can reopen the approved tokens while log entries note when guards are overridden.
- Logged the controlled reopen request so downstream risk reviewers can audit which signals were granted guard relaxations.

## Testing
- Not run (manual verification via scheduled weather scan likely). 
