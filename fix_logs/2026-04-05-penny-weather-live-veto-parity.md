- Repo task dated 2026-04-05: enable threshold weather auto-trading in penny mode with paper-parity behavior, remove any residual scan-only/paper-only penny gate, and surface explicit live safeguard veto reasons.

- Findings:
  - Penny weather autonomy still depended on a scoped runtime default that resolved to `auto_trade_enabled=False` unless the operator had already toggled the penny runtime on, which left threshold weather in a de facto scan-only penny state despite the parity-first repo contract in `AGENTS.md`.
  - The weather phase summary counted opportunities and fills, but it did not aggregate per-trade live execution vetoes such as wallet/balance or slippage failures into the runtime/dashboard status, so penny operators could see "0 traded" without a clear live safeguard reason.

- Changes made:
  - Updated `db.get_autonomy_runtime_settings()` so trading runtimes default to `auto_trade_enabled=True`, keeping penny/book aligned with paper unless the operator explicitly disables the scoped runtime control.
  - Updated `autonomy.weather_phase_policy()` to treat the primary runtime control as parity-on by default and not as a penny weather scan-only fallback.
  - Extended the autonomy weather phase summary to record:
    - `result_counts.live_vetoed`
    - `live_safeguard_veto_count`
    - `live_safeguard_reason_counts`
    - sampled `live_safeguard_vetoes`
  - When live weather execution fails after tradeability/preflight passes, the phase now records the concrete `reason_code`/reason and summarizes the vetoes in the phase-level `reason`.
  - Updated `/api/autonomy/runtime` slot-limit strategy status to flag weather as `blocked` or `limited` when the last penny cycle hit explicit live safeguard vetoes, not only when slots were exhausted.
  - Updated the dashboard runtime summary text so the weather phase line calls out live safeguard veto counts and reason-code breakdowns.

- Result:
  - Threshold weather in penny mode now follows the same default admission path as paper.
  - Eligible penny weather trades execute by default unless an explicit per-trade live safeguard vetoes them.
  - Those vetoes are now visible in the autonomy runtime payload and dashboard summary instead of appearing as an unexplained lack of fills.

- Verification:
  - `python3 -m pytest tests/test_runtime_scope_split.py`
