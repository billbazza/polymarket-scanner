# Scoring Pipeline Guide

## How Scoring Works

Every opportunity from the scanner runs through `math_engine.score_opportunity()` which applies 8 binary filters:

| Filter | Pass Condition | What It Checks |
|--------|---------------|----------------|
| `ev_pass` | EV% >= hurdle | Expected value as % of trade size (category-adjusted when category is present) |
| `kelly_pass` | Kelly fraction > 0 | Positive edge exists |
| `z_pass` | \|z-score\| >= active scan threshold | Spread is significantly diverged |
| `coint_pass` | p-value < active scan threshold | Pair is genuinely cointegrated |
| `hl_pass` | half-life < 20 | Spread reverts fast enough to profit |
| `momentum_pass` | spread is retreating | Latest spread move is reverting, not still widening |
| `price_pass` | both prices in 5%-95% band | Avoid near-resolution or non-operable pairs |
| `spread_std_pass` | spread std >= 0.02 | Spread moves enough to overcome fees |

## Grades

- **A+** = 8/8 filters pass, `tradeable = True`
- **A** = 7/8
- **B** = 6/8
- **C** = 5/8
- **D** = 4/8
- **F** = 0/8

Only A+ signals have `tradeable = True`. A-grade rows can still be operator-meaningful when the only miss is `ev_pass`; those are the controlled near-miss trial cohort. If that cohort is admitted in paper, penny/book must use the same default trial admission path unless an explicit live safeguard vetoes the specific trade. Lower-quality rows are rejected with structured blocker metadata.

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
- `min_ev_pct` (default 2.0) — minimum edge to pass EV filter before any category adjustment
- `max_slippage_pct` (default 2.0) — maximum acceptable slippage

Scanner runtime thresholds matter too:
- `min_z_abs` is taken from the active scan `z_threshold`
- `max_coint_pvalue` is taken from the active scan `p_threshold`

Rejection observability:
- `score_opportunity()` now returns an `admission` dict with failed filters, primary blocker code, human-readable reason, thresholds, and observed values.
- `/api/signals` defaults to operator-meaningful rows; pass `include_rejected=true` to inspect the lower-quality rejected set.
