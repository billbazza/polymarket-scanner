"""Math engine — Kelly Criterion, Expected Value, Slippage Protection.

All local math, zero external dependencies beyond numpy.
"""
import logging
import numpy as np
import api

log = logging.getLogger("scanner.math")


def expected_value(win_prob, win_payout, loss_amount):
    """Calculate expected value of a trade.

    Args:
        win_prob: probability the spread reverts (0-1)
        win_payout: profit if spread reverts (USD)
        loss_amount: loss if spread widens (USD)

    Returns:
        EV in USD. Positive = edge exists.
    """
    return (win_prob * win_payout) - ((1 - win_prob) * loss_amount)


def ev_from_zscore(z_score, half_life, spread_std, size_usd=100):
    """Estimate EV from z-score and half-life.

    Uses historical reversion rate as win probability proxy.
    Higher |z| and shorter half-life = higher win probability.

    The logic: if z > 2, historically ~95% of observations are within 2 std.
    So a z of 2.5 means we're in the 1.2% tail — high reversion probability.
    But we discount by half-life: slow reversion = more time for things to go wrong.
    """
    # Base win probability from z-score (using normal CDF)
    from scipy.stats import norm
    # Probability of reverting to within 0.5 std of mean
    abs_z = abs(z_score)
    # P(revert) = P(|Z| < current |Z|) — how unusual is this deviation
    base_prob = 2 * norm.cdf(abs_z) - 1  # e.g., z=2 -> 0.954

    # Discount by half-life: fast reversion = keep full prob, slow = discount
    # Half-life > 10 periods is slow, < 3 is fast
    if half_life <= 0 or half_life > 100:
        hl_factor = 0.3  # slow reversion, big discount
    else:
        hl_factor = min(1.0, 3.0 / half_life)  # 1.0 at hl=3, 0.3 at hl=10

    win_prob = base_prob * hl_factor

    # Win payout: spread reverts to mean (z goes to 0)
    # Each unit of z = spread_std in price terms
    win_payout = abs_z * spread_std * size_usd

    # Loss: spread widens by another 1 std (stop-loss level)
    loss_amount = 1.0 * spread_std * size_usd

    ev = expected_value(win_prob, win_payout, loss_amount)

    log.debug("EV calc: z=%.2f hl=%.1f base_prob=%.3f hl_factor=%.2f ev=%.4f",
              z_score, half_life, base_prob, hl_factor, ev)

    return {
        "ev": round(ev, 4),
        "ev_pct": round(ev / size_usd * 100, 2) if size_usd else 0,
        "win_prob": round(win_prob, 4),
        "win_payout": round(win_payout, 4),
        "loss_amount": round(loss_amount, 4),
        "base_prob": round(base_prob, 4),
        "hl_factor": round(hl_factor, 4),
    }


def kelly_fraction(win_prob, win_payout, loss_amount):
    """Kelly Criterion — optimal fraction of bankroll to risk.

    f* = (p * b - q) / b
    where:
        p = win probability
        q = 1 - p (loss probability)
        b = win/loss ratio (payout odds)

    Returns fraction (0-1). We cap at 0.25 (quarter-Kelly) for safety.
    """
    if loss_amount <= 0 or win_payout <= 0:
        return 0

    b = win_payout / loss_amount  # odds ratio
    q = 1 - win_prob

    f = (win_prob * b - q) / b

    # Cap at quarter-Kelly (full Kelly is too aggressive for real trading)
    f = max(0, min(f, 0.25))

    return round(f, 4)


def kelly_size(bankroll, win_prob, win_payout, loss_amount):
    """Calculate position size using Kelly Criterion.

    Returns recommended USD size for this trade.
    """
    f = kelly_fraction(win_prob, win_payout, loss_amount)
    size = bankroll * f
    # Full Kelly = quarter-Kelly * 4 (we cap at quarter for safety)
    return {
        "kelly_fraction": f,
        "full_kelly_size": round(size * 4, 2),
        "quarter_kelly_size": round(size, 2),
    }


def position_size(bankroll, ev_result):
    """Convenience: compute Kelly size from EV result."""
    f = kelly_fraction(ev_result["win_prob"], ev_result["win_payout"], ev_result["loss_amount"])
    return {
        "kelly_fraction": f,
        "recommended_size": round(bankroll * f, 2),
        "max_size": round(bankroll * 0.25, 2),  # never more than 25% of bankroll
    }


def check_slippage(token_id, trade_size_usd=100, max_slippage_pct=2.0):
    """Check order book depth and estimate slippage.

    Args:
        token_id: Polymarket token ID
        trade_size_usd: how much we want to trade
        max_slippage_pct: max acceptable slippage (default 2%)

    Returns:
        dict with slippage estimate and pass/fail
    """
    try:
        book = api.get_book(token_id)
    except Exception as e:
        log.warning("Failed to fetch order book for %s: %s", token_id[:16], e)
        return {
            "ok": False,
            "reason": f"Failed to fetch order book: {e}",
            "slippage_pct": None,
        }

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    if not bids or not asks:
        return {
            "ok": False,
            "reason": "Empty order book",
            "slippage_pct": None,
            "bid_depth": 0,
            "ask_depth": 0,
        }

    # Calculate depth: total USD available within 2% of best price
    best_bid = float(bids[0].get("price", 0))
    best_ask = float(asks[0].get("price", 0))
    spread_pct = ((best_ask - best_bid) / best_ask * 100) if best_ask > 0 else 100

    # Sum liquidity on each side
    bid_depth = sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids)
    ask_depth = sum(float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks)

    # Estimate slippage: walk the book
    def walk_book(levels, size_usd):
        filled = 0
        total_cost = 0
        for level in levels:
            price = float(level.get("price", 0))
            available = float(level.get("size", 0)) * price
            fill = min(size_usd - filled, available)
            total_cost += fill
            filled += fill
            if filled >= size_usd:
                break
        if filled == 0:
            return None
        avg_price = total_cost / filled if filled > 0 else 0
        return avg_price

    # For buying: walk asks. For selling: walk bids.
    avg_buy = walk_book(asks, trade_size_usd)
    slippage = abs(avg_buy - best_ask) / best_ask * 100 if avg_buy and best_ask > 0 else 100

    ok = slippage <= max_slippage_pct and bid_depth >= trade_size_usd * 2

    return {
        "ok": ok,
        "slippage_pct": round(slippage, 3),
        "spread_pct": round(spread_pct, 3),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_depth_usd": round(bid_depth, 2),
        "ask_depth_usd": round(ask_depth, 2),
        "reason": None if ok else f"Slippage {slippage:.1f}% > {max_slippage_pct}%" if slippage > max_slippage_pct else f"Thin book: ${bid_depth:.0f} depth",
    }


def score_opportunity(opp, bankroll=1000, min_ev_pct=5.0, max_slippage_pct=2.0):
    """Score a signal through all Tier 1 filters.

    Returns the opportunity enriched with:
    - EV calculation
    - Kelly sizing
    - Slippage check
    - Overall pass/fail grade
    """
    # EV calculation
    ev = ev_from_zscore(
        z_score=opp["z_score"],
        half_life=opp["half_life"],
        spread_std=opp["spread_std"],
        size_usd=100,
    )

    # Kelly sizing
    sizing = position_size(bankroll, ev)

    # Filter #3: reject if either market price is near resolution (outside 5%-95%)
    # A market at 0.01 or 0.99 is effectively resolved — no mean reversion possible
    price_a = float(opp.get("price_a") or 0)
    price_b = float(opp.get("price_b") or 0)
    price_ok = (0.05 <= price_a <= 0.95) and (0.05 <= price_b <= 0.95)

    # Filters
    filters = {
        "ev_pass":       bool(ev["ev_pct"] >= min_ev_pct),
        "kelly_pass":    bool(sizing["kelly_fraction"] > 0),
        "z_pass":        bool(abs(opp["z_score"]) >= 1.5),
        "coint_pass":    bool(opp["coint_pvalue"] < 0.10),
        "hl_pass":       bool(opp["half_life"] < 20),
        # Filter #2: spread must be retreating toward mean, not still diverging
        "momentum_pass": bool(opp.get("spread_retreating", True)),
        # Filter #3: neither market near resolution
        "price_pass":    bool(price_ok),
    }

    grade = sum(filters.values())
    all_pass = all(filters.values())

    label = ["F", "D", "C", "B", "A", "A", "A", "A+"][min(grade, 7)]
    log.info("Scored: %s | grade=%s ev=%.2f%% kelly=%.4f z=%.2f tradeable=%s",
             opp.get("event", "?")[:40], label, ev["ev_pct"], sizing["kelly_fraction"],
             opp["z_score"], all_pass)

    return {
        **opp,
        "ev": ev,
        "sizing": sizing,
        "filters": filters,
        "grade": grade,
        "tradeable": all_pass,
        "grade_label": ["F", "D", "C", "B", "A", "A", "A", "A+"][min(grade, 7)],
    }
