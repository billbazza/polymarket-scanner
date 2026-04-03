# 2026-04-03 Confidence-based Sizing Rollout

## Source
- Daily report follow-up on 2026-04-03 ("Position sizing not utilizing confidence scoring") documented 53 shadow decisions that never affected fills. The AGENTS contract and the rule to cap Kelly at 0.25 provided the gating context for the rollout.

## Findings
- The shadow-mode paper-sizing framework was logging recommended sizes but execution never pulled those recommendations into the fill size or the confidence audit trail.
- Weather trade execution likewise skipped the sizing metadata so the confidence score and override never appeared in the live logs.
- Execution logs did not make it obvious when a paper-sizing recommendation was honored or when bank-roll limits forced a further cap.

## Fixes Applied
- `execution.execute_trade` now inspects the attached `paper_sizing` decision, overrides the requested USD size when confidence is applied, and enforces a 25% Kelly cap before checking balances. The result/return payload surfaces the confidence score, policy, and whether the quarter-Kelly cap trimmed the order.
- Weather trades now pass their `paper_sizing` decision to `execution.execute_weather_trade`, which mirrors the same confidence override logging and quarter-Kelly enforcement before opening a weather position.
- Both helper flows log the override/cap occurrences so the new rollout can be audited, and the journal entries capture the confidence metadata inside the trade audit trail.

## Verification
- `python3 -m py_compile execution.py autonomy.py`
- `python3 -m unittest tests.test_trade_state_architecture`
