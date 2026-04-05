# 2026-04-05 Keychain Runtime Config Migration

## What changed
- Replaced the repo’s automatic `.env` loading path with `runtime_config.py`, which reads scanner config from the macOS Keychain service `polymarket-scanner`.
- Updated the secret-sensitive runtime consumers (`brain.py`, `perplexity.py`, `blockchain.py`, `execution.py`, `auth.py`) plus config-driven modules (`db.py`, `weather_exact_temp_scanner.py`, `weather_risk_review.py`) to read through the shared loader instead of direct `os.environ` access.
- Updated entry points and utility scripts to log a redacted runtime-config audit line at startup so operators can confirm whether values came from Keychain or explicit process env overrides.

## Behavior changes
- `.env` is no longer auto-loaded at runtime. Operators must place persistent config in the macOS Keychain or inject process env vars explicitly for one-off runs.
- Process env vars still override Keychain values for tests, CI, and emergency local cutovers, which preserves the existing test harness and graceful fallback behavior.
- Live-trading safeguards are unchanged: live mode still depends on `POLYMARKET_PRIVATE_KEY`, balance checks, slippage checks, and the existing explicit confirmation flow before promotion beyond paper.

## Migration notes
- Store each config item as a Keychain generic password whose account matches the config key, for example `POLYMARKET_PRIVATE_KEY`, `ALCHEMY_API_KEY`, or `OPENAI_API_KEY`.
- If you need multiple environments on one Mac, point the process at a different Keychain service with `SCANNER_KEYCHAIN_SERVICE`.
- Startup logging now records `Runtime config (...)` lines in `logs/scanner.log` so missing Keychain support or missing live-trading inputs are visible without exposing secret values.
