# 2026-04-04 whale execution rollout

- Added `db.inspect_whale_trade_open()` and a centralized `execution.execute_whale_trade()` so every whale opportunity now runs through the same quarter-Kelly cap, 2% slippage check, balance gate, and duplicate-suppression chain that the other strategies use before writing to `trades`.
- Updated the CLI/server entry points plus `whale_detector.create_whale_trade()` so any manual or AI-driven whale trade also uses the guarded execution path (token validation, slippage, balance feedback) instead of inserting directly into the database.
- Autonomy now treats 9×+ volume/liquidity anomalies as tradeable whenever whale slots are free, journals both the accepted fills and blocked attempts, and records a `whale_trade_opened` event when a job succeeds so the new autop-run trading path is auditable in `logs/journal.jsonl`.
