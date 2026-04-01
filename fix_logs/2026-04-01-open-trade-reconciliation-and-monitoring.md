# 2026-04-01 - Open Trade Reconciliation And Monitoring

## Summary
- Added an explicit open-trade reconciliation path that audits every open trade against live Polymarket/Gamma state and classifies it as `resolved`, `unpriceable-but-identifiable`, `detached-from-watched-wallet`, or `genuinely-still-open`.
- Added durable SQLite audit logging for reconciliation outcomes and auto-remediation actions in a new `trade_monitor_events` table.
- Surfaced flagged open trades in the dashboard and via API so past-end-date contradictions are visible instead of silently lingering.

## Behavior Changes
- `trade_monitor.py` now inspects open trades using:
  - Gamma market state and outcome prices
  - midpoint pricing where available
  - copy-wallet live positions from Polymarket data API
- High-confidence remediation now happens automatically for:
  - resolved markets with a reliable final outcome price
  - detached copy trades when a reliable exit price is available
  - obvious synthetic placeholder/test trades that cannot be priced
- Past-end-date but still-active markets are no longer treated as implicitly resolved. They remain open, but are flagged as `genuinely-still-open` with `attention_required`.

## Current Reconciliation Result
- Reconciliation run executed at `2026-04-01 19:18 UTC`.
- Scanned `24` open trades.
- Auto-closed `1` trade:
  - Trade `#282` (`whale`, synthetic placeholder `test_token_id`) was administratively closed flat at entry price with `pnl=0.0`.
- Remaining open trades after reconciliation: `23`.
- Remaining flagged open trades: `5`.
  - Weather trades `#281` and `#288` are past expected close and still active on Polymarket.
  - Copy trades `#180`, `#186`, and `#189` are past their nominal end dates but still active/disputed on Gamma and still attached to watched wallets.

## Files Changed
- `db.py`
- `trade_monitor.py`
- `autonomy.py`
- `server.py`
- `dashboard.html`

## Verification
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, trade_monitor; print('OK')"`
- `python3 -m py_compile db.py trade_monitor.py server.py autonomy.py tracker.py execution.py`
- `python3 - <<'PY' ... trade_monitor.reconcile_open_trades(auto_remediate=True) ... PY`
