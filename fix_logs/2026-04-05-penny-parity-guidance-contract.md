# 2026-04-05 Penny Parity Guidance Contract

## Context
- Operator request dated 2026-04-05: penny parity with paper is the governing rule. Penny should match paper strategy admission/execution behavior by default, with only explicit per-trade live safeguards allowed as vetoes.
- The repo guidance had drifted into staged-live language: Stage 2/3 Perplexity gating, weather rollout/scan-only wording, paper-only A-grade trial references, and live-readiness prose that encouraged agents to preserve extra penny restrictions.

## Changes
- Rewrote `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` in sync so the repo contract now states:
  - paper is the behavioral reference lane for strategy admission/execution
  - penny/book must mirror that default behavior
  - only explicit live safeguards such as wallet/balance verification, slippage, market metadata failure, and exchange/order failure may veto a penny trade
  - stage labels, cached Perplexity verdicts, profitable-candidate tags, and Polygon audit payloads are observability artifacts, not extra penny-only blockers
- Reworked the historical Stage 3 checklist in `reports/2026-04-04-stage3-live-readiness.md` into a parity-first live safeguards checklist while keeping the filename for continuity.
- Updated legacy guidance/report files that still taught staged live gating or paper-only penny behavior:
  - `fix_logs/2026-04-05-penny-weather-phase-runtime-visibility.md`
  - `fix_logs/2026-04-05-penny-weather-live-rollout-scope.md`
  - `fix_logs/2026-04-04-stage2-perplexity-validation.md`
  - `fix_logs/2026-04-04-stage3-perplexity-gating.md`
  - `fix_logs/2026-04-03-stage2-polygon-gating.md`
  - `fix_logs/2026-04-03-stage2-3-live-tests.md`
  - `fix_logs/2026-04-04-grade-a-paper-promotion.md`
  - `fix_logs/2026-04-01-a-grade-cointegration-paper-trial.md`
  - `reports/diagnostics/2026-04-01-a-grade-cointegration-paper-trial-review.md`
  - `reports/2026-04-03-daily-report.md`
  - `guides/scoring.md`

## Active Contract
- Do not reintroduce penny-only rollout gates, scan-only defaults, or paper-only admission paths for strategies that already run in paper.
- If a cached AI verdict, stage label, or audit payload is used for penny admission, the same rule must also be part of the paper default path; otherwise it is stale and must be removed or downgraded to telemetry.
- Keep the one explicit exception labeled as an exception when documented. As of this update, exact-temperature weather remains the documented paper-only live exception.

## Verification
- `diff -q AGENTS.md CLAUDE.md`
- `diff -q AGENTS.md GEMINI.md`
- `rg -n "Stage 3 readiness now depends|only profitable candidate features reach the live bucket|paper-only A-grade|scan-only by default|weather auto-trading is paper-only outside the research runtime" AGENTS.md CLAUDE.md GEMINI.md fix_logs reports guides`
