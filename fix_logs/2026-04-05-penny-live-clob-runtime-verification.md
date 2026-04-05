## Summary

- Installed `py-clob-client` into the same Python 3.11 framework runtime used by the live server process so penny weather and cointegration execution no longer depend on an interactive-shell-only package set.
- Centralized live CLOB client bootstrap in `execution.py` so penny weather opens, cointegration opens, live closes, GTC order placement/cancel, and open-order polling all report the same structured runtime failure when the live client stack is unavailable.
- Added operator-facing runtime dependency telemetry in `/api/runtime/dependencies`, `/api/runtime/account`, and `/api/autonomy/runtime`, including the active Python executable, package availability/version, and a concrete remediation command.

## Behavior Change

- When the Polymarket live client is missing or fails to initialize in the active server runtime, the API now reports `clob_client_unavailable` or `clob_client_init_failed` with `live_execution` details instead of only logging a generic veto.
- Penny runtime account responses now fail closed with HTTP 503 when the live execution client stack is unavailable, matching the existing fail-closed wallet-ledger posture.
- Manual penny open requests for weather and cointegration now return HTTP 503 for live-client runtime failures and include the runtime dependency payload needed to diagnose the broken server environment.

## Verification

- Verified the active server interpreter path and imported `py_clob_client.client.ClobClient` from `/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python`.
- Verified `py-clob-client` is installed in that runtime and available via the same `python3` command path used for local operator checks.
- Ran targeted import/runtime checks plus the repo import smoke test after the execution/server changes.
