# Scoring Pipeline Guide

## How Scoring Works

Every opportunity from the scanner runs through `math_engine.score_opportunity()` which applies 5 binary filters:

| Filter | Pass Condition | What It Checks |
|--------|---------------|----------------|
| `ev_pass` | EV% >= 5.0 | Expected value as % of trade size |
| `kelly_pass` | Kelly fraction > 0 | Positive edge exists |
| `z_pass` | \|z-score\| >= 1.5 | Spread is significantly diverged |
| `coint_pass` | p-value < 0.10 | Pair is genuinely cointegrated |
| `hl_pass` | half-life < 20 | Spread reverts fast enough to profit |

## Grades

- **A+** = 5/5 filters pass, `tradeable = True`
- **A** = 4/5
- **B** = 3/5
- **C** = 2/5
- **D** = 1/5
- **F** = 0/5

Only A+ signals have `tradeable = True`. All others are informational.

## EV Calculation

```
Base probability = CDF of |z-score| (how unusual is this deviation)
Half-life factor = min(1.0, 3.0 / half_life) (fast reversion = keep full prob)
Win probability = base_prob * hl_factor
Win payout = |z| * spread_std * size_usd
Loss amount = 1.0 * spread_std * size_usd (stop at 1 std)
EV = (win_prob * win_payout) - (loss_prob * loss_amount)
```

## Kelly Sizing

```
f* = (p * b - q) / b
where p = win_prob, q = 1-p, b = win_payout / loss_amount
Capped at 0.25 (quarter-Kelly)
```

## Modifying Thresholds

Change defaults in `math_engine.score_opportunity()`:
- `bankroll` (default 1000) — your paper trading bankroll
- `min_ev_pct` (default 5.0) — minimum edge to pass EV filter
- `max_slippage_pct` (default 2.0) — maximum acceptable slippage

Or pass them via the scan CLI: `python3 scan.py --z-threshold 2.0 --strict`
