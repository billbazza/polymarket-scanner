# 2026-04-03 Cointegration Paper Execution

## Source
- Daily-report follow-up dated 2026-04-03: scanner produced 339 A+ cointegration signals but paper trading opened zero pairs trades, so operator confidence dropped and the strategy looked dead.  The same report pointed to the need to "verify filters, execution path, and paper/live mode gating." 
- Followed [AGENTS.md](AGENTS.md) guidance (Architecture, Scoring Pipeline, Autonomy, and Always Do rules).

## Problem
- Autonomy ran the optional brain validation regardless of mode, so paper-level scans fetched the brain provider, which frequently rejected otherwise tradeable signals and stamped them as blocked before execution.
- Paper mode should trust the math scoring filters and sizing logic when AI services are unavailable or disabled, since these runs are purely for evaluation/training and there is no external risk.

## Fixes Applied
- Updated `autonomy.py` so the brain validation step only runs for live trades; paper and scout levels now skip the provider gate and log that they are trusting the statistical filters.
- Preserved the existing rejection logging, so any future change to this gating still records whether a rejection occurred (even when live-only).

## Verification
- `python3 -m py_compile autonomy.py`
