# 2026-04-03 Stage 2 Polygon Gating

Historical stage label retained. Active policy treats this as optional audit instrumentation, not an extra penny admission gate.

## Summary
- When `STAGE2_POLYGON_GATING` is enabled, `execution.execute_trade()` snapshots Polygon block metadata (block number, base fee, gas used) and chain parity before paper trades are sent so each attempt has richer audit context.
- Paper-mode slippage instrumentation now runs on both legs and stores the results (slippage %, orderbook depth) alongside the block snapshot, and the returned `stage2_context` follows the trade record into `autonomy.record_attempt()` so the audit trail can show exactly which Polygon state gated the decision.
- `blockchain.capture_polygon_rollout()` wraps the RPC calls used by the deployment plan so runs can fetch the latest block and chain ID without requiring `web3`, and `paper_trade_attempts.details_json` now carries a `stage2_polygon` payload for every trade attempt while the env flag is active.
- Superseding note: this flag may enrich logs and attempt records, but it must not be used to keep penny narrower than paper.

## Testing
- Run a stage 2 paper cycle with `STAGE2_POLYGON_GATING=1`: look for `Stage 2 polygon rollout: block #...` log lines before execution and verify `paper_trade_attempts.details_json` includes the new `stage2_polygon` sub-document.
- Confirm the slippage gate entries now include `slippage_a` and `slippage_b` when the flag is set by querying `db.get_paper_trade_attempts()` or checking the journal in `logs/journal.jsonl` after trades.

## Follow-up
- Perplexity or other metadata layers can reuse the `stage2_polygon` payload for observability, but not as a penny-only promotion requirement.
