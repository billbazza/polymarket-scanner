# 2026-04-05 Penny Weather Strategy Parity Preflight

## Source
- Operator request dated 2026-04-05: penny weather could log `Executing weather trade ... mode=live` and then veto the same candidate with `horizon_too_short`, which violated the repo parity contract for threshold-weather.
- Followed the repo contract in `AGENTS.md`: keep penny weather strategy admission aligned with paper, keep scoped weather history isolated, log every trading decision, and update `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` together when the guidance changes.

## Findings
- Threshold-weather scan output was being treated as the final `tradeable` classification in autonomy, but live execution could still apply a later horizon revalidation and reject the candidate after the runtime had already counted it as executable.
- Runtime/UI summaries exposed `history_source` for weather blockers even when the blocker was not historical, which hid pre-execution admission failures such as `horizon_too_short` behind incomplete source attribution.
- Penny weather history isolation in `db.inspect_weather_trade_open()` was already scoped to `trade_type='weather'` plus the active `runtime_scope`, but the preflight/runtime reporting path was not using that canonical admission decision early enough.

## Changes Made
- Moved threshold-weather horizon revalidation into `db.inspect_weather_trade_open()` via a shared preflight helper so paper and penny weather both use the same admission path before execution.
- Updated `autonomy.py` so weather phase counts now distinguish:
  - `scan_tradeable`: raw threshold scanner tradeable output
  - `tradeable`: preflight-ready candidates after shared weather admission checks
  - `preflight_blocked`: candidates rejected before execution
- Stopped the weather phase from submitting candidates to execution when the shared preflight already named a blocker such as `horizon_too_short`; those blockers now record attempts/journal entries with `decision_source`, `blocker_source`, and `history_source`.
- Updated `execution.execute_weather_trade()` so live weather logs the concrete preflight veto reason before any real execution work and no longer emits the misleading `Executing weather trade ...` line ahead of a guaranteed rejection.
- Updated weather runtime/UI status wiring so preflight blocks and live-only vetoes surface their concrete reason codes instead of collapsing to a generic `live_safeguard_vetoed`, and weather signal rows now use `blocker_source` rather than `history_source` for non-history blockers.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` to state the exact per-strategy parity rule for threshold weather.

## Verification
- `python3 -m unittest tests.test_weather_signal_lifecycle tests.test_runtime_scope_split`
- `python3 -m py_compile autonomy.py db.py execution.py server.py`
