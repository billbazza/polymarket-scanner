# 2026-04-04 Stage 3 Perplexity Gating

Historical record. The gate described here is superseded by the parity-first repo contract.

## Summary
- This file records the earlier attempt to use cached Perplexity verdicts as a live promotion gate.
- Active policy no longer permits a separate Perplexity-defined live bucket for penny. The cached verdict and `profitable_candidate_feature` fields remain useful as dashboard/audit metadata only.
- Dashboard operators may still filter or inspect these fields, but penny admission must not be narrower than paper because of them.

## Changes
- `dashboard.html`: added the historical Stage 3 filter checkbox, badge column, and UI preference persistence so operators can surface profitable candidate features.
- `db.py`: expose `profitable_candidate_feature`, `profitable_candidate_reason`, `perplexity_status`, and `perplexity_confidence` when deserializing signals so UI widgets can render the cached verdict without re-running the AI layer.
- Superseding note: any code/docs that still say "block non-profitable candidates when not in paper mode" are stale and must be rewritten to parity-safe wording.

## Testing
- `python3 -m pytest tests/test_perplexity_validation.py`
