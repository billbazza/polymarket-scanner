# 2026-04-03 Copy Strategy Filter Rework

## Source
- Follow-up to the copy strategy review and the new user request to harden wallet mirroring filters and gating.

## Problem
- `wallet_monitor` was rounding the average trade size before evaluating `will_copy`, so wallets whose 749.5‑ish average could still be marked copy‑eligible, and there was no single helper capturing the informed/65/750 thresholds consistently.
- `inspect_copy_trade_open()` already guarded on wallet PnL/AI verdict, but the logic was duplicated in place instead of being surfaced through a reusable safety gate.
- `_add_column_if_missing()` blew up when the `signals` table didn’t exist yet, which made the watched‑wallet repair test fail when a backfilled schema had recorded migrations without creating every table.

## Fixes Applied
- Added `_should_copy_wallet()` and ensured `score_wallet()` passes the exact average size (rounded to two decimals for display) when deciding `will_copy`, so the 65‑point, $750+ informed filter runs on real data.
- Introduced `_wallet_copy_block_decision()` so `inspect_copy_trade_open()` now consistently leverages the stored wallet PnL and AI verdict to block trades before reaching the rest of the pipeline.
- Crafted `_table_exists()` + `_add_column_if_missing()` guard so missing tables are skipped instead of triggering `ALTER TABLE` errors during repair passes.
- Verified that decimal math in `tests/test_strategy_performance.py` still reports exactly one copy loss, so no spurious “decimal” losses are being fired from this change.

## Live Safety
- Paper-only copy automation logic; no live trading or smart-contract calls were touched.

## Verification
- `python3 -m pytest tests/test_watched_wallet_monitoring.py -q`
- `python3 -m pytest tests/test_strategy_performance.py -q`
