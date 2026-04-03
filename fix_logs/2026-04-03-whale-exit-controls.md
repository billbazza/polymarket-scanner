# Whale Exit Controls (2026-04-03)

- [x] Added tracker guardrails for whale trades (loss limit: $15, max hold time: 48h, volatility stop: 15% adverse move) and logged the guardrail reason when auto-closing to keep the $54.52 unrealized drawdown from growing.
- [x] Logged aggregated whale drawdown when open losses drop below $50 so the historic $-54.52 situation now raises an explicit alert and the new summary is searchable in `logs/scanner.log`.
