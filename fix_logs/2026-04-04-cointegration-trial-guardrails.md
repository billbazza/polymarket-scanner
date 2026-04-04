# 2026-04-04 Cointegration Trial Guardrails

## Context
- Daily report flagged cointegration as underutilized despite thousands of A-grade signals, so we needed to widen the paper trial while keeping the audit trail tight before graduating any new cohort toward live trading.
- AGENTS/GEMINI/CLAUDE guidance specifically asked us to let a second filter miss through the A-grade trial and to surface the failing filter count for later analysis.

## Analysis & Changes
- Relaxed the default trial gate in `cointegration_trial.py` by lowering `min_z_abs`, `min_ev_pct`, and `min_liquidity`, raising `max_slippage_pct`, and adding a `max_allowed_failed_filters` knob so the trial can now admit two misses (kelly, momentum, spread std, or EV) as long as the failures stay inside the approved set.
- Annotated each opportunity with its trial filter metadata, added the new guardrail values to the published guardrail payload, and kept the failed-filter count in the result so downstream tooling can act on it.
- Autonomy now writes the filter count/list into the paper-trade-attempt details and records a `cointegration_trial_blocked` journal entry for every rejected Grade A signal; the entry includes the grade value, failed filters, and the allowed filter set so the operations team can gauge whether a signal was a 7/8 near miss or a 4/8 outlier before moving it live.

## Tests
- `python3 -m py_compile cointegration_trial.py autonomy.py`
- `python3 scan.py --top 3`
- `tail -n 20 logs/journal.jsonl`
