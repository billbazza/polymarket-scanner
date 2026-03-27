"""Locked market arbitrage scanner.

Finds binary markets where YES + NO prices sum to < $1.00,
enabling risk-free guaranteed profit by buying both sides.

Strategy:
  Buy YES at yes_price + Buy NO at no_price = total_cost
  At resolution, exactly one side pays $1.00.
  Gross profit = $1.00 - total_cost

Polymarket taker fee: ~2% per trade (applied to each leg).
Net profit = $1.00 - yes_price - no_price - fee_rate*(yes_price + no_price)

Break-even sum at 2% fees: 1.0 / 1.02 ≈ 0.980
We require ≥ 0.5% net margin to surface an opportunity.
"""
import json
import logging
import time

import api
import math_engine

log = logging.getLogger("scanner.locked")

FEE_RATE = 0.02        # Polymarket taker fee per leg
MIN_NET_GAP = 0.005    # minimum net profit per $1 resolved (0.5%)
MIN_LIQUIDITY = 500    # minimum event liquidity to bother checking


def _parse_market(market, event_title, event_liquidity):
    """Extract arb data from a single market dict. Returns dict or None."""
    tokens_raw = market.get("clobTokenIds", "")
    prices_raw = market.get("outcomePrices", "")

    if not tokens_raw or tokens_raw == "[]" or not prices_raw:
        return None

    try:
        token_list = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
        price_list = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    except (json.JSONDecodeError, TypeError):
        return None

    if len(token_list) < 2 or len(price_list) < 2:
        return None

    try:
        yes_price = float(price_list[0])
        no_price = float(price_list[1])
    except (ValueError, TypeError):
        return None

    # Skip resolved or near-resolved markets
    if yes_price <= 0.01 or yes_price >= 0.99:
        return None
    if no_price <= 0.01 or no_price >= 0.99:
        return None

    yes_token = token_list[0]
    no_token = token_list[1]

    sum_price = yes_price + no_price
    gap_gross = 1.0 - sum_price
    # Fee applies to both legs (we pay fee_rate on each purchase)
    fees = FEE_RATE * sum_price
    gap_net = gap_gross - fees
    net_profit_pct = round(gap_net * 100, 3)

    return {
        "event": event_title,
        "market": market.get("question", market.get("groupItemTitle", "")),
        "market_id": str(market.get("id", "")),
        "yes_token": yes_token,
        "no_token": no_token,
        "yes_price": round(yes_price, 4),
        "no_price": round(no_price, 4),
        "sum_price": round(sum_price, 4),
        "gap_gross": round(gap_gross, 4),
        "gap_net": round(gap_net, 4),
        "net_profit_pct": net_profit_pct,
        "liquidity": event_liquidity,
    }


def scan(min_net_gap=MIN_NET_GAP, min_liquidity=MIN_LIQUIDITY,
         check_slippage=True, trade_size_usd=100, verbose=True):
    """Scan all active markets for locked-market arbitrage opportunities.

    Args:
        min_net_gap: minimum net profit per $1 resolved (default 0.005 = 0.5%)
        min_liquidity: minimum event liquidity to include
        check_slippage: whether to verify order book depth on each opportunity
        trade_size_usd: trade size used for slippage check
        verbose: print progress to stdout

    Returns:
        list of opportunity dicts, sorted by net_profit_pct descending
    """
    t0 = time.time()

    if verbose:
        print("Fetching active events...", end=" ", flush=True)

    try:
        events = api.get_all_active_events(max_pages=10)
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        return []

    if verbose:
        print(f"{len(events)} events")

    candidates = []
    markets_checked = 0

    for event in events:
        liq = float(event.get("liquidity", 0) or 0)
        if liq < min_liquidity:
            continue

        event_title = event.get("title", "")
        markets = event.get("markets", [])

        for market in markets:
            markets_checked += 1
            result = _parse_market(market, event_title, liq)
            if result is None:
                continue

            if result["gap_net"] >= min_net_gap:
                candidates.append(result)

    if verbose:
        print(f"Checked {markets_checked} markets → {len(candidates)} raw candidates")

    if not candidates:
        log.info("No locked-market opportunities found (checked %d markets)", markets_checked)
        return []

    # Optionally verify order book depth on each candidate
    if check_slippage:
        if verbose:
            print(f"Checking slippage on {len(candidates)} candidates...")

        confirmed = []
        for opp in candidates:
            yes_slip = math_engine.check_slippage(
                opp["yes_token"], trade_size_usd=trade_size_usd
            )
            no_slip = math_engine.check_slippage(
                opp["no_token"], trade_size_usd=trade_size_usd
            )

            opp["yes_slippage_ok"] = yes_slip["ok"]
            opp["no_slippage_ok"] = no_slip["ok"]
            opp["yes_slippage_pct"] = yes_slip.get("slippage_pct")
            opp["no_slippage_pct"] = no_slip.get("slippage_pct")
            opp["tradeable"] = yes_slip["ok"] and no_slip["ok"]

            confirmed.append(opp)

            log.debug(
                "Slippage check %s: yes_ok=%s no_ok=%s gap_net=%.3f%%",
                opp["market"][:40], yes_slip["ok"], no_slip["ok"], opp["net_profit_pct"],
            )
    else:
        for opp in candidates:
            opp["yes_slippage_ok"] = None
            opp["no_slippage_ok"] = None
            opp["yes_slippage_pct"] = None
            opp["no_slippage_pct"] = None
            opp["tradeable"] = False  # unknown until slippage checked
        confirmed = candidates

    # Sort: tradeable first, then by net profit descending
    confirmed.sort(key=lambda x: (not x["tradeable"], -x["gap_net"]))

    duration = round(time.time() - t0, 1)
    tradeable_count = sum(1 for o in confirmed if o["tradeable"])

    log.info(
        "Locked scan complete: %d markets checked, %d opportunities, %d tradeable in %.1fs",
        markets_checked, len(confirmed), tradeable_count, duration,
    )

    if verbose:
        print(f"\nFound {len(confirmed)} opportunities ({tradeable_count} tradeable) in {duration}s\n")
        for opp in confirmed[:10]:
            flag = "TRADE" if opp["tradeable"] else "check"
            print(
                f"  [{flag}] {opp['event'][:35]:35s} | "
                f"YES={opp['yes_price']:.3f} NO={opp['no_price']:.3f} "
                f"sum={opp['sum_price']:.3f} net={opp['net_profit_pct']:+.2f}%"
            )

    return confirmed
