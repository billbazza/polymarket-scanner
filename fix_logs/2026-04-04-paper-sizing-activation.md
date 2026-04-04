# 2026-04-04 Paper Sizing Activation

## Source
- Daily report follow-up dated 2026-04-04: paper sizing decisions still mostly shadow (87 of 89 trades), so the recorded fills don’t show whether the confidence gate could have applied.

## Findings
- Confidence recommendations were being computed and stored, but execution always saw `applied=False`, so the actual fills kept the fixed baselines and the audit trail never captured the gating status for the majority of trades.
- The quarter-Kelly cap was enforced at execution time, but the telemetry did not expose whether the paper gate allowed confidence sizing, which made it hard to prove the 97.8% shadow rate was an intentional gate.

## Fixes Applied
- `_apply_confidence_sizing` now always returns metadata describing the activation gate, requested policy, and selected sizes, even when the decision stays in fallback mode, so every fill can be traced back to its gating outcome.
- Execution helpers propagate the gating metadata through the paper and live fill payloads and the journal entries, and the quarter-Kelly ceiling remains in place while the returned payload now includes the comparison between baseline and confidence sizes plus the gate blockers.

## Verification
- `python3 -m py_compile execution.py autonomy.py`
