# 2026-04-02 Paper Sizing Framework

## Source
- Daily-report follow-up dated 2026-04-02: evaluate larger paper sizes and higher bankroll utilization only after admission logic and trade/account state correctness were repaired.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/eb200/polymarket-scanner/AGENTS.md), especially Architecture, Never Do, Always Do, and Testing Changes.

## Findings
- The 2026-04-02 sizing idea should not be implemented directly on top of uncertain admission or accounting paths.
- The repo now has the prerequisite guardrails from the 2026-04-01 fixes:
  - centralized paper-open inspection in [db.py](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/db.py)
  - explicit paper trade-state separation in [execution.py](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/execution.py) and [db.py](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/db.py)
  - paper-account and strategy utilization visibility in [db.py](/Users/will/.cline/worktrees/e61dc/polymarket-scanner/db.py)
- What was still missing was a safe way to compare fixed sizing against confidence-aware sizing without silently changing paper execution behavior.

## Fixes Applied
- Added [paper_sizing.py](/Users/will/.cline/worktrees/eb200/polymarket-scanner/paper_sizing.py) as a paper-only sizing framework with:
  - strategy-specific fixed baselines for `cointegration` and `weather`
  - confidence-aware shadow recommendations using existing signal quality fields
  - explicit per-trade, per-strategy, and total bankroll-utilization caps
  - default `rollout_state=shadow` and `active_policy=fixed` so broader rollout stays disabled
  - explicit rollback policy back to `fixed`
- Added migration `013_paper_sizing_decisions` plus decision/query helpers in [db.py](/Users/will/.cline/worktrees/eb200/polymarket-scanner/db.py) so recent sizing recommendations and summaries are persisted and reviewable.
- Updated [autonomy.py](/Users/will/.cline/worktrees/eb200/polymarket-scanner/autonomy.py) so paper pairs and weather trades record sizing decisions only after the normal admission path has already succeeded, while execution remains on the selected paper policy.
- Updated [server.py](/Users/will/.cline/worktrees/eb200/polymarket-scanner/server.py) with `GET /api/paper-sizing` and extended stats to surface sizing observability.
- Added the dated rollout note [reviews/2026-04-02-paper-sizing-rollout-review.md](/Users/will/.cline/worktrees/eb200/polymarket-scanner/reviews/2026-04-02-paper-sizing-rollout-review.md) to document the recommendation before any broader rollout.
- Added a machine-checked activation gate in [paper_sizing.py](/Users/will/.cline/worktrees/eb200/polymarket-scanner/paper_sizing.py) so confidence-aware sizing cannot become active unless:
  - the dated review note exists
  - `signal_admission_stable` is explicitly acknowledged
  - `trade_state_accounting_stable` is explicitly acknowledged
- Added focused regression coverage in [tests/test_paper_sizing.py](/Users/will/.cline/worktrees/eb200/polymarket-scanner/tests/test_paper_sizing.py) for:
  - shadow mode keeping fixed execution while logging higher confidence-aware recommendations
  - paper-only rollback to fixed policy outside paper mode
  - confidence-aware sizing staying blocked until readiness acknowledgements are set
  - missing review-note path forcing rollback back to fixed sizing
  - sizing API visibility for operators

## Verification
- `python3 -m py_compile autonomy.py db.py server.py paper_sizing.py tests/test_paper_sizing.py`
- `python3 -m unittest tests.test_paper_sizing tests.test_trade_state_architecture tests.test_cointegration_admission`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, paper_sizing; print('OK')"`
