from __future__ import annotations

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
MIN_PRICE_BOUND = 0.05
MAX_PRICE_BOUND = 0.95
DEFAULT_MIN_Z_ABS = 1.5
DEFAULT_MAX_COINT_PVALUE = 0.10
DEFAULT_MAX_HALF_LIFE = 20.0

# ── Category maker-edge table ───────────────────────────────────────────────
# Empirical maker edge (%) by market category from research/ideas01.md.
# Used to adjust the min_ev_pct hurdle in score_opportunity():
#   - High-edge categories: relax hurdle (easier to pass)
#   - Low-edge categories: tighten hurdle (require more signal)
# Edge values represent the average per-trade advantage makers have in each
# category relative to takers. "finance" barely moves — avoid unless signal
# is very strong.
CATEGORY_MAKER_EDGE = {
    "world_events":  3.66,
    "entertainment": 2.40,
    "crypto":        1.34,
    "sports":        1.11,
    "politics":      0.51,
    "finance":       0.08,
}
# Default hurdle when category is unknown
DEFAULT_MIN_EV_PCT = 2.0

FILTER_ORDER = (
    "ev_pass",
    "kelly_pass",
    "z_pass",
    "coint_pass",
    "hl_pass",
    "momentum_pass",
    "price_pass",
    "spread_std_pass",
)
PRIMARY_REJECTION_PRIORITY = (
    "price_pass",
    "spread_std_pass",
    "coint_pass",
    "z_pass",
    "hl_pass",
    "momentum_pass",
    "ev_pass",
    "kelly_pass",
)
MONITORABLE_FILTERS = (
    "kelly_pass",
    "z_pass",
    "coint_pass",
    "hl_pass",
    "momentum_pass",
    "price_pass",
    "spread_std_pass",
)


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


def position_size(bankroll, ev_result, correlated_legs=False):
    """Convenience: compute Kelly size from EV result.

    correlated_legs: set True for pairs trades where both legs are exposed to
    the same event. Kelly assumes independent bets; correlated legs double-count
    the edge, so we halve the fraction to correct for it.
    """
    f = kelly_fraction(ev_result["win_prob"], ev_result["win_payout"], ev_result["loss_amount"])
    if correlated_legs:
        f = round(f / 2, 4)
    return {
        "kelly_fraction": f,
        "recommended_size": round(bankroll * f, 2),
        "max_size": round(bankroll * 0.25, 2),  # never more than 25% of bankroll
        "correlated_legs": correlated_legs,
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


def category_ev_hurdle(category: str, base_min_ev_pct: float = DEFAULT_MIN_EV_PCT) -> float:
    """Adjust EV hurdle based on market category maker edge.

    High-edge categories (world events, entertainment) get a lower hurdle —
    the structural maker advantage means we need less signal to have positive
    expected outcome. Low-edge categories (finance) require stronger signals.

    The adjustment is linear: category_edge / 2.0 subtracted from the hurdle,
    capped so we never go below 0.5% (we always need some positive EV).
    """
    edge = CATEGORY_MAKER_EDGE.get(category, 0.0)
    adjusted = base_min_ev_pct - (edge / 2.0)
    return max(0.5, round(adjusted, 2))


def summarize_filters(filters: dict | None) -> dict:
    """Summarize pass/fail state for downstream admission decisions."""
    filters = filters or {}
    failed = [name for name in FILTER_ORDER if name in filters and not filters[name]]
    failed.extend(
        name for name, passed in filters.items()
        if name not in FILTER_ORDER and not passed
    )
    failed_set = set(failed)
    monitorable = all(filters.get(name, False) for name in MONITORABLE_FILTERS)
    ev_only = failed == ["ev_pass"]
    return {
        "failed_filters": failed,
        "failed_filter_count": len(failed),
        "monitorable_signal": monitorable,
        "ev_only_near_miss": monitorable and ev_only,
        "all_pass": not failed,
        "primary_failed_filter": next(
            (name for name in PRIMARY_REJECTION_PRIORITY if name in failed_set),
            failed[0] if failed else None,
        ),
    }


def build_admission_diagnostics(
    opp: dict,
    filters: dict,
    *,
    effective_min_ev_pct: float,
    min_z_abs: float,
    max_coint_pvalue: float,
    max_half_life: float,
    min_spread_std: float,
    min_price: float,
    max_price: float,
) -> dict:
    """Return structured operator-facing admission diagnostics."""
    summary = summarize_filters(filters)
    primary = summary["primary_failed_filter"]
    z_abs = abs(float(opp.get("z_score") or 0.0))
    coint_pvalue = float(opp.get("coint_pvalue") or 0.0)
    half_life = float(opp.get("half_life") or 0.0)
    spread_std = float(opp.get("spread_std") or 0.0)
    price_a = float(opp.get("price_a") or 0.0)
    price_b = float(opp.get("price_b") or 0.0)

    thresholds = {
        "min_ev_pct": round(float(effective_min_ev_pct), 4),
        "min_z_abs": round(float(min_z_abs), 4),
        "max_coint_pvalue": round(float(max_coint_pvalue), 4),
        "max_half_life": round(float(max_half_life), 4),
        "min_spread_std": round(float(min_spread_std), 4),
        "min_price": round(float(min_price), 4),
        "max_price": round(float(max_price), 4),
    }

    observed = {
        "ev_pct": round(float((opp.get("ev") or {}).get("ev_pct") or 0.0), 4),
        "kelly_fraction": round(float((opp.get("sizing") or {}).get("kelly_fraction") or 0.0), 4),
        "z_abs": round(z_abs, 4),
        "coint_pvalue": round(coint_pvalue, 6),
        "half_life": round(half_life, 4),
        "spread_std": round(spread_std, 6),
        "price_a": round(price_a, 4),
        "price_b": round(price_b, 4),
        "spread_retreating": bool(opp.get("spread_retreating", True)),
    }

    if not primary:
        status = "tradeable" if summary["all_pass"] else "accepted"
        reason_code = "accepted"
        reason = "All admission filters passed."
    elif primary == "ev_pass":
        status = "monitor"
        reason_code = "ev_below_hurdle"
        reason = (
            f"EV {observed['ev_pct']:.2f}% is below the hurdle "
            f"{thresholds['min_ev_pct']:.2f}%."
        )
    elif primary == "kelly_pass":
        status = "rejected"
        reason_code = "kelly_non_positive"
        reason = "Kelly fraction is non-positive."
    elif primary == "z_pass":
        status = "rejected"
        reason_code = "z_below_threshold"
        reason = f"|z| {z_abs:.2f} is below the scan threshold {min_z_abs:.2f}."
    elif primary == "coint_pass":
        status = "rejected"
        reason_code = "cointegration_too_weak"
        reason = (
            f"Cointegration p-value {coint_pvalue:.4f} exceeds the scan cap "
            f"{max_coint_pvalue:.4f}."
        )
    elif primary == "hl_pass":
        status = "rejected"
        reason_code = "half_life_too_slow"
        reason = (
            f"Half-life {half_life:.1f} exceeds the admissible cap "
            f"{max_half_life:.1f}."
        )
    elif primary == "momentum_pass":
        status = "rejected"
        reason_code = "spread_still_widening"
        reason = "Spread is still widening instead of retreating toward mean."
    elif primary == "price_pass":
        status = "rejected"
        reason_code = "price_outside_operating_band"
        reason = (
            f"Prices {price_a:.2f}/{price_b:.2f} sit outside the operating band "
            f"{min_price:.2f}-{max_price:.2f}."
        )
    elif primary == "spread_std_pass":
        status = "rejected"
        reason_code = "spread_too_tight"
        reason = (
            f"Spread std {spread_std:.4f} is below the minimum {min_spread_std:.4f}."
        )
    else:
        status = "rejected"
        reason_code = "filter_failed"
        reason = f"Filter {primary} failed."

    return {
        "status": status,
        "accepted": summary["all_pass"],
        "monitorable_signal": summary["monitorable_signal"],
        "ev_only_near_miss": summary["ev_only_near_miss"],
        "primary_reason_code": reason_code,
        "primary_reason": reason,
        "failed_filters": summary["failed_filters"],
        "failed_filter_count": summary["failed_filter_count"],
        "primary_failed_filter": primary,
        "thresholds": thresholds,
        "observed": observed,
    }


def score_opportunity(opp, bankroll=1000, min_ev_pct=DEFAULT_MIN_EV_PCT,
                      max_slippage_pct=2.0, fee_pct=TAKER_FEE_PCT,
                      min_spread_std=MIN_SPREAD_STD, correlated_legs=False,
                      min_z_abs=DEFAULT_MIN_Z_ABS,
                      max_coint_pvalue=DEFAULT_MAX_COINT_PVALUE,
                      max_half_life=DEFAULT_MAX_HALF_LIFE):
    """Score a signal through all Tier 1 filters.

    min_ev_pct is NET of fees (2% default). Pass fee_pct=MAKER_FEE_PCT
    when executing as a maker to relax the EV hurdle appropriately.

    correlated_legs=True halves Kelly for pairs trades (both legs exposed to
    the same event — standard Kelly over-sizes correlated positions).

    If opp contains a 'category' key, the EV hurdle is automatically adjusted
    based on the category's empirical maker edge (CATEGORY_MAKER_EDGE table).
    """
    spread_std = opp.get("spread_std") or 0

    # Adjust EV hurdle by category if available
    category = opp.get("category", "")
    effective_min_ev_pct = category_ev_hurdle(category, min_ev_pct) if category else min_ev_pct

    # EV calculation (fee-aware)
    ev = ev_from_zscore(
        z_score=opp["z_score"],
        half_life=opp["half_life"],
        spread_std=spread_std,
        size_usd=100,
        fee_pct=fee_pct,
    )

    # Kelly sizing (halved for correlated two-leg pairs trades)
    sizing = position_size(bankroll, ev, correlated_legs=correlated_legs)

    # Price filter: reject if either market is near resolution (outside 5%–95%)
    price_a = float(opp.get("price_a") or 0)
    price_b = float(opp.get("price_b") or 0)
    price_ok = (MIN_PRICE_BOUND <= price_a <= MAX_PRICE_BOUND) and (MIN_PRICE_BOUND <= price_b <= MAX_PRICE_BOUND)

    # Filters
    filters = {
        "ev_pass":        bool(ev["ev_pct"] >= effective_min_ev_pct),
        "kelly_pass":     bool(sizing["kelly_fraction"] > 0),
        "z_pass":         bool(abs(opp["z_score"]) >= min_z_abs),
        "coint_pass":     bool(opp["coint_pvalue"] < max_coint_pvalue),
        "hl_pass":        bool(opp["half_life"] < max_half_life),
        "momentum_pass":  bool(opp.get("spread_retreating", True)),
        "price_pass":     bool(price_ok),
        # Spread must have enough volatility to pay off after fees
        "spread_std_pass": bool(spread_std >= min_spread_std),
    }
    opp["ev"] = ev
    opp["sizing"] = sizing
    admission = build_admission_diagnostics(
        opp,
        filters,
        effective_min_ev_pct=effective_min_ev_pct,
        min_z_abs=min_z_abs,
        max_coint_pvalue=max_coint_pvalue,
        max_half_life=max_half_life,
        min_spread_std=min_spread_std,
        min_price=MIN_PRICE_BOUND,
        max_price=MAX_PRICE_BOUND,
    )

    grade = sum(filters.values())
    all_pass = all(filters.values())

    # 8 filters → need all 8 for A+
    label = ["F", "D", "C", "B", "A", "A", "A", "A", "A+"][min(grade, 8)]
    log.info(
        "Scored: %s | grade=%s ev=%.2f%%(net) hurdle=%.1f%% category=%s "
        "spread_std=%.4f z=%.2f tradeable=%s blocker=%s",
        opp.get("event", "?")[:40], label, ev["ev_pct"], effective_min_ev_pct,
        category or "unknown", spread_std, opp["z_score"], all_pass,
        admission["primary_reason_code"],
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
        "category": category,
        "effective_min_ev_pct": effective_min_ev_pct,
        "admission": admission,
    }
