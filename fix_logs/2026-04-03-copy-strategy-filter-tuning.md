# 2026-04-03 Copy Strategy Filter Tuning

## Source
- Daily-report follow-up to tighten watched-wallet automation per the AGENTS.md copy strategy guidance.

## Problem
- Watched-wallet mirroring currently activates once a wallet just barely clears a 60-point threshold and trades at least $500 on average, so neutral or unproven wallets slipped into the copy pipeline.
- Copy-trade gating did not re-check the stored wallet scoring data, so we could still mirror wallets with net negative PnL or with recent AI verdicts that advised against copying.

## Fixes Applied
- Raised the wallet-monitor size threshold to $750 and now only sets `will_copy` when the wallet is classified as `informed` with at least a 65-point score, ensuring the dashboard, API, and autonomy loops align on the stricter filter.
- Extended `db.inspect_copy_trade_open()` so it looks up the watched-wallet record, blocks any copy trade if the wallet’s stored PnL is negative, and refuses to mirror wallets whose retained AI verdict is not `copy`. This keeps the paper pipeline from blindly reopening known weak profiles.

## Live Safety
- No live trading paths were touched; the new filters only affect paper copy automation and the human-facing reporting around watched wallets.

## Verification
- `python3 -m pytest tests/test_strategy_performance.py -q`
