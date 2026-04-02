# 2026-04-02 Weather Risk Review

## Scope
- Daily-report follow-up dated 2026-04-02: validate weather trade-state data before changing stop-loss policy.
- Audit focus: recent weather losses, open/close correctness, P&L attribution, stale-forecast risk, delayed-close risk, and stop-loss width.

## Verified Trade-State Integrity
- Recent weather closes in `scanner.db` match tracker and journal events; stop-loss exits for trades `412`, `414`, `416`, `419`, and `425` were recorded consistently in `trades`, `logs/scanner.log`, and `logs/journal.jsonl`.
- Stop-loss closes were not materially delayed once breached. For the sampled losses, the first recorded breach to close delay was about `15-18` seconds, so the recent drawdown is not primarily a close-loop latency issue.
- Single-leg weather P&L attribution is behaving correctly for the sampled losses and wins: realized P&L in `trades.pnl` matches entry/exit prices and size.

## Findings
- The recent drawdown sequence is real, but it is not explained by broken weather accounting.
- Entry quality is mixed rather than obviously broken. The sampled stop-outs still had positive model edge at entry, typically about `0.16-0.27` probability points versus entry price.
- Forecast staleness is a contributor. Several stop-outs were entered `48-96` hours ahead and the market repriced against the trade before resolution.
- The biggest control gap was repeat exposure on the same weather token after a stop-out. Live DB audit found active reopen patterns:
  - trade `418` reopened the Denver April 4 contract immediately after trade `412` stopped out
  - trade `424` reopened the Atlanta April 3 contract immediately after trade `416` stopped out
  - trade `423` reopened the Los Angeles April 4 contract after three prior closed losses on the same token totaling `$-18.27`

## Recommendation
- Tighten the weather stop-loss from `20%` to `15%`.
- Do not reopen the same weather outcome token after it has already been closed once.

## Evidence
- Counterfactual on the closed weather sample in `scanner.db`:
  - `20%` stop: simulated closed-sample P&L `+$257.88`
  - `15%` stop: simulated closed-sample P&L `+$264.40`
  - `10%` stop: simulated closed-sample P&L `+$263.86`
- A `15%` stop improved the closed-sample result versus `20%` while cutting fewer eventual winners than a `10%` stop.
- A tighter stop alone does not address the repeated-loss cluster if the same token can be reopened after a stop-out, so the reentry block should travel with the stop update.
