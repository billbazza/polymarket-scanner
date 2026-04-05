# 2026-04-05 Weather Stop-Loss Review

## Scope
- Investigate recurring weather stop-losses and reduce the frequent `-$4/-$5` losses without changing any weather entry thresholds.
- Verify whether the current losses come from policy width, close-loop latency, repeated exposure, or missing diagnostics.

## Evidence
- Closed weather performance remains positive overall in the current DB snapshot: `80` closed trades, `73.8%` win rate, `+$390.89` total PnL, `+$4.89` average PnL per close.
- The stop-loss cohort is concentrated in a small set of recent trades, but the recent losses are larger than the earlier cluster:
  - `448` Denver 2026-04-09, `96h` horizon, `-$8.64`
  - `431` San Francisco 2026-04-04, `24h` horizon, `-$8.45`
  - `433` Chicago 2026-04-04, `24h` horizon, `-$11.71`
  - earlier April 1-3 stops remain in the `-$3.27` to `-$5.18` range
- Horizon breakdown of closed stop-loss trades:
  - `0-24h`: `4` stops, `-$30.36` total, `-$7.59` average
  - `25-48h`: `3` stops, `-$13.04` total, `-$4.35` average
  - `49-72h`: `2` stops, `-$9.53` total, `-$4.77` average
  - `73h+`: `1` stop, `-$8.64` total, `-$8.64` average
- Repeat city/date groupings in the closed stop sample:
  - Atlanta 2026-04-03: `2` stops, `-$10.34`
  - Denver 2026-04-04: `2` stops, `-$8.42`
- Structured stop events were present in `logs/journal.jsonl`, including `weather_stop_loss` entries for the April 4-5 losses, but the standalone `weather-stop-contexts.jsonl` diagnostics file was not present in this worktree before the fix.

## Conclusion
- The weather strategy is still profitable overall, but the active `18%` stop was looser than the documented supported policy and was not containing the newer gap-through losses.
- The worst recent stop-outs are still concentrated in shorter-horizon entries, especially `0-24h`, but this review does not change any weather entry threshold.
- The correct immediate mitigation is:
  - restore the supported `15%` stop policy
  - improve stop diagnostics so gap-through vs direct floor touches are explicit
  - leave weather entry thresholds unchanged for now
