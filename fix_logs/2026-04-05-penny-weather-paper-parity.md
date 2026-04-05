# 2026-04-05 Penny Weather Paper Parity

## Source
- Operator request dated 2026-04-05: penny weather was still completing as `mode=scan-only`, `trade_status=scan_only`, `reason=weather_auto_trade_disabled`, which is not acceptable for the live penny test.
- Followed the repo contract in `AGENTS.md`, including explicit safeguards for real-money behavior, audit logging, fix-log updates, and synced doc changes.

## Findings
- `autonomy.py` still let weather diverge from the primary penny runtime control through a separate `weather_auto_trade_enabled` gate.
- `db.get_autonomy_runtime_settings()` persisted that separate gate, so penny could keep scanning weather while refusing to attempt otherwise eligible threshold-weather trades.
- Runtime/operator guidance in `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` still described penny weather as opt-in behind the separate weather toggle.

## Fixes Applied
- Removed the separate penny weather scan-only gate in `autonomy.py`; weather now follows the scoped primary `auto_trade_enabled` control in both paper and penny runtimes.
- Kept the legacy `weather_auto_trade_enabled` field only for API/settings compatibility, but normalized it to the runtime-wide auto-trade value in `db.py` so it can no longer disable penny weather independently.
- Preserved explicit live safeguards per trade, including the existing exact-temperature live block; autonomy now records the full execution result in blocked weather attempts so live vetoes are audit-visible.
- Stopped auditing `weather_auto_trade_enabled` as an independent operator control in `server.py`, since it no longer changes behavior separately from the main runtime switch.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` to describe penny weather paper-parity semantics and explicit live safeguard veto reporting.

## Verification
- `python3 -m py_compile autonomy.py db.py server.py tests/test_runtime_scope_split.py`
- `python3 -m unittest tests.test_runtime_scope_split`
