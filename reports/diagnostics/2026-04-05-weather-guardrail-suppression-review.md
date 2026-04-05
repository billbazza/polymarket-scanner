# 2026-04-05 Weather Guardrail Suppression Review

## Scope
- Operator request dated 2026-04-05: compare the current weather guardrail stack against the earlier simpler weather setup, focus on weather only, and separate true policy effects from bugs or execution-environment failures.
- Followed the repo contract in [AGENTS.md](/Users/will/.cline/worktrees/d0cec/polymarket-scanner/AGENTS.md):
  - `Architecture / Trade Types` and `Autonomy Loop` for the threshold-weather execution path.
  - `Trading Modes / Weather strategy parity` and `Pre-execution parity` for scan vs preflight consistency.
  - `Always Do` for logging the analysis under `fix_logs/` or diagnostics.

## Baseline Used
- Same recent market window: `2026-04-04 00:00` through the current DB snapshot on `2026-04-05`.
- Current system:
  - scan gate = `sources_agree`, positive EV, positive Kelly, edge >= 15pp, entry price >= 0.35, plus the active weather noise guard
  - active guard observed in current signals = relaxed tier: `liquidity >= 5k`, `hours_ahead >= 48`, `source disagreement <= 18pp`
  - preflight/history = open-token dedupe, closed-token reopen/probation block, horizon recheck / admission parity, paper/live runtime checks
- Earlier simpler approximation:
  - scan gate = `sources_agree`, positive EV, positive Kelly, edge >= 15pp, entry price >= 0.35`
  - no extra horizon/liquidity/disagreement scan gate
  - no closed-token reopen block and no horizon recheck at immediate preflight
  - still keeps open-token dedupe in the comparison so the baseline does not get credit for obviously duplicating an already-open contract

## Signal-Flow Comparison
- Recent weather opportunities in DB window: `404` signal snapshots across `111` unique entry tokens.
- Simpler baseline tradeable: `137` snapshots across `19` unique entry tokens.
- Current tradeable: `130` snapshots across `18` unique entry tokens.
- Net scan-time suppression from the extra guard stack: `7` snapshots, only `1` unique token.

### What Actually Blocked Those 7 Scan-Time Misses
- `7`/`7` failed the horizon guard (`<48h`).
- `3`/`7` also failed source-disagreement (`>18pp`).
- `0` failed liquidity (`<5k`) in this window.
- `0` were lost to edge/EV/Kelly relative to the simpler baseline because those are shared in both setups.

### Immediate Preflight Comparison
- Using the actual trade ledger as the fixed history for the same window:
  - simpler preflight survivors: `89` snapshots
  - current preflight survivors: `28` snapshots
- The delta is mostly repeated re-entry suppression, not fresh discovery:
  - current added `58` `token_already_closed` blocks
  - both systems still hit many `token_already_open` blocks (`48` simple vs `44` current)
- Unique-token view is much smaller:
  - simpler preflightable unique tokens: `18`
  - current preflightable unique tokens: `17`
- Conclusion from the flow data:
  - the current scan guards are not materially shrinking new weather discovery
  - the current stack is materially shrinking repeated attempts on already-used tokens

## Actual Execution In The Same Window
- Actual paper weather trades opened: `12`
- Closed so far: `6`
- Realized PnL on those closes: `+$7.85`
- Hit rate on those closes: `33.3%`
- Realized closed-trade drawdown in sequence: `-$22.06`

These execution numbers are weak enough that the current window does not support relaxing the system just to increase fill count.

## Bugs / Runtime Failures Versus True Policy Effects
- Weather journal entries from `2026-04-04` onward:
  - `12` `trade_opened`
  - `44` reopen blocks
  - `44` open-token blocks
  - `3` `horizon_too_short` preflight blocks at the boundary case
  - `2` legacy penny weather disabled entries
  - `2` penny weather scan-only runtime-disabled entries
- The `3` horizon boundary skips are implementation drift / parity issues, not evidence that the policy is working:
  - they used the same nominal `48.0h` threshold on a freshly scanned candidate
  - they should be treated as bugs or timing mismatches, not as valid guardrail saves
- The `4` penny disabled/scan-only entries are runtime configuration effects, not weather-policy suppression.

## Realized Trade Quality Of The Blocked Shapes
- Historical closed weather trades, evaluated against the current relaxed extra guard (`48h / 5k / 18pp`):
  - trades that would pass the relaxed extra guard: `57` closes, `+$300.64`, `78.9%` hit rate, `-$49.02` drawdown
  - trades that would fail the relaxed extra guard: `27` closes, `+$76.83`, `55.6%` hit rate, `-$68.57` drawdown
- Per-filter read:
  - disagreement-fail cohort: `9` closes, `-$13.09`, `33.3%` hit rate, `-$38.89` drawdown
  - horizon-fail cohort: `16` closes, `+$43.54`, `50.0%` hit rate, `-$42.58` drawdown
  - liquidity-fail cohort: `6` closes, `+$39.81`, `100%` hit rate, `0` drawdown

## Operator Conclusion
- Current weather checks are **not too tight at scan time** in the recent April 4-5 window.
- The extra scan guardrails only removed `7` of `137` otherwise-simple tradeable snapshots (`5.1%`) and only `1` unique token. That is not the main source of trade suppression.
- The main suppressor is the **reopen/history layer**, which cut preflight survivors from `89` to `28`, but almost all of that reduction was repeated attempts on tokens that were already open or already closed. On a unique-token basis the difference was only `18` vs `17`.
- The strongest justified filter is **source disagreement**. Historical disagreement-fail trades were meaningfully worse than the pass cohort.
- **Horizon** looks directionally useful but less decisive than disagreement. The short-horizon cohort still made money overall, but with much worse hit rate.
- **Liquidity** is the weakest current guard on the historical data. It did not suppress anything in the April 4-5 window, and the historical `<5k` cohort was actually positive. If any scan guard should be relaxed first in a future experiment, it is liquidity, not disagreement.
- Immediate action:
  - keep the relaxed disagreement + horizon stack in place
  - fix the remaining 48h boundary preflight mismatch and do not count those skips as policy wins
  - treat reopen policy separately from scan guardrails, because that is where most suppression now lives
