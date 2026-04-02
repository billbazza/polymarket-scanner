# 2026-04-02 Weather Risk Review

## Scope
- Daily-report follow-up dated 2026-04-02: validate weather trade-state data before changing stop-loss policy.
- Audit focus: recent weather losses, open/close correctness, P&L attribution, stale-forecast risk, delayed-close risk, and stop-loss width.

## Verified Trade-State Integrity
- Recent weather closes in `scanner.db` match tracker and journal events; stop-loss exits for trades `412`, `414`, `416`, `419`, and `425` were recorded consistently in `trades`, `logs/scanner.log`, and `logs/journal.jsonl`.
- The sampled drawdown trades were closed on the prior `20%` weather stop, not the current `15%` setting now present in `tracker.py`. The recorded exit notes show floors `0.404`, `0.540`, `0.448`, `0.296`, and `0.292`, which are exactly `20%` below entry for trades `412`, `414`, `416`, `419`, and `425`.
- Close-loop latency was not the main problem at the stop that was actually in force. For those five trades, the time from the last recorded `20%` floor breach to the close event was about `14.9-17.8` seconds.
- Single-leg weather P&L attribution is behaving correctly for the sampled losses and wins: realized `trades.pnl` matches shares-based mark-to-market from `entry_price_a`, `exit_price_a`, and `size_usd`.

## Findings
- The recent drawdown sequence is real, but it is not explained by broken weather accounting.
- Entry quality is mixed rather than obviously broken. The recent stop-outs still had positive modeled entry edge, typically about `+18.8pp` to `+27.4pp` versus market price for `BUY_YES` entries, or comparable inverse edge for `BUY_NO`.
- Forecast staleness is a contributor, especially on repeat entries opened `48-120` hours ahead. Los Angeles April 4 was opened three separate times (`338`, `415`, `419`) for cumulative closed P&L of `$-18.27`, which is more consistent with forecast drift and re-entry churn than with a single bad close.
- Stop-loss width mattered on the recent drawdown. Replaying the sampled losses against a `15%` stop would have reduced loss size materially on trades `412`, `414`, and `416`:
  - `412`: `$-5.15` actual at `20%` vs about `$-3.17` at `15%`
  - `414`: `$-5.04` actual at `20%` vs about `$-3.26` at `15%`
  - `416`: `$-5.18` actual at `20%` vs about `$-3.75` at `15%`
- A tighter stop did not help every case. Trades `419` and `425` gapped through both the `20%` and `15%` floors on the next tracker observation, so those losses were driven more by market repricing between checks than by the policy width itself.
- The biggest control gap around the drawdown cluster was repeat exposure on the same weather token after a stop-out. Closed-token totals in the live DB show:
  - Los Angeles April 4 token: `3` closed losses, `$-18.27`
  - Dallas April 2 token: `2` closed losses, `$-9.27`

## Recommendation
- Keep the current weather stop-loss at `15%`.
- Do not tighten further to `10%` yet; the closed-trade replay improves slightly at `10%`, but it cuts more eventual winners than `15%` and the sample does not justify another step tighter.
- Keep the no-reopen rule for weather outcome tokens after any exit.
- Do not treat delayed close behavior or P&L attribution as the root cause of the 2026-04-02 drawdown sequence.

## Evidence
- Counterfactual on the closed weather sample in `scanner.db`:
  - `20%` stop: simulated closed-sample P&L `+$257.84`
  - `15%` stop: simulated closed-sample P&L `+$264.36`
  - `10%` stop: simulated closed-sample P&L `+$263.83`
- Changed exits in the replay:
  - `20%`: `17` trades, `5` eventual winners cut early
  - `15%`: `20` trades, `6` eventual winners cut early
  - `10%`: `24` trades, `8` eventual winners cut early
- Closed weather results by entry horizon remain positive overall at `72h+`, so the current sample is not strong enough to justify a hard horizon cutoff:
  - `0-24h`: `10` closes, `+$9.12`
  - `25-48h`: `2` closes, `$-9.77`
  - `49-72h`: `15` closes, `+$78.89`
  - `73h+`: `36` closes, `+$173.02`
- The evidence supports holding `15%` plus the no-reopen guard as the best current control set. The losses were primarily a combination of forecast drift, repeat token exposure, and some gap-through-stop behavior, not bad accounting or a slow close loop.
