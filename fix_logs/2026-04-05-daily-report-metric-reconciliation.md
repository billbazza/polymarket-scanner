# 2026-04-05 Daily Report Metric Reconciliation

## Source
- Daily-report cleanup requested on 2026-04-05: reconcile headline metrics in `reports/2026-04-05-daily-report.md`, remove speculative wording, and enforce the repo's routing/formatting rules from `AGENTS.md`.

## Changes Applied
- Rewrote `reports/2026-04-05-daily-report.md` from one `db.get_paper_account_overview()` snapshot so the summary now names:
  - paper-account realized PnL
  - unrealized PnL
  - total equity
  - committed capital / bankroll utilization
  - strategy-level realized vs net PnL and closed/open/total trade counts
- Removed ambiguous relative-date wording and replaced it with explicit `2026-04-05` references where follow-up timing mattered.
- Routed unresolved `Not Working` items to the correct sinks:
  - weather stop-loss review stays in `reports/diagnostics/2026-04-05-weather-stop-loss-review.md`
  - cointegration underutilization/rejection follow-up stays in `fix_logs/2026-04-04-cointegration-trial-guardrails.md` and `fix_logs/2026-04-04-filter-failure-visibility.md`
- Updated `server.py` so rendered daily reports use checkbox bullets consistently in `Working`, `Not Working`, and `Top 5 Improvements`, including the empty-state fallback rows.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` so the workflow now explicitly requires one-snapshot metric sourcing plus explicit realized/net and closed/open/total labels.

## Verification
- `python3 - <<'PY'` with `db.get_paper_account_overview(False)` to capture the reconciliation snapshot used in the report rewrite
- `python3 -m py_compile server.py`
