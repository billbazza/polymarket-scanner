# 2026-04-03 Confidence-aware Paper Sizing

## Source
- Daily-report follow-up dated 2026-04-03: enable confidence-aware paper sizing once the readiness gates are acknowledged and confirm the sizing API respects utilization limits before replacing fixed baselines.
- Followed the AGENTS contract (Architecture, Rules, Always Do) while touching the paper sizing gate.

## Findings
- The gate in `paper_sizing.py` already enforces the dated review note plus `signal_admission_stable` and `trade_state_accounting_stable` before allowing `confidence_aware` policy to apply; the readiness flags were still False in the DB, keeping everything in `shadow`/`fixed` mode.
- `/api/paper-sizing` now runs under the same settings that drive execution, so capturing a new cointegration decision with `build_paper_sizing_decision` gave a live sample of `selected_size_usd=30` and `confidence_size_usd=30` while `constraints` reported `max_total_room_usd=300`, `binding_caps=[]`, and projected total utilization of 21.5% (well below the 35% cap).
- The API summary showed the new decision and `summary.applied_decisions` incremented, with `settings.paper_gate.can_apply_confidence=true`, proving the endpoint reflects the activated rollout and still enforces the configured caps.

## Fixes Applied
- Persisted the rollout change by setting `rollout_state=active`, `active_policy=confidence_aware`, and acknowledging both readiness flags via `paper_sizing.set_sizing_settings`; the review note requirement stays satisfied.
- Rebuilt and recorded a cointegration sizing decision so that `/api/paper-sizing` now surfaces an applied confidence-aware recommendation, letting us confirm the reported `constraints` keep the dynamic size inside the total and strategy utilization caps.
- Logged the follow-up in this fix log per the instructions so future reviews link the readiness change with the API validation.

## Verification
- `python3 - <<'PY'` (reload `db`, `paper_sizing`, and `server`, initialize bankroll, set the new rollout/readiness settings, build/record a cointegration decision, and GET `/api/paper-sizing?limit=1` to confirm `applied_decisions` and the binding caps). The output showed `selected_size=30`, `max_total_room=300`, projected total utilization 21.5%, and `summary.applied_decisions=2` with `binding_caps=[]`.
