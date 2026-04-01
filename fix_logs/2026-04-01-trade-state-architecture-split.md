# 2026-04-01 - Trade State Architecture Split

## Source
- Trade-state rationalization task for Polymarket Scanner.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/398fc/polymarket-scanner/AGENTS.md), especially Architecture, Database, Trading Modes, and Always Do.

## Summary
- Split trade persistence into explicit state modes so paper research, wallet-attached copy tracking, and live exchange-linked trades no longer share the same implicit identity model.
- Removed fake order-style reconciliation artifacts from paper pairs execution.
- Added canonical identifiers for wallet-attached and live trades, plus monitor logic that flags externally attached trades if those identifiers are missing.

## Behavior Changes
- Paper research trades now persist as `trade_state_mode=paper_research` with `reconciliation_mode=internal_simulation`.
  - Required persisted identifiers: local signal linkage, token ids, event/market labels, entry/exit prices, size, and local trade id.
  - They no longer create `open_orders` rows on immediate paper fill.
- Wallet-attached copy trades now persist as `trade_state_mode=wallet_attached` with `reconciliation_mode=wallet_position`.
  - Required persisted identifiers: `copy_wallet`, `copy_condition_id`, `copy_outcome`, `external_position_id`, and `canonical_ref`.
  - Canonical wallet key is now `wallet:<address>:condition:<conditionId>:outcome:<outcome>`.
  - Raw token/asset id is still stored separately as `external_position_id` when available.
- Live exchange trades now persist as `trade_state_mode=live_exchange` with `reconciliation_mode=exchange_orders`.
  - Required persisted identifiers: exchange order ids for both legs plus a canonical live reference.

## Operator-Facing Effects
- Manual trade-open endpoints now report the trade-state mode and reconciliation mode explicitly.
- Copy monitoring, autonomy mirroring, and trade reconciliation now use the same canonical wallet-position identity instead of a plain `(wallet, conditionId)` tuple.
- Trade reconciliation now raises `missing_canonical_identity` if an externally attached trade lacks the identifiers needed for safe reconciliation.

## Files Changed
- [db.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/db.py)
- [execution.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/execution.py)
- [wallet_monitor.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/wallet_monitor.py)
- [trade_monitor.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/trade_monitor.py)
- [autonomy.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/autonomy.py)
- [server.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/server.py)
- [tests/test_trade_state_architecture.py](/Users/will/.cline/worktrees/398fc/polymarket-scanner/tests/test_trade_state_architecture.py)

## Verification
- `python3 -m py_compile db.py execution.py wallet_monitor.py trade_monitor.py autonomy.py server.py tests/test_trade_state_architecture.py tests/test_watched_wallet_monitoring.py tests/test_paper_trade_attempts.py tests/test_cointegration_trial.py`
- `python3 -m unittest tests.test_trade_state_architecture tests.test_watched_wallet_monitoring tests.test_paper_trade_attempts tests.test_cointegration_trial`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, trade_monitor, wallet_monitor; print('OK')"`
