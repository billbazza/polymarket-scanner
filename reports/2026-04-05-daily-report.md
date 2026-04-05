# Daily Report - 2026-04-05

Generated at: 2026-04-05T15:33:10Z
Model: gpt-5-codex
Confidence: high

## Summary
All headline metrics below come from one `db.get_paper_account_overview()` snapshot taken at `2026-04-05T15:33:10Z`.

In paper-account scope (`paper_research_only`), realized PnL is `+$382.84` and unrealized PnL is `+$179.85`, producing `+$562.69` above the `$2000.00` starting bankroll and `total_equity=$2562.69`. That same snapshot shows `10` open paper trades, `$261.00` committed capital, `13.1%` paper-bankroll utilization, and `0` data-quality gaps (`0` inferred trade states, `0` open paper trades missing marks, `0` excluded external open trades).

In the embedded strategy breakdown from the same snapshot, weather is the main contributor with `93` total trades, `84` closed trades, `9` open trades, `+$377.47` realized PnL, `+$550.51` net PnL, and a `71.4%` win rate on closed trades. Cointegration is `9` total / `8` closed / `1` open with `+$20.30` realized PnL, `+$27.11` net PnL, and a `100.0%` win rate on closed trades. Copy is `38` total / `38` closed / `0` open with `-$14.85` realized/net PnL in the all-states strategy view; its paper-only realized/net PnL is `-$14.93` because `+$0.08` of realized PnL sits outside the paper bankroll scope.

## Working
- [x] Paper-account totals reconcile cleanly in this snapshot: `+$382.84` realized PnL plus `+$179.85` unrealized PnL equals `total_equity=$2562.69` on the `$2000.00` paper bankroll.
- [x] Weather remains the strongest strategy by contribution: `93` total trades, `84` closed, `9` open, `+$377.47` realized PnL, and `+$550.51` net PnL.
- [x] Cointegration closed trades remain profitable: `8` wins, `0` losses, `100.0%` closed-trade win rate, `+$20.30` realized PnL, and one open paper position still marked in the net figure.
- [x] Paper risk remains contained in this snapshot: `10` open paper trades, `$261.00` committed capital, `13.1%` utilization, and `$2121.84` available cash.

## Not Working
- [ ] Weather stop-loss losses remain the active unresolved issue on `2026-04-05`; the current follow-up stays in `reports/diagnostics/2026-04-05-weather-stop-loss-review.md` and `fix_logs/2026-04-05-weather-stop-loss-investigation.md`.
- [ ] Cointegration remains volume-constrained despite the guarded A-grade trial: `9` total trades / `8` closed / `1` open in this snapshot. Follow-up stays in `fix_logs/2026-04-04-cointegration-trial-guardrails.md` and `fix_logs/2026-04-04-filter-failure-visibility.md`.
- [ ] Copy remains negative in the all-states strategy view: `38` closed trades, `57.9%` win rate, and `-$14.85` realized/net PnL. The guardrail fix is logged in `fix_logs/2026-04-04-copy-strategy-risk-reward.md`; reassessment is still needed before expanding the strategy again.

## Top 5 Improvements
- [ ] No improvement items were intentionally promoted to `implementation-plan.md` or `testing-ideas.md` on `2026-04-05`; unresolved follow-ups remain in `fix_logs/` or `reports/diagnostics/` until an explicit promotion decision is made.
