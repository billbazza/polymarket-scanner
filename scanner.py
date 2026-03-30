"""Cointegration scanner for Polymarket.

Finds pairs of related markets where the price spread has diverged
beyond normal bounds — potential mean-reversion opportunities.

How it works:
1. Fetch all active events with multiple markets (natural pairs)
2. Pull price history for each market's YES token
3. Run cointegration tests on all pairs within each event
4. For cointegrated pairs, compute z-score of current spread
5. Flag pairs where |z| > threshold (spread has diverged)
"""
import json
import logging
import sys

import api
import math_engine
from log_setup import init_logging
from scanner_core import MIN_DAYS_TO_RESOLUTION, align_prices, days_to_resolution, test_pair as core_test_pair

init_logging()
log = logging.getLogger("scanner.core")


def _days_to_resolution(end_date_str):
    return days_to_resolution(end_date_str)


def find_multi_market_events(min_markets=2, max_markets=15, min_liquidity=1000, max_events=200):
    """Get events that have multiple active markets — these are natural pairs.

    Caps at max_markets to skip massive sports/election events where
    pairwise testing would be O(n^2) API calls for little signal.
    """
    print(f"Fetching active events...", end=" ", flush=True)
    events = api.get_events(limit=100, offset=0)
    print(f"{len(events)} total")

    candidates = []
    for e in events:
        markets = e.get("markets", [])
        # Filter to markets with actual token IDs and liquidity
        active_markets = []
        for m in markets:
            tokens = m.get("clobTokenIds", "")
            # tokens is a JSON string of array, or empty
            if not tokens or tokens == "[]":
                continue
            # Parse outcome prices
            prices = m.get("outcomePrices", "")
            if not prices:
                continue
            try:
                price_list = json.loads(prices) if isinstance(prices, str) else prices
                yes_price = float(price_list[0]) if price_list else 0
            except:
                yes_price = 0
            # Skip resolved markets (price = 0 or 1)
            if yes_price <= 0.01 or yes_price >= 0.99:
                continue

            token_list = json.loads(tokens) if isinstance(tokens, str) else tokens
            active_markets.append({
                "question": m.get("question", m.get("groupItemTitle", "")),
                "yes_token": token_list[0] if token_list else "",
                "yes_price": yes_price,
                "market_id": m.get("id", ""),
                "end_date": m.get("endDate", ""),
            })

        liq = e.get("liquidity", 0) or 0
        if len(active_markets) >= min_markets and len(active_markets) <= max_markets and liq >= min_liquidity:
            candidates.append({
                "title": e.get("title", ""),
                "event_id": e.get("id", ""),
                "liquidity": liq,
                "volume_24h": e.get("volume24hr", 0) or 0,
                "markets": active_markets,
            })

    # Sort by liquidity
    candidates.sort(key=lambda x: x["liquidity"], reverse=True)
    return candidates[:max_events]


def get_aligned_prices(token_a, token_b, interval="1w", fidelity=100):
    """Fetch price history for two tokens and align by timestamp."""
    hist_a = api.get_price_history(token_a, interval=interval, fidelity=fidelity)
    hist_b = api.get_price_history(token_b, interval=interval, fidelity=fidelity)
    return align_prices(hist_a, hist_b)


def test_pair(prices_a, prices_b):
    """Run cointegration test and compute spread statistics."""
    return core_test_pair(prices_a, prices_b)


def scan(z_threshold=1.5, p_threshold=0.10, min_liquidity=5000,
         interval="1w", fidelity=100, verbose=True, include_stats=False):
    """Run the full scan. Returns list of opportunities sorted by |z-score|.

    Args:
        z_threshold: minimum |z-score| to flag (default 1.5, use 2.0 for stricter)
        p_threshold: max cointegration p-value (default 0.10, use 0.05 for stricter)
        min_liquidity: minimum event liquidity in USD
        interval: price history window ("1d", "1w", "1m")
        fidelity: number of price data points
        verbose: print progress
    """
    try:
        events = find_multi_market_events(min_liquidity=min_liquidity)
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        empty = {"opportunities": [], "pairs_tested": 0, "pairs_cointegrated": 0}
        return empty if include_stats else []

    log.info("Found %d events with 2+ active markets", len(events))
    if verbose:
        print(f"Found {len(events)} events with 2+ active markets\n")

    opportunities = []
    pairs_tested = 0
    pairs_cointegrated = 0

    for event in events:
        markets = event["markets"]
        n = len(markets)
        if n < 2:
            continue

        if verbose:
            print(f"[{event['title'][:60]}] ({n} markets, ${event['liquidity']:,.0f} liq)")

        # Test all pairs within the event
        for i in range(n):
            for j in range(i + 1, n):
                m_a = markets[i]
                m_b = markets[j]

                if not m_a["yes_token"] or not m_b["yes_token"]:
                    continue

                # Filter #1: skip pairs resolving too soon
                days_a = _days_to_resolution(m_a.get("end_date", ""))
                days_b = _days_to_resolution(m_b.get("end_date", ""))
                if days_a < MIN_DAYS_TO_RESOLUTION or days_b < MIN_DAYS_TO_RESOLUTION:
                    log.debug("Skip near-expiry pair: %s (%.0fd) / %s (%.0fd)",
                              m_a["question"][:30], days_a, m_b["question"][:30], days_b)
                    continue

                try:
                    prices_a, prices_b = get_aligned_prices(
                        m_a["yes_token"], m_b["yes_token"],
                        interval=interval, fidelity=fidelity,
                    )
                except Exception as e:
                    log.warning("Price fetch failed for pair in '%s': %s",
                                event["title"][:40], e)
                    continue

                result = test_pair(prices_a, prices_b)
                pairs_tested += 1

                if result is None:
                    continue

                if result["coint_pvalue"] < p_threshold:
                    pairs_cointegrated += 1

                    if abs(result["z_score"]) >= z_threshold:
                        # Determine direction
                        if result["z_score"] > 0:
                            action = f"SELL {m_a['question'][:40]} / BUY {m_b['question'][:40]}"
                        else:
                            action = f"BUY {m_a['question'][:40]} / SELL {m_b['question'][:40]}"

                        opp = {
                            "event": event["title"],
                            "market_a": m_a["question"],
                            "market_b": m_b["question"],
                            "token_id_a": m_a["yes_token"],
                            "token_id_b": m_b["yes_token"],
                            "price_a": m_a["yes_price"],
                            "price_b": m_b["yes_price"],
                            "liquidity": event["liquidity"],
                            "volume_24h": event["volume_24h"],
                            "action": action,
                            **result,
                        }

                        # Score through Tier 1 filters
                        try:
                            scored = math_engine.score_opportunity(opp)
                            opp.update({
                                "ev": scored["ev"],
                                "sizing": scored["sizing"],
                                "filters": scored["filters"],
                                "grade": scored["grade"],
                                "grade_label": scored["grade_label"],
                                "tradeable": scored["tradeable"],
                            })
                        except Exception as e:
                            log.warning("Scoring failed for %s: %s",
                                        event["title"][:40], e)
                            opp.update({
                                "grade_label": "?",
                                "tradeable": False,
                            })

                        opportunities.append(opp)

                        if verbose:
                            z = result["z_score"]
                            p = result["coint_pvalue"]
                            hl = result["half_life"]
                            hl_str = f"{hl:.1f}pts" if hl < 1000 else "slow"
                            gl = opp.get("grade_label", "?")
                            print(f"  *** z={z:+.2f} p={p:.4f} hl={hl_str} [{gl}] | {action}")

    # Sort by |z-score| descending
    opportunities.sort(key=lambda x: abs(x["z_score"]), reverse=True)

    log.info("Scan complete: %d tested, %d cointegrated, %d diverged",
             pairs_tested, pairs_cointegrated, len(opportunities))

    if verbose:
        print(f"\n{'='*60}")
        print(f"Pairs tested: {pairs_tested}")
        print(f"Cointegrated (p<{p_threshold}): {pairs_cointegrated}")
        print(f"Diverged (|z|>{z_threshold}): {len(opportunities)}")
        print(f"{'='*60}")

    if include_stats:
        return {
            "opportunities": opportunities,
            "pairs_tested": pairs_tested,
            "pairs_cointegrated": pairs_cointegrated,
        }

    return opportunities


def format_opportunity(opp, rank=1):
    """Format a single opportunity for display."""
    lines = [
        f"{'='*60}",
        f"#{rank} | z-score: {opp['z_score']:+.2f} | p-value: {opp['coint_pvalue']:.4f} | Grade: {opp.get('grade_label', '?')}",
        f"{'='*60}",
        f"Event:    {opp['event']}",
        f"Market A: {opp['market_a']}  (YES @ {opp['price_a']:.1%})",
        f"Market B: {opp['market_b']}  (YES @ {opp['price_b']:.1%})",
        f"Beta:     {opp['beta']:.3f}",
        f"Half-life: {opp['half_life']:.1f} periods" if opp['half_life'] < 1000 else "Half-life: slow reversion",
        f"Liquidity: ${opp['liquidity']:,.0f} | 24h vol: ${opp['volume_24h']:,.0f}",
    ]

    # Tier 1 scoring
    ev = opp.get("ev")
    sizing = opp.get("sizing")
    if ev:
        lines.append(f"EV:       {ev['ev_pct']}% (win_prob={ev['win_prob']:.3f}, payout=${ev['win_payout']:.2f})")
    if sizing:
        lines.append(f"Kelly:    {sizing['kelly_fraction']:.4f} → ${sizing['recommended_size']:.2f}")
    if opp.get("tradeable") is not None:
        lines.append(f"Tradeable: {'YES' if opp['tradeable'] else 'NO'}")

    lines.extend([
        f"",
        f"Signal:   {opp['action']}",
        f"",
        f"Spread is {abs(opp['z_score']):.1f} std devs from mean.",
        f"{'Spread is ABOVE mean — A is overpriced relative to B.' if opp['z_score'] > 0 else 'Spread is BELOW mean — A is underpriced relative to B.'}",
    ])
    return "\n".join(lines)
