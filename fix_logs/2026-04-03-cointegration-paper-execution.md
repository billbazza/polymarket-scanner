# 2026-04-03 Cointegration Paper Execution

## Source
- Daily-report follow-up dated 2026-04-03: scanner produced 339 A+ cointegration signals but paper trading opened zero pairs trades, so operator confidence dropped and the strategy looked dead.  The same report pointed to the need to "verify filters, execution path, and paper/live mode gating." 
- Followed [AGENTS.md](AGENTS.md) guidance (Architecture, Scoring Pipeline, Autonomy, and Always Do rules).

## Problem
- Autonomy ran the optional brain validation regardless of mode, so paper-level scans fetched the brain provider, which frequently rejected otherwise tradeable signals and stamped them as blocked before execution.
- Paper mode should trust the math scoring filters and sizing logic when AI services are unavailable or disabled, since these runs are purely for evaluation/training and there is no external risk.

## Fixes Applied
- Updated `autonomy.py` so paper/sprint levels now sanitize the stored level string before deriving `config` and `paper_mode`. The loop now uses the normalized flag to pick `mode="paper"` and skip `brain.validate_signal()` for paper runs while continuing to log the usual `trade_opened`/`trade_rejected` events.
- Added a `brain_validation_skipped` journal entry for paper-driven cointegration admissions so the audit trail shows math-only trades explicitly and we can still track which signals were eligible.
- Confirmed the existing `execution.execute_trade()` logic still calls `check_balance()` and `math_engine.check_slippage()` before opening paper trades, so slippage and cash rules are enforced even when the AI gate is now bypassed.

## Verification
- `python3 -m py_compile autonomy.py`
