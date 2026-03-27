"""Async cointegration scanner — parallel API calls via httpx.

Same logic as scanner.py but fetches all price histories concurrently.
Typical speedup: 2min → 15-20sec.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
import numpy as np
from statsmodels.tsa.stattools import coint
from sklearn.linear_model import LinearRegression

import async_api
import math_engine
from log_setup import init_logging

init_logging()
log = logging.getLogger("scanner.async")

MIN_DAYS_TO_RESOLUTION = 21


def _days_to_resolution(end_date_str):
    """Return days until market resolves, or inf if unknown/unparseable."""
    if not end_date_str:
        return float("inf")
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0, (end - datetime.now(timezone.utc)).days)
    except (ValueError, TypeError):
        return float("inf")


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
    if not hist_a or not hist_b:
        return None, None

    map_a = {h["t"]: h["p"] for h in hist_a}
    map_b = {h["t"]: h["p"] for h in hist_b}
    common_ts = sorted(set(map_a.keys()) & set(map_b.keys()))

    if len(common_ts) < 20:
        if len(hist_a) >= 20 and len(hist_b) >= 20:
            min_len = min(len(hist_a), len(hist_b))
            return (np.array([h["p"] for h in hist_a[-min_len:]]),
                    np.array([h["p"] for h in hist_b[-min_len:]]))
        return None, None

    return (np.array([map_a[t] for t in common_ts]),
            np.array([map_b[t] for t in common_ts]))


def _test_pair(prices_a, prices_b):
    """Run cointegration test (same as scanner.test_pair)."""
    if prices_a is None or prices_b is None:
        return None
    if len(prices_a) < 20 or len(prices_b) < 20:
        return None
    if np.std(prices_a) < 0.001 or np.std(prices_b) < 0.001:
        return None

    try:
        score, pvalue, crit_values = coint(prices_a, prices_b)
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

    # Filter #2: spread momentum
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


async def scan(z_threshold=1.5, p_threshold=0.10, min_liquidity=5000,
               interval="1w", fidelity=100, verbose=True):
    """Async scan — fetches all prices in parallel, then tests pairs."""
    try:
        events = await find_multi_market_events(min_liquidity=min_liquidity)
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        return []

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
    histories = await async_api.get_price_histories(
        list(all_tokens), interval=interval, fidelity=fidelity, max_concurrent=15
    )

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

    await async_api.close()
    return opportunities


if __name__ == "__main__":
    results = asyncio.run(scan())
    for r in results[:5]:
        print(f"[{r.get('grade_label','?')}] z={r['z_score']:+.2f} | {r['action'][:60]}")
