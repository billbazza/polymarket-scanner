# 2026-04-04 Grade A Paper Promotion

## Context
- Cointegration paper trades were limited to the A+ cohort, leaving thousands of Grade A near-misses sitting on the sidelines.
- The latest autonomy run requested more detail about those fails so operators could tell whether a Grade A was a 7/8 near-miss or a lower-quality rejection.

## Changes
- Restricted the trial to only the soft momentum/spread_std filters and capped the misses at one so every promoted Grade A is no more than a single soft failure.
- Loosened the guardrails slightly (lower `min_liquidity`, higher `max_slippage_pct`, and a marginally softer `min_ev_pct`) so these near-A+ signals still publish workable guardrails without diluting the main tradeable cohort.
- Continued to keep `tradeable=True` tied strictly to the A+ math outcome while logging the guardrail tweaks and failed-filter counts for every A-grade attempt. Superseding note: the trial itself is no longer paper-only; see `fix_logs/2026-04-05-penny-a-grade-parity.md`.

## Signal counts
- Pre-change (from `sqlite3 scanner.db "SELECT grade_label, COUNT(*) FROM signals GROUP BY grade_label;"` run before the guardrail tweak):
  - A+: 341
  - A: 3522
- Post-change (after rerunning `python3 scan.py --top 3`):
  - A+: 341
  - A: 3522

## Verification
- `python3 -m py_compile cointegration_trial.py autonomy.py`
- `python3 scan.py --top 3`
- `sqlite3 scanner.db "SELECT grade_label, COUNT(*) FROM signals GROUP BY grade_label;"`
