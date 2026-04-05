# 2026-04-05 Penny Weather Phase Runtime Visibility

## Context
- Operator report dated 2026-04-05: manual penny scans completed too quickly in the dashboard and did not appear to run the weather strategy when watching the live server CLI.
- This fix log originally described a paper-only weather skip. That staged behavior is now superseded by the parity-first repo contract: penny weather should follow paper by default, with only explicit live safeguard vetoes allowed.

## Findings
- `POST /api/autonomy` launches a background autonomy cycle and returns immediately.
- At the time of the original fix, `autonomy.py` skipped penny weather entirely and only a generic journal entry explained that choice.
- The dashboard runtime panel only showed opened/closed trade totals, so operators could not tell whether the penny weather phase had completed, failed, or been intentionally skipped.

## Changes
- Added explicit weather-phase telemetry to `autonomy.run_cycle()` including status, timing, counts, and skip/error reasons.
- Historical note: the original patch logged the paper-only skip explicitly. Active guidance now requires the same telemetry to report real execution, `slots_full`, or explicit live safeguard vetoes instead.
- Updated `server.py` autonomy background status payloads to expose `execution_mode=background`, `all_enabled_phases_completed`, and the structured weather-phase summary.
- Updated `dashboard.html` so manual autonomy runs are labeled as background work and the runtime panel/toast now show the weather-phase outcome for the selected scope.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` at the time; those docs are now updated again to remove the penny-weather-skip contract.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
