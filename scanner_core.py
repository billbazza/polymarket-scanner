"""Shared scanner helpers for sync and async pair scanners."""
from datetime import datetime, timezone

import numpy as np
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.stattools import coint

MIN_DAYS_TO_RESOLUTION = 21


def days_to_resolution(end_date_str):
    """Return days until market resolves, or inf if unknown/unparseable."""
    if not end_date_str:
        return float("inf")
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0, (end - datetime.now(timezone.utc)).days)
    except (ValueError, TypeError):
        return float("inf")


def align_prices(hist_a, hist_b):
    """Align two price history lists by timestamp or by matching tail lengths."""
    if not hist_a or not hist_b:
        return None, None

    map_a = {h["t"]: h["p"] for h in hist_a}
    map_b = {h["t"]: h["p"] for h in hist_b}
    common_ts = sorted(set(map_a.keys()) & set(map_b.keys()))

    if len(common_ts) < 20:
        if len(hist_a) >= 20 and len(hist_b) >= 20:
            min_len = min(len(hist_a), len(hist_b))
            return (
                np.array([h["p"] for h in hist_a[-min_len:]]),
                np.array([h["p"] for h in hist_b[-min_len:]]),
            )
        return None, None

    return (
        np.array([map_a[t] for t in common_ts]),
        np.array([map_b[t] for t in common_ts]),
    )


def test_pair(prices_a, prices_b):
    """Run cointegration test and compute spread statistics."""
    if prices_a is None or prices_b is None:
        return None
    if len(prices_a) < 20 or len(prices_b) < 20:
        return None
    if np.std(prices_a) < 0.001 or np.std(prices_b) < 0.001:
        return None

    try:
        score, pvalue, _crit_values = coint(prices_a, prices_b)
    except Exception:
        return None

    model = LinearRegression()
    model.fit(prices_b.reshape(-1, 1), prices_a)
    beta = model.coef_[0]

    spread = prices_a - beta * prices_b
    mean_spread = np.mean(spread)
    std_spread = np.std(spread)
    if std_spread < 0.0001:
        return None

    z_score = (spread[-1] - mean_spread) / std_spread
    z_prev = float((spread[-2] - mean_spread) / std_spread) if len(spread) >= 2 else float(z_score)
    spread_retreating = bool(abs(z_score) < abs(z_prev))

    spread_lag = spread[:-1]
    spread_diff = np.diff(spread)
    if len(spread_lag) > 5:
        hl_model = LinearRegression()
        hl_model.fit(spread_lag.reshape(-1, 1), spread_diff)
        lam = hl_model.coef_[0]
        half_life = -np.log(2) / lam if lam < 0 else float("inf")
    else:
        half_life = float("inf")

    return {
        "coint_score": float(score),
        "coint_pvalue": float(pvalue),
        "beta": float(beta),
        "z_score": float(z_score),
        "z_prev": float(z_prev),
        "spread_retreating": spread_retreating,
        "spread_mean": float(mean_spread),
        "spread_std": float(std_spread),
        "current_spread": float(spread[-1]),
        "half_life": float(half_life),
        "n_points": int(len(prices_a)),
    }
