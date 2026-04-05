# 2026-04-05 Penny A-Grade Parity

## Context
- Penny autonomy was still rejecting grade-A cointegration trial candidates with a `paper_only` reason, so penny was seeing a narrower opportunity set than paper.
- That violated the operator requirement that penny act as a live-parity test of paper strategy behavior, with only explicit live safeguards allowed to veto individual trades.

## Changes
- Removed the paper-only mode gate from `cointegration_trial.py` so eligible grade-A cointegration signals can flow through the same A-trial guardrails in paper and penny/book.
- Renamed the active admission path to `a_grade_trial`, kept legacy `paper_a_trial` reads in summaries for historical continuity, and renamed the active experiment label to `cointegration_a_grade_parity_trial`.
- Applied the grade-weighted A-trial sizing path in live autonomy as well as paper so admitted grade-A entries keep the same reduced-size behavior across runtimes.
- Expanded strategy-admission audit logging in `autonomy.py` so penny/book now emit the same blocked-at-admission records and `cointegration_trial_blocked` journal entries that paper already produced.
- Kept explicit live safeguards intact: wallet/balance checks, execution/slippage vetoes, exchange/order failures, and other concrete live preflight/runtime blockers still veto trades individually and must surface their reasons explicitly. Historical Stage 3/Perplexity-only gating is no longer part of the active policy.

## Verification
- `python3 -m unittest tests.test_cointegration_trial tests.test_runtime_scope_split`
- `python3 -m py_compile cointegration_trial.py autonomy.py db.py server.py`
