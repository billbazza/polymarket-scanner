# 2026-04-04 Stage 2 Perplexity Validation

## Summary
- Implemented the Stage 2 Perplexity validation plan so every scanned signal now carries a cached verdict, a confidence score, and the fallback metadata needed to trace why a candidate passed or was blocked.
- Added a durable metadata column (`perplexity_json`) so the UI, journal, and downstream automation can tag “profitable candidate” features without re-running Perplexity.
- Logged the fallback pathway when Perplexity is unavailable or returns errors, satisfying the plan’s recommendation to keep the audit trail clear before we promote a signal into the Stage 2/3 bucket.

## Changes
- `perplexity.py`: added JSON caching with TTL, a dedicated Stage 2 evaluation prompt, and `annotate_profitable_candidate()` so the verdict is attached to each opportunity before it becomes a signal. Fallbacks now emit structured metadata (`status`, `reason`, `context`) and a confidence score, and successful verdicts are logged once per signal to avoid repeated API calls.
- `brain.py`: now reuses the stored Perplexity verdict (re-running only if the cached status wasn’t `ok`), feeds the context into Claude/OpenAI, and records Perplexity fallbacks in the log stream for traceability.
- `db.py`: schema gained `perplexity_json`, `save_signal()` persists the verdict, and `_deserialize_signal_row()` exposes it so automation/journal readers can inspect the Stage 2 tag.
- `cron_scan.py` / `server.py`: every signal is annotated with the new verdict before it is saved, so the Stage 2 tag is always present in paper/live pipelines.
- `tests/test_perplexity_validation.py`: new unit tests confirm the flows degrade to the disabled state when the API key is missing and that the annotation flow attaches the metadata.

## Testing
- `python3 -m pytest tests/test_perplexity_validation.py`
