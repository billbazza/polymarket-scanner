# 2026-04-04 Stage 2 Perplexity Validation

Historical stage label retained. Active policy treats this as metadata enrichment, not a penny-only gate.

## Summary
- Implemented cached Perplexity metadata so every scanned signal now carries a verdict, a confidence score, and fallback metadata needed to trace why a candidate passed or was blocked.
- Added a durable metadata column (`perplexity_json`) so the UI, journal, and downstream automation can show “profitable candidate” annotations without re-running Perplexity.
- Logged the fallback pathway when Perplexity is unavailable or returns errors.

## Changes
- `perplexity.py`: added JSON caching with TTL and `annotate_profitable_candidate()` so the verdict is attached to each opportunity before it becomes a signal. Fallbacks now emit structured metadata (`status`, `reason`, `context`) and a confidence score, and successful verdicts are logged once per signal to avoid repeated API calls.
- `brain.py`: now reuses the stored Perplexity verdict (re-running only if the cached status wasn’t `ok`), feeds the context into Claude/OpenAI, and records Perplexity fallbacks in the log stream for traceability.
- `db.py`: schema gained `perplexity_json`, `save_signal()` persists the verdict, and `_deserialize_signal_row()` exposes it so automation/journal readers can inspect the metadata.
- `cron_scan.py` / `server.py`: every signal is annotated with the new verdict before it is saved, so the metadata is always present in paper/live pipelines.
- Superseding note: cached Perplexity verdicts may inform operators or shared AI context, but they must not create an extra penny admission gate unless paper uses the same rule too.
- `tests/test_perplexity_validation.py`: new unit tests confirm the flows degrade to the disabled state when the API key is missing and that the annotation flow attaches the metadata.

## Testing
- `python3 -m pytest tests/test_perplexity_validation.py`
