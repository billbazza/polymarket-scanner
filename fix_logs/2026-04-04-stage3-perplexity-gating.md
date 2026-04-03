# 2026-04-04 Stage 3 Perplexity Gating

## Summary
- Wired Stage 3 readiness to cached Perplexity verdicts so only profitable candidate features reach the live bucket.
- Dashboard operators can now filter for Stage 3-ready signals and see the Perplexity verdict badge while the info panel notes when the filter is on.
- Scan journals, gate logs, and persistence now expose the `profitable_candidate_feature` flag so the automation and UI can rely on the cached verdict without re-running Perplexity.

## Changes
- `autonomy.py`: annotate every opportunity with the cached verdict, block non-profitable candidates when not in paper mode, log the stage-3 gate decisions, and record the gate rejection in the paper-trade attempt feed for auditability.
- `dashboard.html`: added the Stage 3 filter checkbox, badge column, and UI preference persistence so operators can surface only profitable candidate features; the info row now notes when the Stage 3 filter is active.
- `db.py`: expose `profitable_candidate_feature`, `profitable_candidate_reason`, `perplexity_status`, and `perplexity_confidence` when deserializing signals so UI widgets can render the cached verdict without re-running the AI layer.

## Testing
- `python3 -m pytest tests/test_perplexity_validation.py`
