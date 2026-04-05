## Summary

- audited scope propagation across autonomy, execution, DB guards, weather preflight, manual APIs, and dashboard gate logs to keep paper and penny policy lanes isolated.
- fixed a concrete audit-trail bug where pairs DB guard logs could label a penny-scoped block as `Paper pairs trade blocked`, which made live-policy vetoes appear inside the paper lane.
- threaded explicit `runtime_scope` through pairs/weather execution entry points, added `blocker_source` / `blocker_runtime_scope` / `blocker_strategy` metadata to scoped decisions and failures, and surfaced those fields in manual API responses plus the dashboard gate table.

## Behavioral Changes

- paper and penny blocker logs now name both the active `runtime_scope` and the exact blocker lane, for example `paper-cointegration`, `penny-weather`, or `shared-external`.
- pairs runtime-status annotations now expose scoped preflight blocker metadata so UI/runtime consumers can see the active lane instead of inferring it from generic reason text.
- autonomy/manual trade-path logs now include blocker source fields for weather and pairs skips/vetoes, making cross-policy contamination immediately visible if it ever regresses.

## Verification

- added regression coverage for scoped pairs blocker metadata, scoped API blocker responses, and the dashboard gate table showing blocker-source/runtime-scope context.
