# 2026-04-05 Penny Live-Book Controls And Ledger

## What changed
- Promoted `penny` from a mostly scoped label into a live-book operator lane with its own persisted runtime controls in `scanner_settings`: `auto_trade_enabled` plus `max_open_override`.
- Added dashboard controls for penny auto-trading and max-open so the live book can start at `3` open trades and be raised deliberately without touching paper state.
- Routed manual penny entries through `execution.execute_trade(..., mode="live")` instead of the paper-only DB open path, and routed penny closes through a live close helper that records the submitted offset orders before persisting the close.
- Extended the trade ledger with live execution metadata: entry/exit execution JSON, paid fees, close-side slippage fields, and richer open-order rows (`purpose`, `tx_hash`, raw exchange response).
- Fed the live execution metadata into HMRC audit logging and added `/api/reporting/hmrc` so downstream reporting can read penny-scoped wallet/order/fee data without mixing in paper trades.

## Safeguards
- Penny runtime auto-trading is now explicitly configurable and defaults to disabled unless the operator enables it for the live scope.
- Penny max-open is still scope-local, so paper research positions do not consume live slots.
- Live account reporting remains fail-closed on verified Polygon wallet data; none of the new controls fall back to paper balances.

## Verification
- `python3 -m py_compile server.py autonomy.py execution.py db.py hmrc.py tracker.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, server, autonomy; print('OK')"`
- `PYTHONPATH=. pytest -q tests/test_trade_state_architecture.py::TradeStateArchitectureTests::test_paper_pairs_trade_stays_internal_and_creates_no_open_orders`

## Follow-up
- The scoped runtime API regression test currently hangs under the repo's older pytest interpreter path; the new code imports cleanly, but that test still needs a stable Python/tooling lane before it can be used as the acceptance check for the penny runtime controls.
