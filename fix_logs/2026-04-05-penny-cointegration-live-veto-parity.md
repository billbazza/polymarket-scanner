# 2026-04-05 Penny Cointegration Live-Veto Parity

- Removed the legacy penny-only cointegration Perplexity Stage 3 admission gate from [`autonomy.py`](/Users/will/.cline/worktrees/4bb8b/polymarket-scanner/autonomy.py) so penny follows the same signal-admission path as paper.
- Converted live cointegration brain checks from a reject gate into advisory-only telemetry, preserving parity while still capturing operator context in the journal.
- Added structured `pairs_phase.live_vetoes` / `latest_live_veto` runtime reporting so the last penny autonomy cycle exposes the exact live safeguard `reason_code` and reason when execution is blocked.
- Promoted penny live safeguard vetoes into explicit `live_trade_veto` journal entries and warning logs with signal id, event, and veto reason.
- Kept the runtime gate table visible in penny mode in [`dashboard.html`](/Users/will/.cline/worktrees/4bb8b/polymarket-scanner/dashboard.html) and surfaced the last cointegration live veto in the runtime panel so blocked A+ penny signals are explainable in the UI without tailing logs.
