# Daily Report - 2026-04-05

Generated at: 2026-04-05T08:30:00Z
Model: codex-gpt-5
Confidence: high

## Summary
- [x] Weather stop-loss investigation completed without changing any entry thresholds.
- [x] Weather remains profitable overall at `80` closed trades, `73.8%` win rate, and `+$390.89` closed PnL.
- [x] The active weather stop policy has been realigned with the supported `15%` setting, and stop diagnostics now capture enough context to audit gap-through losses directly.

## Working
- [x] Weather strategy still carries the strongest realized edge despite the recent stop-loss cluster.
- [x] The tracker now writes richer weather stop diagnostics with city/date/hold-time metadata and an explicit gap-through classification.
- [x] The standalone weather stop-context JSONL output is now tied to the active DB/report environment instead of drifting away from the live run context.

## Not Working
- [ ] Shorter-horizon weather entries still produce the worst stop-loss outcomes.
  Evidence in `reports/diagnostics/2026-04-05-weather-stop-loss-review.md`: the `0-24h` bucket averages `-$7.59` across `4` recent stop-outs.
- [ ] Repeat city/date pain still appears in the closed stop-loss sample.
  Atlanta `2026-04-03` and Denver `2026-04-04` each recorded `2` stop-outs in the current audit.
- [x] The undocumented `18%` weather stop drift has been removed.
  See `fix_logs/2026-04-05-weather-stop-loss-investigation.md`.

## Top 5 Improvements
- [x] Restore the supported weather stop policy and capture better stop-loss evidence instead of changing weather entry thresholds.
  Logged in `fix_logs/2026-04-05-weather-stop-loss-investigation.md`.
- [ ] Review whether shorter-horizon weather trades need different operational handling after more diagnostics accumulate.
  No threshold changes were made in this pass.
- [ ] Review repeated city/date stop clusters after more stop-context samples are collected.
- [ ] Continue stage 2/3 readiness work already tracked in the April 3-4 reports.
- [ ] Keep copy and whale performance under review relative to weather and cointegration.

## Evidence Links
- [x] `fix_logs/2026-04-05-weather-stop-loss-investigation.md`
- [x] `reports/diagnostics/2026-04-05-weather-stop-loss-review.md`
