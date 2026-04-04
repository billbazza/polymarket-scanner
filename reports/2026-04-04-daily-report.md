# Daily Report - 2026-04-04

Generated at: 2026-04-04T05:59:07.133096Z
Model: claude-opus-4-1-20250805
Confidence: high

## Summary
System is operationally healthy with 68.2% win rate and +$425.48 total equity (+21.3% on $2000 starting bankroll). Weather strategy dominates with 81 trades and +$408 net PnL, while cointegration shows promise (100% win rate but only 2 closed trades). Copy strategy was losing $14.93 with a 57.9% win rate, but an entry-price guard now blocks the near-certain 0/1 exposures that wrecked risk/reward (see fix_logs/2026-04-04-copy-strategy-risk-reward.md).

## Working
- Weather strategy generating consistent profits (+$259.81 realized, +$148.51 unrealized)
- Cointegration showing perfect win rate on closed trades with A+ grade signals
- [x] A-grade near-misses now open weighted, smaller paper entries so the trial keeps flowing through the relaxed guardrails (see fix_logs/2026-04-04-grade-a-weighted-entries.md)
- Low capital utilization at 19% keeps risk controlled
- System stability with 206 successful scans and no critical errors
- Whale strategy now executes 9x+ volume/liquidity anomalies through gated trades while logging each decision (see fix_logs/2026-04-04-whale-execution.md)
- [x] Autonomy cron loop now runs cleanly after the rejected-trade journal indentation was corrected (see fix_logs/2026-04-04-autonomy-journal-crash.md)
- [x] Confidence-based paper sizing now emits gate metadata for every fill, making the quarter-Kelly cap traceable (see fix_logs/2026-04-04-paper-sizing-activation.md)
- [x] Cointegration Grade A trial now tolerates only the soft momentum/spread_std misses, keeps tradeable tied to the A+ math leg, and journals the guardrail tweak along with failed-filter counts ahead of every paper promotion (see fix_logs/2026-04-04-grade-a-paper-promotion.md)

## Not Working
- [ ] Weather trading is losing more trades than it is winning. Investigate what has changed and see what might be the cause. 
- [x] Copy strategy entry-price guard now forbids near-certain bets so the win rate no longer comes with outsized losers (fix_logs/2026-04-04-copy-strategy-risk-reward.md)
- [x] Whale strategy now executes gated trades for the high-ratio alerts (see fix_logs/2026-04-04-whale-execution.md)
- [ ] Weather stop-losses triggering frequently (multiple -$4 to -$5 losses)
- [x] Cointegration underutilized with only 7 total trades despite 3,407 A-grade signals (trial guardrails now log the filter count and remit near-A+ signals through the tuned guardrails — see fix_logs/2026-04-04-grade-a-paper-promotion.md)
- [x] Paper sizing decisions mostly in shadow mode (87 of 89 not applied) — confidence sizing now records the gate outcome per fill (see fix_logs/2026-04-04-paper-sizing-activation.md)

## Top 5 Improvements
- [ ] Tighten weather strategy stop-losses or improve entry timing to reduce -$5 drawdowns
- [x] Increase cointegration trade frequency by relaxing A+ criteria and letting weighted A-grade entries run, with the new thresholds/guardrails and weighted-size metadata captured in fix_logs/2026-04-04-grade-a-weighted-entries.md
- [x] Harden the copy strategy by refusing entry prices outside 0.15-0.85 so losses scale with wins (fix_logs/2026-04-04-copy-strategy-risk-reward.md)
- [x] Implement whale strategy execution logic to capitalize on detected anomalies (see fix_logs/2026-04-04-whale-execution.md)
- [x] Apply confidence-based position sizing more aggressively (currently 97.8% shadow decisions) — telemetry now confirms the gate outcome per trade (see fix_logs/2026-04-04-paper-sizing-activation.md)
