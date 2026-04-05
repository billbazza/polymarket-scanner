# Penny Live Safeguards Checklist

Historical filename retained from the earlier Stage 3 rollout plan. Active repo policy is now penny-paper parity by default.

Generated: 2026-04-03

## Purpose
This checklist documents the explicit live safeguards that may veto a penny/book trade. It does not create a separate promotion stage, live bucket, or paper-to-live graduation rule. If paper would admit the strategy/opportunity, penny should attempt the same path by default unless one of the concrete safeguards below fails on that trade.

## Readiness Items
1. **Wallet and balance verification** – the `POLYMARKET_PRIVATE_KEY` wallet must exist, be unlocked via `execution.fetch_wallet_balance()` (or the equivalent `blockchain.py` helper), and show sufficient verified cash for the attempted fill. If wallet verification fails, penny fails closed.
2. **Slippage gate** – every candidate trade runs through `math_engine.check_slippage()` (≤2% slippage). Any signal that fails this check is recorded in `logs/scanner.log` and rejected before sizing.
3. **Quarter-Kelly cap** – sizing helpers must cap the Kelly fraction at 0.25 and compute trade sizes that fit the active penny/book exposure policy.
4. **Runtime keys and market preflight** – `POLYMARKET_PRIVATE_KEY` and `ALCHEMY_API_KEY` must be present in the macOS Keychain service used by `runtime_config.py` (or be injected as explicit per-process env overrides), and order submission must have Polymarket CLOB level-2 credentials either explicitly (`POLYMARKET_CLOB_API_KEY` / `POLYMARKET_CLOB_API_SECRET` / `POLYMARKET_CLOB_API_PASSPHRASE`) or derivable from the wallet key at runtime. If wallet, market, or CLOB auth preflight fails, the trade is vetoed and logged with the concrete blocker.
5. **Live-book controls + reporting** – penny must stay behind explicit operator controls (`auto_trade_enabled`, scoped `max_open_override`) and every live trade must persist order ids, fees, and execution metadata strongly enough for `/api/reporting/hmrc` and `logs/hmrc_audit.jsonl` to reconstruct the live book without reading any paper trades.

## Autonomy Integration
- Each autonomy cycle should treat these items as per-trade safeguards, not as a separate stage gate.
- The safeguard outcomes must be logged (for example via `logs/journal.jsonl`) with the timestamped veto/allow reason so we can trace why a live trade was allowed or blocked.
- Operator reviews should confirm that the penny dashboard controls and `/api/reporting/hmrc` agree with the scoped trade ledger before max-open or size overrides are raised.
- Historical references to "Stage 2" or "Stage 3" in older reports/fix logs are archived planning terminology only.
