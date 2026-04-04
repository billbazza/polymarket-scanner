# 2026-04-04 cointegration A-trial guardrail expansion

- lowered the trial defaults (1.6 |z|, 0.35% EV, $10K liquidity, 1.25% slippage) so the backlog of 3,427 pending grade-A signals can see the updated thresholds and a clear admission path.
- allowed up to two soft filter misses (`ev_pass`, `momentum_pass`, `spread_std_pass`, `kelly_pass`) and now expose a `blocker_context` with every failure count, disallowed filters, and required guardrail so the audit trail shows exactly what each signal needs to clear.
- autonomy journaling now records `filters_failed`, `failed_filter_count`, `blocker_context`, and the trial guardrails for each blocked signal so operators can spot whether an event needs more liquidity, tighter slippage, or a fresh filter run.
