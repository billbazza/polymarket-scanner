# 2026-04-05 Penny Live-Book Controls And Ledger

## What changed
- Promoted `penny` from a mostly scoped label into a live-book operator lane with its own persisted runtime controls in `scanner_settings`: `auto_trade_enabled` plus `max_open_override`.
- Added dashboard controls for penny auto-trading and max-open so the live book can start at `3` open trades and be raised deliberately without touching paper state.
- Added explicit audit capture for runtime-control edits: `POST /api/autonomy/settings` now journals `runtime_controls_updated` entries with the penny scope, actor, before/after values, requested updates, and changed fields, while `scanner.log` records the same change summary.
- Routed manual penny entries through `execution.execute_trade(..., mode="live")` instead of the paper-only DB open path, and routed penny closes through a live close helper that records the submitted offset orders before persisting the close.
- Extended the trade ledger with live execution metadata: entry/exit execution JSON, paid fees, close-side slippage fields, and richer open-order rows (`purpose`, `tx_hash`, raw exchange response).
- Fed the live execution metadata into HMRC audit logging and added `/api/reporting/hmrc` so downstream reporting can read penny-scoped wallet/order/fee data without mixing in paper trades.

## Safeguards
- Penny runtime auto-trading is now explicitly configurable and defaults to disabled unless the operator enables it for the live scope.
- Penny max-open is still scope-local, so paper research positions do not consume live slots.
- The dashboard now states that penny runtime controls are live-scope only and journaled, so raising the max-open cap is visible to operators and auditors instead of hiding in a constant or environment knob.
- Live account reporting remains fail-closed on verified Polygon wallet data; none of the new controls fall back to paper balances.

## Verification
- `python3 -m py_compile server.py autonomy.py execution.py db.py hmrc.py tracker.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, server, autonomy; print('OK')"`
- `PYTHONPATH=. pytest -q tests/test_trade_state_architecture.py::TradeStateArchitectureTests::test_paper_pairs_trade_stays_internal_and_creates_no_open_orders`
- `PYTHONPATH=. pytest -q tests/test_runtime_scope_split.py -k "runtime_settings_update or dashboard_uses_runtime_scoped_history"`

## Follow-up
- The full scoped-runtime suite is still broader than this operator-control patch. If the repo's slower pytest lane regresses again, keep the penny-control acceptance check focused on the scoped settings endpoint, dashboard strings, and journal entry shape before broadening it back out.
