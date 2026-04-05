# 2026-04-05 Penny Strategy Capital Reconciliation

## Source
- Operator request dated 2026-04-05: restore penny portfolio/stat scoping so penny mode excludes paper trades and paper aggregates completely.
- Followed `AGENTS.md` Always Do and Dashboard/runtime semantics rules: keep audit logging intact, log the fix in `fix_logs/`, and sync `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` when reporting semantics change.

## Findings
- Core penny trade/history/account totals were already runtime-scoped, but the strategy breakdown still exposed paper-oriented `committed_capital` as the dashboard’s live-exposure field.
- For penny-scoped live or wallet-attached positions, `committed_capital` stays at `0.0` by design while `external_capital` carries the real deployed exposure, so the penny strategy table could show zero live exposure even when the penny account card reported deployed capital correctly.
- That mismatch is a reporting correctness bug because penny mode must reconcile all visible stats to the penny/live ledger only.

## Fixes Applied
- Updated `db.get_strategy_performance()` to publish a canonical scoped `reporting_capital` field:
  - paper scope uses `committed_capital`
  - penny scope uses `external_capital`
- Added `reporting_capital_basis` plus top-level `total_reporting_capital` so API consumers can verify which capital basis the scoped strategy summary is using.
- Updated `dashboard.html` to render penny strategy exposure from `reporting_capital` instead of paper-only `committed_capital`, and to label penny strategy state with live/wallet-open counts instead of implying exchange-only exposure.
- Synced `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` so the repo contract now states that penny strategy breakdowns must report live/wallet deployed capital rather than paper placeholders.

## Acceptance Checks
- `GET /api/stats?runtime_scope=penny` must return strategy rows whose `reporting_capital` matches penny live/wallet exposure, not paper committed-capital fields.
- Penny dashboard strategy rows must show the same deployed-capital basis as the penny account summary.
- Paper strategy rows must remain unchanged and continue to report `committed_capital` / bankroll utilization.

## Verification
- `python3 -m pytest tests/test_runtime_scope_split.py tests/test_strategy_performance.py -q`
- `python3 -m py_compile db.py server.py`
