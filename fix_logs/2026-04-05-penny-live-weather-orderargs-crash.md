# 2026-04-05 Penny Live Weather OrderArgs Crash

## Issue
- Penny/live weather candidates were reaching the live execution path and then failing with `dict object has no attribute token_id`.
- The live order path was passing raw dict payloads like `{"tokenID": ...}` into `py-clob-client`, but the installed client expects typed `OrderArgs(token_id=..., ...)` objects.

## Changes
- Added a shared live-order builder in [execution.py](/Users/will/.cline/worktrees/23e3c/polymarket-scanner/execution.py) that normalizes token ids and constructs typed `OrderArgs` for `create_and_post_order`.
- Switched the live weather open path, shared pairs live execution path, live close path, and generic GTC helper to use the same typed order builder so object-vs-dict assumptions no longer diverge across strategies.
- Expanded live weather failure handling to log and return compact debug context with the exact weather signal id, market id, strategy, entry token, action, requested size, and order request payload.

## Regression Coverage
- Added regression coverage in [test_all.py](/Users/will/.cline/worktrees/23e3c/polymarket-scanner/test_all.py) that saves a real weather signal, runs `execute_weather_trade(..., mode="live")` with mocks, and asserts the client receives `OrderArgs` with the expected weather token.
- Added a failure-path regression that asserts `live_execution_failed` responses now carry the weather signal and order-input context needed to diagnose runtime errors.

## Verification
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"` passed.
- `python3 test_all.py` still reports pre-existing unrelated failures in the suite, but the new live weather regression checks passed within that run.
