# Stage 3 Live Readiness Checklist

Generated: 2026-04-03

## Purpose
Stage 3 unlocks the $1–5 live exposure described in `autonomy.py`’s `penny`/`book` levels once the Stage 2 paper testing has proven the scanning, scoring, and Perplexity gates. This checklist ensures the autonomy risk gating stays intact by forcing every readiness condition to pass before any live dollar is committed.

## Readiness Items
1. **Balance confirmation** – the `POLYMARKET_PRIVATE_KEY` wallet must exist, be unlocked via `execution.fetch_wallet_balance()` (or the equivalent `blockchain.py` helper), and show a positive USD-equivalent balance large enough to buffer the small exposures (~$25 minimum) while still capping total live risk at $1–5 per trade.
2. **Slippage gate** – every candidate trade runs through `math_engine.check_slippage()` (≤2% slippage) and the Polygon-derived liquidity view used by the scan layer must agree with the REST data to prevent false passes. Any signal that fails this check is recorded in `logs/scanner.log` and rejected before sizing.
3. **Quarter-Kelly cap** – sizing helpers (e.g., `paper_sizing` and the autonomy-level position sizing pipeline) must cap the Kelly fraction at 0.25 and compute trade sizes that fall inside the $1–5 live window; no stage 3 fill may exceed 0.25 Kelly even if confidence is higher.
4. **Runtime keys** – `POLYMARKET_PRIVATE_KEY` and `ALCHEMY_API_KEY` must be present in the macOS Keychain service used by `runtime_config.py` (or be injected as explicit per-process env overrides) and validated via `blockchain.ping()`/`execution.fetch_wallet_balance()` before `autonomy.py` escalates beyond `paper` mode. If either key is missing or invalid, stage 3 stays disabled and a `missing_live_key` warning is emitted.
5. **Live-book controls + reporting** – penny must stay behind explicit operator controls (`auto_trade_enabled`, scoped `max_open_override`) and every live trade must persist order ids, fees, and execution metadata strongly enough for `/api/reporting/hmrc` and `logs/hmrc_audit.jsonl` to reconstruct the live book without reading any paper trades.

## Autonomy Integration
- At the end of each autonomy cycle, if all readiness items pass and stage 2 telemetry shows improved win rates, stage 3 allows `autonomy.py` to fire `penny`-level (real $1–5) fills before graduating to `book` (Kelly-sized) trades.
- The readiness checklist must be logged (e.g., via `logs/journal.jsonl`) with the timestamped gate status so we can trace why a live trade was allowed or held.
- Operator reviews should also confirm that the penny dashboard controls and `/api/reporting/hmrc` agree with the scoped trade ledger before max-open or size overrides are raised.
- Manual audits should cross-reference this document with the `reports/2026-04-03-daily-report.md` Kanban task to confirm the readiness documentation is current.
