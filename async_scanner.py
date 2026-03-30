"""Async cointegration scanner — parallel API calls via httpx.

Same logic as scanner.py but fetches all price histories concurrently.
Typical speedup: 2min → 15-20sec.
"""
import asyncio
import json
import logging
import time

import async_api
import math_engine
from log_setup import init_logging
from scanner_core import MIN_DAYS_TO_RESOLUTION, align_prices, days_to_resolution, test_pair as core_test_pair

init_logging()
log = logging.getLogger("scanner.async")


def _days_to_resolution(end_date_str):
    return days_to_resolution(end_date_str)


async def find_multi_market_events(min_markets=2, max_markets=15, min_liquidity=1000):
    """Get events with multiple active markets."""
    events = await async_api.get_events(limit=100, offset=0)
    log.info("Fetched %d events from API", len(events))

    candidates = []
    for e in events:
        markets = e.get("markets", [])
        active_markets = []
        for m in markets:
            tokens = m.get("clobTokenIds", "")
            if not tokens or tokens == "[]":
                continue
            prices = m.get("outcomePrices", "")
            if not prices:
                continue
            try:
                price_list = json.loads(prices) if isinstance(prices, str) else prices
                yes_price = float(price_list[0]) if price_list else 0
            except Exception:
                yes_price = 0
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
        if min_markets <= len(active_markets) <= max_markets and liq >= min_liquidity:
            candidates.append({
                "title": e.get("title", ""),
                "event_id": e.get("id", ""),
                "liquidity": liq,
                "volume_24h": e.get("volume24hr", 0) or 0,
                "markets": active_markets,
            })

    candidates.sort(key=lambda x: x["liquidity"], reverse=True)
    return candidates


def _align_prices(hist_a, hist_b):
    """Align two price history lists by timestamp."""
    return align_prices(hist_a, hist_b)


def _test_pair(prices_a, prices_b):
    """Run cointegration test (same as scanner.test_pair)."""
    return core_test_pair(prices_a, prices_b)


async def scan(z_threshold=1.5, p_threshold=0.10, min_liquidity=5000,
               interval="1w", fidelity=100, verbose=True, include_stats=False):
    """Async scan — fetches all prices in parallel, then tests pairs."""
    try:
        events = await find_multi_market_events(min_liquidity=min_liquidity)
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        empty = {"opportunities": [], "pairs_tested": 0, "pairs_cointegrated": 0}
        return empty if include_stats else []

    log.info("Found %d events with 2+ active markets", len(events))

    # Collect all unique tokens we need prices for
    all_tokens = set()
    for event in events:
        for m in event["markets"]:
            if m["yes_token"]:
                all_tokens.add(m["yes_token"])

    log.info("Fetching price histories for %d tokens in parallel...", len(all_tokens))
    t0 = time.time()

    # Fetch all price histories in parallel
    try:
        histories = await async_api.get_price_histories(
            list(all_tokens), interval=interval, fidelity=fidelity, max_concurrent=15
        )
    finally:
        await async_api.close()

    fetch_time = time.time() - t0
    log.info("Fetched %d histories in %.1fs", len(histories), fetch_time)

    # Now test pairs (CPU-bound, no async needed)
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

                hist_a = histories.get(m_a["yes_token"], [])
                hist_b = histories.get(m_b["yes_token"], [])

                prices_a, prices_b = _align_prices(hist_a, hist_b)
                result = _test_pair(prices_a, prices_b)
                pairs_tested += 1

                if result is None:
                    continue

                if result["coint_pvalue"] < p_threshold:
                    pairs_cointegrated += 1

                    if abs(result["z_score"]) >= z_threshold:
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
                            log.warning("Scoring failed: %s", e)
                            opp.update({"grade_label": "?", "tradeable": False})

                        opportunities.append(opp)

                        if verbose:
                            z = result["z_score"]
                            p = result["coint_pvalue"]
                            hl = result["half_life"]
                            hl_str = f"{hl:.1f}pts" if hl < 1000 else "slow"
                            gl = opp.get("grade_label", "?")
                            print(f"  *** z={z:+.2f} p={p:.4f} hl={hl_str} [{gl}] | {action}")

    opportunities.sort(key=lambda x: abs(x["z_score"]), reverse=True)

    log.info("Async scan: %d tested, %d cointegrated, %d diverged (fetch=%.1fs)",
             pairs_tested, pairs_cointegrated, len(opportunities), fetch_time)

    if include_stats:
        return {
            "opportunities": opportunities,
            "pairs_tested": pairs_tested,
            "pairs_cointegrated": pairs_cointegrated,
        }

    return opportunities


if __name__ == "__main__":
    results = asyncio.run(scan())
    for r in results[:5]:
        print(f"[{r.get('grade_label','?')}] z={r['z_score']:+.2f} | {r['action'][:60]}")
