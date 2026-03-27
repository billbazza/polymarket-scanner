"""Log returns — proper P&L math.

Arithmetic returns lie when compounding: +50% then -50% = -25%, not 0%.
Log returns sum correctly: ln(1.5) + ln(0.5) = ln(0.75) = -28.8%.

Every P&L calculation in the system should use these.
"""
import logging
import numpy as np

log = logging.getLogger("scanner.returns")


def log_return(p0, p1):
    """Single-period log return: ln(P1/P0)."""
    if p0 <= 0 or p1 <= 0:
        return 0.0
    return float(np.log(p1 / p0))


def log_return_series(prices):
    """Convert price series to log return series."""
    prices = np.array(prices, dtype=float)
    # Filter out zeros
    mask = prices > 0
    if mask.sum() < 2:
        return np.array([])
    clean = prices[mask]
    return np.diff(np.log(clean))


def cumulative_log_return(prices):
    """Total log return from first to last price."""
    prices = np.array(prices, dtype=float)
    valid = prices[prices > 0]
    if len(valid) < 2:
        return 0.0
    return float(np.log(valid[-1] / valid[0]))


def log_to_simple(log_ret):
    """Convert log return to simple (arithmetic) return."""
    return float(np.exp(log_ret) - 1)


def pairs_pnl(entry_a, exit_a, entry_b, exit_b, side_a="BUY", size_usd=100):
    """Calculate pairs trade P&L using log returns.

    For a pairs trade:
      - BUY A, SELL B: profit when A rises and B falls
      - SELL A, BUY B: profit when A falls and B rises

    Returns dict with log returns, simple returns, and USD P&L.
    """
    lr_a = log_return(entry_a, exit_a)
    lr_b = log_return(entry_b, exit_b)

    if side_a == "BUY":
        # Long A, short B
        net_log_return = lr_a - lr_b
    else:
        # Short A, long B
        net_log_return = lr_b - lr_a

    simple_return = log_to_simple(net_log_return)
    pnl_usd = simple_return * size_usd

    return {
        "log_return_a": round(lr_a, 6),
        "log_return_b": round(lr_b, 6),
        "net_log_return": round(net_log_return, 6),
        "simple_return": round(simple_return, 6),
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(simple_return * 100, 2),
    }


def sharpe_ratio(returns, risk_free_rate=0.0):
    """Annualized Sharpe ratio from a series of returns.

    Assumes daily returns. Annualizes by sqrt(365) for crypto markets.
    """
    returns = np.array(returns, dtype=float)
    if len(returns) < 2:
        return 0.0
    excess = returns - risk_free_rate / 365
    if np.std(excess) == 0:
        return 0.0
    return float(np.mean(excess) / np.std(excess) * np.sqrt(365))
