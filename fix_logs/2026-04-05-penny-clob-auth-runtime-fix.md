# 2026-04-05 Penny CLOB Auth Runtime Fix

## What changed
- Fixed the live Polymarket client bootstrap in `execution.py` so penny order submission no longer stops at level-1 wallet auth. The runtime now loads explicit CLOB API credentials from `POLYMARKET_CLOB_API_KEY` / `POLYMARKET_CLOB_API_SECRET` / `POLYMARKET_CLOB_API_PASSPHRASE` when present, or creates/derives level-2 CLOB credentials from `POLYMARKET_PRIVATE_KEY` before any live order is posted.
- Added redacted live-runtime diagnostics for CLOB auth presence and source. `/api/runtime/dependencies`, `/api/runtime/account`, `/api/autonomy/runtime`, startup logs, and live-trade failure payloads now show whether the runtime has a private key, whether explicit CLOB creds are fully configured or partially missing, whether derivation is required, and whether order-submission auth is actually ready.
- Added a fail-closed operator-facing error when live order-submission credentials are unavailable or only partially configured. Manual penny trade endpoints now return `503` with `reason_code=clob_api_auth_unavailable` instead of allowing the exchange to reject the order later with `API Credentials are needed to interact with this endpoint!`.

## Why
- Penny cointegration logs showed the live server reaching the CLOB order endpoint with valid token/side/price/size data but failing because the client had only the wallet private key loaded. `py-clob-client` needs level-2 API credentials for `post_order()`, and the previous runtime never called `create_or_derive_api_creds()` or `set_api_creds()`.
- The running server process audit confirmed the mismatch: `POLYMARKET_PRIVATE_KEY` and `ALCHEMY_API_KEY` were present in Keychain/runtime status, but there were no explicit CLOB API env vars in the live server environment and no derivation path in the client bootstrap.

## Verification
- `python3 -m unittest discover -s tests -p 'test_live_clob_auth.py'`
- `python3 -m unittest discover -s tests -p 'test_live_cointegration_execution.py'`
- `PYTHONPATH=. python3 tests/test_runtime_config.py`
- `python3 -m py_compile execution.py server.py runtime_config.py tests/test_live_clob_auth.py`
- Ad hoc runtime probe: `PYTHONPATH=. python3 - <<'PY' ... execution._create_live_clob_client(...) ... PY` returned `client_has_creds=true` with the active local Keychain/runtime, confirming level-2 auth can now be loaded without exposing the credentials themselves.
