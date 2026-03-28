"""Math engine — Kelly Criterion, Expected Value, Slippage Protection.

All local math, zero external dependencies beyond numpy.
"""
import logging
import numpy as np
import api

log = logging.getLogger("scanner.math")

# ── Fee model ──────────────────────────────────────────────────────────────
# Polymarket charges takers ~2% per leg. A full round-trip pairs trade
# (entry + exit, both legs) costs 4× fee_per_leg on the total position.
# Maker orders pay 0% — this is the target execution mode.
TAKER_FEE_PCT  = 0.02   # per-leg fee as a fraction of notional
MAKER_FEE_PCT  = 0.00   # maker orders: no fee

# ── Minimum spread volatility ──────────────────────────────────────────────
# Markets where the spread barely moves can't pay off even with zero fees.
# spread_std is in price units (0-1). 0.02 = a 2-cent swing per std — the
# minimum for a z=2 signal to generate meaningful dollar EV.
MIN_SPREAD_STD = 0.02


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


def ev_from_zscore(z_score, half_life, spread_std, size_usd=100,
                   fee_pct=TAKER_FEE_PCT):
    """Estimate EV from z-score and half-life, net of round-trip fees.

    fee_pct: per-leg fee (TAKER_FEE_PCT=0.02 or MAKER_FEE_PCT=0.00).
    A pairs trade has 4 legs (entry A, entry B, exit A, exit B), so the
    total fee cost is 4 × fee_pct × half_size = 2 × fee_pct × size_usd.
    EV returned is NET of fees so callers compare directly to a hurdle rate.
    """
    from scipy.stats import norm
    abs_z = abs(z_score)
    base_prob = 2 * norm.cdf(abs_z) - 1  # e.g., z=2 → 0.954

    if half_life <= 0 or half_life > 100:
        hl_factor = 0.3
    else:
        hl_factor = min(1.0, 3.0 / half_life)

    win_prob = base_prob * hl_factor

    # Gross payoff: spread reverts to mean (z → 0)
    win_payout_gross = abs_z * spread_std * size_usd
    loss_amount_gross = 1.0 * spread_std * size_usd

    # Round-trip fee cost (4 legs total)
    fee_cost = 2 * fee_pct * size_usd

    # Net payoffs after fees
    win_payout_net  = max(0.0, win_payout_gross - fee_cost)
    loss_amount_net = loss_amount_gross + fee_cost

    ev = expected_value(win_prob, win_payout_net, loss_amount_net)

    log.debug(
        "EV calc: z=%.2f hl=%.1f spread_std=%.4f fee_pct=%.0f%% "
        "gross_win=%.2f fee=%.2f net_win=%.2f ev=%.4f",
        z_score, half_life, spread_std, fee_pct * 100,
        win_payout_gross, fee_cost, win_payout_net, ev,
    )

    return {
        "ev": round(ev, 4),
        "ev_pct": round(ev / size_usd * 100, 2) if size_usd else 0,
        "win_prob": round(win_prob, 4),
        "win_payout": round(win_payout_net, 4),
        "win_payout_gross": round(win_payout_gross, 4),
        "loss_amount": round(loss_amount_net, 4),
        "fee_cost": round(fee_cost, 4),
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


def score_opportunity(opp, bankroll=1000, min_ev_pct=2.0, max_slippage_pct=2.0,
                      fee_pct=TAKER_FEE_PCT, min_spread_std=MIN_SPREAD_STD):
    """Score a signal through all Tier 1 filters.

    min_ev_pct is now NET of fees (2% default). Pass fee_pct=MAKER_FEE_PCT
    when executing as a maker to relax the EV hurdle appropriately.
    """
    spread_std = opp.get("spread_std") or 0

    # EV calculation (fee-aware)
    ev = ev_from_zscore(
        z_score=opp["z_score"],
        half_life=opp["half_life"],
        spread_std=spread_std,
        size_usd=100,
        fee_pct=fee_pct,
    )

    # Kelly sizing
    sizing = position_size(bankroll, ev)

    # Price filter: reject if either market is near resolution (outside 5%–95%)
    price_a = float(opp.get("price_a") or 0)
    price_b = float(opp.get("price_b") or 0)
    price_ok = (0.05 <= price_a <= 0.95) and (0.05 <= price_b <= 0.95)

    # Filters
    filters = {
        "ev_pass":        bool(ev["ev_pct"] >= min_ev_pct),
        "kelly_pass":     bool(sizing["kelly_fraction"] > 0),
        "z_pass":         bool(abs(opp["z_score"]) >= 1.5),
        "coint_pass":     bool(opp["coint_pvalue"] < 0.10),
        "hl_pass":        bool(opp["half_life"] < 20),
        "momentum_pass":  bool(opp.get("spread_retreating", True)),
        "price_pass":     bool(price_ok),
        # Spread must have enough volatility to pay off after fees
        "spread_std_pass": bool(spread_std >= min_spread_std),
    }

    grade = sum(filters.values())
    all_pass = all(filters.values())

    # 8 filters → need all 8 for A+
    label = ["F", "D", "C", "B", "A", "A", "A", "A", "A+"][min(grade, 8)]
    log.info(
        "Scored: %s | grade=%s ev=%.2f%%(net) spread_std=%.4f z=%.2f tradeable=%s",
        opp.get("event", "?")[:40], label, ev["ev_pct"], spread_std,
        opp["z_score"], all_pass,
    )

    return {
        **opp,
        "ev": ev,
        "sizing": sizing,
        "filters": filters,
        "grade": grade,
        "tradeable": all_pass,
        "grade_label": label,
        "fee_pct": fee_pct,
    }
