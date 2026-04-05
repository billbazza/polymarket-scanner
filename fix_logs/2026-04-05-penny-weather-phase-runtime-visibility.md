# 2026-04-05 Penny Weather Phase Runtime Visibility

## Context
- Operator report dated 2026-04-05: manual penny scans completed too quickly in the dashboard and did not appear to run the weather strategy when watching the live server CLI.
- Per the runtime guardrails, weather auto-trading is paper-only outside the research runtime, but the penny autonomy path did not state that clearly in either the cycle result payload or the dashboard copy.

## Findings
- `POST /api/autonomy` launches a background autonomy cycle and returns immediately.
- In `autonomy.py`, the penny/runtime-scoped weather step was intentionally skipped for non-paper scopes, but only a generic journal entry explained that choice.
- The dashboard runtime panel only showed opened/closed trade totals, so operators could not tell whether the penny weather phase had completed, failed, or been intentionally skipped.

## Changes
- Added explicit weather-phase telemetry to `autonomy.run_cycle()` including status, timing, counts, and skip/error reasons.
- Logged clear weather-phase start/completion lines in the autonomy runtime so penny/book cycles now state that the weather phase was skipped because it is paper-only.
- Updated `server.py` autonomy background status payloads to expose `execution_mode=background`, `all_enabled_phases_completed`, and the structured weather-phase summary.
- Updated `dashboard.html` so manual autonomy runs are labeled as background work and the runtime panel/toast now show the weather-phase outcome for the selected scope.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` with the explicit background-run and penny-weather-skip semantics.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
