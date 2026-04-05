# 2026-04-05 Penny Live-Ledger Fail-Closed Guards

## Source
- Operator request dated 2026-04-05: treat penny mode as a real-money live ledger and fail closed whenever Polygon wallet data is unavailable, stale, or unverifiable.
- Followed `AGENTS.md` trading-mode and Always Do rules: keep audit/error logging intact, log the bug-fix work in `fix_logs/`, and update `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` when dashboard/runtime semantics change.

## Findings
- `db.get_live_account_overview()` treated the live wallet as a soft dependency and returned a renderable penny payload even when wallet verification failed.
- `blockchain.get_usdc_balance()` collapses several failure modes into `0.0`, which is acceptable for optional tooling but too weak for penny-mode live-ledger safety.
- `/api/runtime/account?runtime_scope=penny` returned HTTP 200 with a degraded object, so the dashboard could continue painting a penny lane without verified live-wallet data.
- The penny dashboard lacked an explicit `LIVE / POLYGON WALLET` status banner and did not visibly distinguish verified live-ledger mode from blocked mode.

## Fixes Applied
- Added `blockchain.get_verified_wallet_snapshot()` with hard verification gates for:
  - wallet derivation
  - Polygon chain parity (`137`)
  - latest block metadata availability
  - block freshness (`<= 180s`)
  - successful USDC.e balance lookup
- Updated `db.get_live_account_overview()` to expose explicit live-ledger verification fields:
  - `verified_live_ledger`
  - `verification_status`
  - `verification_error`
  - block / chain metadata used to verify the live ledger
- Changed `/api/runtime/account` in `server.py` to return HTTP `503` for penny scope when the live Polygon wallet cannot be verified, with a structured blocked payload instead of paper or aggregate fallbacks.
- Updated `execution.check_balance(mode=\"live\")` to reuse the verified wallet snapshot so live-balance checks fail closed on stale or unverifiable Polygon data.
- Updated `dashboard.html` so penny mode now:
  - shows `LIVE / POLYGON WALLET VERIFIED` when verification succeeds
  - shows `LIVE / POLYGON WALLET BLOCKED` when verification fails
  - blocks the penny hero/account rendering instead of showing mixed or fallback numbers
  - explicitly states that paper balances, paper PnL, and shared totals are suppressed in blocked penny mode
- Updated `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` so the repo contract now states that penny mode is fail-closed and must surface an explicit live-wallet indicator.

## Acceptance Checks
- Backend:
  - `GET /api/runtime/account?runtime_scope=penny` returns `200` only when `verified_live_ledger=true`.
  - `GET /api/runtime/account?runtime_scope=penny` returns `503` with `verification_status` and `verification_error` when Polygon verification fails or is stale.
  - Penny payloads must never backfill `available_balance_usd`, realized/unrealized PnL, or total equity from paper or aggregate sources when verification fails.
- UI:
  - Penny mode shows `LIVE / POLYGON WALLET VERIFIED` only when the backend provides verified live-ledger data.
  - Penny mode shows `LIVE / POLYGON WALLET BLOCKED` and suppresses ledger cards when the backend returns a blocked response.
  - No blocked penny state may render paper balances, paper PnL, or shared totals.
- Trading safety:
  - Live balance checks fail closed if the wallet is unavailable, Polygon RPC is stale, or chain verification fails.

## Verification
- `python3 -m unittest tests.test_runtime_scope_split`
- `python3 -m py_compile blockchain.py db.py execution.py server.py`
