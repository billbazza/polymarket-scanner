# 2026-04-04 Copy Strategy Risk/Reward Guardrail

## Source
- Daily report 2026-04-04 flagged the copy strategy as losing $14.93 despite a 57.9% win rate and asked whether it should be removed from the tests or rehabilitated.

## Problem
- Mirrored wallets routinely opened positions priced at or near 0/1 (e.g., "No @1.000"); copying those wagers yields tiny winners but $20+ losers whenever the wallet is wrong, so the high win rate still left the strategy in negative expectancy.

## Fixes Applied
- Added a 0.15–0.85 entry-price window in `db.inspect_copy_trade_open()` so copy trades refuse exposures whose risk/reward is lopsided. The block decision now records the entry price, allowed range, and new reason code `entry_price_range_violation` for diagnosis.
- Added regression coverage (`tests/test_trade_state_architecture.py`) that ensures entry prices on both ends of the spectrum are rejected while balanced mid-range tickets stay allowed.

## Live Safety
- Paper-only copy automation was touched; no live trading or blockchain calls were modified.

## Verification
- `python3 -m pytest tests/test_trade_state_architecture.py -q`
