# 2026-04-03 Stage 2/3 Live-Test Plan

## Context
- Daily-report follow-up item asked for a concrete stage 2/3 rollout plan that ties Polygon execution data and the Perplexity-driven validation layer to the profitable feature set (per AGENTS.md guidance to keep live experiments lean and focused on clearly winning strategies).
- The plan needs to capture both the integration outline and the paper-to-live test matrix, then be surfaced in reports/fix logs so the Daily Report stays consistent with how we track improvements.

## Polygon Integration Outline
- **On-chain parity checks:** Use the existing `blockchain.py` / `execution.py` surface to pull Polygon block height, gas, and transaction status before attempting fills; map the `py-clob-client` order lifecycle back into `execution` so we always log `send_tx`, `confirmation`, and `fill` events with polygon block metadata.
- **Market state sync:** Layer in a new `polygon` scan helper that mirrors the market data already collected from the Polymarket API so we know liquidity/depth directly from the chain and can gate trades by the 2% slippage ceiling per `math_engine.check_slippage()`.
- **Live trade gating:** Add hooks into `autonomy.py` so stage 3 live trades only fire after the readiness checklist (balance verification, slippage scan, AI validation) is green and `POLYMARKET_PRIVATE_KEY` + `ALCHEMY_API_KEY` are configured in `.env`.

## Perplexity API Integration Outline
- **Model-aware validation:** Extend `brain.py` (or add a new helper) to call `perplexity.py` for probability estimates that complement Anthropic/OpenAI; cache responses so repeated due diligence on the same signal picks up the last verdict and avoids rate-limit fines.
- **Feature gating:** Use Perplexity output (and optionally the new UI) to annotate which features are “profitable candidates” (high confidence, positive EV after the Polygon slippage check). Only those annotated features get promoted into the stage 2/3 test bucket.
- **Fallback/resilience:** Add try/except around `perplexity.py` so we gracefully degrade to the stacked `BRAIN_PROVIDER` sequence mentioned in AGENTS.md; log the absence of Perplexity validation as part of the signal metadata so we can trace why a feature was blocked.

## Requirements & Risks
1. **Requirements**
   - `.env` entries for `ALCHEMY_API_KEY`, `POLYMARKET_PRIVATE_KEY`, and a new `PERPLEXITY_API_KEY`; confirm `perplexity.py` still supports key + base URL overrides and document them in `README`/`AGENTS.md` (per the “always keep AGENTS/GEMINI/CLAUDE in sync” rule if we touch the logging process).
   - UI filter that only surfaces “profitable features” identified by the scoring + Perplexity tag, to avoid overwhelming stage-2 human reviewers with low-quality signals.
   - Monitoring on `logs/scanner.log` and `logs/journal.jsonl` for new Polygon/perplexity flags so we can trace why a signal passed or failed each gate.
2. **Risks**
   - Polygon RPC throttling or gas spikes could delay execution; mitigate by caching the health of `ALCHEMY_API_KEY` and refusing to trade if the node reports >1s latency.
   - Perplexity rate limits or availability could block validation; log the fallback path and keep `brain.validate_signal()` tolerant of missing AI verdicts.
- Live money risk: stage 3 must cap at $1-5 live bets (per autonomy leveling) until the metrics prove the live link; stage 2 should stay in paper with paper sizing capped by the confidence-aware framework documented in `fix_logs/2026-04-03-confidence-aware-paper-sizing.md` and our other sizing discussions.
   - Data drift between polygon-derived liquidity & Polymarket REST data might flag false slippage; add reconciliation job before enabling live fills.

## Test Matrix (Paper → Live Progression)
1. **Stage 2 Paper — Polygon Data Only**
   - Scope: Feed Polygon-derived liquidity, slippage, and block confirmations into the scanner and paper execution; keep trading disabled for paperwork only.
   - Conditions: Balanced test dataset (existing profitable signals) with Polygon health checks; `math_engine.check_slippage()` must pass on every trade.
   - Success: Signals that previously hung at paper stage now log block metadata and pass the new risk checklist.
2. **Stage 2+ Paper — Add Perplexity Validation**
   - Scope: Add the Perplexity verdict before any AI validation gate; only signals with positive Perplexity scores are promoted to the “profitable feature” label.
   - Conditions: Validate side-by-side with Anthropic/OpenAI fallback; record the Perplexity verdict as part of the signal metadata for audit.
   - Success: Clear evidence that Perplexity improves the accuracy of profitable indicators (fewer false positives vs a baseline without Perplexity) before we trust live automation.
3. **Stage 3 Live — Limited Exposure**
   - Scope: Enable live fills (always GTC) once Stage 2 paper results satisfy the profitability + slippage thresholds; cap Kelly fraction at ≤0.25 and limit trades to $1-5 per open position.
   - Conditions: Run via `autonomy.py` at “penny” level → `book` level only after at least three successful Stage 2 paper runs; confirm `POLYMARKET_PRIVATE_KEY` balance, allowed slippage, and Perplexity verdict per trade.
   - Success: Live trades execute smoothly with logged Polygon confirmations and expect less than 2% slippage; feed results back into the reporting/daily logs so the next Daily Report can move stage 3 items to “Working.”

## Follow-up
- Tie this plan to the Daily Report so the “Top 5 Improvements” item has a concrete reference, and keep tracking progress via `reports/2026-04-03-daily-report.md` and this fix log entry.
