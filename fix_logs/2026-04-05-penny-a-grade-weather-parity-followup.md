# 2026-04-05 Penny A-Grade + Weather Parity Follow-Up

## Context
- Penny still had a live-only post-admission filter in `autonomy.py`: Perplexity metadata (`profitable_candidate_feature`) could remove an otherwise admitted cointegration signal before execution.
- That violated the repo parity contract in `AGENTS.md`: if paper admits the opportunity, penny must attempt the same default path unless a concrete per-trade live safeguard blocks it.
- Weather threshold opportunities were already on the parity path; the only approved live-only weather exclusion remains the explicit `exact_temp_paper_only` veto.

## Changes
- Removed the penny/book `stage3` Perplexity admission filter from `autonomy.py`; Perplexity annotations now remain observability-only and are journaled without blocking execution.
- Kept explicit live-only vetoes intact and operator-visible: brain rejection, wallet/balance failure, slippage failure, exchange/order rejection, missing market metadata, and the exact-temperature weather live block still stop trades individually with structured reasons.
- Added a penny regression test proving an A-grade cointegration trial signal still reaches live execution even when Perplexity marks it as not-profitable.

## Verification
- `python3 -m unittest tests.test_cointegration_trial tests.test_runtime_scope_split`
- `python3 -m py_compile autonomy.py`
