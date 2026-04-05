# 2026-04-05 Penny Weather Live Rollout Scope

Historical filename retained. The intermediate rollout toggle described here is superseded by the parity-first repo contract.

## Source
- Operator request dated 2026-04-05: penny autonomy was reporting `Step 4b scope=penny mode=skip-paper-only reason=paper_only_scope_disabled`, which hid the weather scan from the live runtime and left manual/live weather execution paths inconsistent.
- Followed the repo contract in `AGENTS.md`, including explicit safeguards for real-money behavior, audit logging, fix-log updates, and synced doc changes.

## Findings
- `autonomy.py` hard-skipped the entire weather phase outside the paper runtime, so penny/book never even scanned weather.
- `execution.execute_weather_trade()` blocked every live weather trade with a generic paper-only rejection, while `/api/weather/{signal_id}/trade` always forced `mode="paper"` even when `runtime_scope=penny`.
- The scoped runtime controls had no clean path to parity, so an intermediate rollout switch was added temporarily.

## Fixes Applied
- Historical note: this patch added `weather_auto_trade_enabled` and a temporary penny `scan-only` intermediate state.
- Those semantics are now superseded by `fix_logs/2026-04-05-penny-weather-paper-parity.md` and `fix_logs/2026-04-05-penny-parity-guidance-contract.md`. Active policy is that threshold-weather follows the same default admission/execution path in paper and penny/book, with only explicit live safeguard vetoes allowed.
- Implemented live single-leg weather execution for the threshold-weather lane in `execution.py`, including live balance/slippage checks, quarter-Kelly capping, GTC order submission, open-order persistence, entry execution metadata, and HMRC/audit logging.
- Kept exact-temperature weather execution paper-only in live mode with an explicit `exact_temp_paper_only` rejection.
- Updated `/api/weather/{signal_id}/trade` so penny-scoped manual weather entries now route through live execution instead of forcing paper mode.
- Extended `db.py` weather trade persistence and runtime settings so live weather trades carry scoped ledger metadata while paper-only cash checks remain paper-scope only.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` at the time; active docs no longer preserve the rollout semantics as policy.

## Verification
- `python3 -m py_compile autonomy.py execution.py server.py db.py tests/test_runtime_scope_split.py tests/test_weather_exact_temp.py`
- `python3 -m unittest tests.test_runtime_scope_split tests.test_weather_exact_temp`
