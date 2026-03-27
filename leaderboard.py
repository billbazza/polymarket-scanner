#!/usr/bin/env python3
"""Polymarket leaderboard analysis — fetch top traders and analyze patterns.

Usage:
    python3 leaderboard.py             # top 20 traders
    python3 leaderboard.py --top 10    # top 10 traders
"""
import argparse
import logging
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from log_setup import init_logging

init_logging()
log = logging.getLogger("scanner.leaderboard")

GAMMA_BASE = "https://gamma-api.polymarket.com"

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})


def get_top_traders(limit=20):
    """Fetch top traders from the Polymarket leaderboard."""
    log.info("Fetching top %d traders from leaderboard...", limit)
    try:
        resp = _session.get(
            f"{GAMMA_BASE}/leaderboard",
            params={"window": "all", "limit": limit},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.error("Failed to fetch leaderboard: %s", e)
        return []

    traders = []
    # Handle both list and dict response shapes
    entries = data if isinstance(data, list) else data.get("leaders", data.get("data", []))

    for i, entry in enumerate(entries[:limit], 1):
        trader = {
            "rank": i,
            "address": entry.get("userAddress", entry.get("address", "unknown")),
            "username": entry.get("username", entry.get("displayName", "")),
            "profit": entry.get("profit", entry.get("pnl", 0)),
            "volume": entry.get("volume", entry.get("totalVolume", 0)),
            "markets_traded": entry.get("marketsTraded", entry.get("numMarkets", 0)),
            "positions": entry.get("positions", entry.get("numPositions", 0)),
        }
        traders.append(trader)

    log.info("Retrieved %d traders", len(traders))
    return traders


def analyze_trader(address):
    """Fetch recent activity and trading patterns for a trader address."""
    log.info("Analyzing trader: %s", address)

    result = {
        "address": address,
        "profile": None,
        "activity": [],
        "patterns": {},
    }

    # Fetch profile info
    try:
        resp = _session.get(
            f"{GAMMA_BASE}/profiles/{address}",
            timeout=15,
        )
        if resp.status_code == 200:
            result["profile"] = resp.json()
            log.debug("Profile loaded for %s", address)
    except requests.RequestException as e:
        log.warning("Could not fetch profile for %s: %s", address, e)

    # Fetch recent activity/positions
    try:
        resp = _session.get(
            f"{GAMMA_BASE}/positions",
            params={"user": address, "limit": 50, "sortBy": "value", "sortOrder": "desc"},
            timeout=15,
        )
        if resp.status_code == 200:
            positions = resp.json()
            if isinstance(positions, list):
                result["activity"] = positions
            else:
                result["activity"] = positions.get("data", positions.get("positions", []))
            log.debug("Loaded %d positions for %s", len(result["activity"]), address)
    except requests.RequestException as e:
        log.warning("Could not fetch positions for %s: %s", address, e)

    # Derive patterns from activity
    positions = result["activity"]
    if positions:
        total_value = sum(float(p.get("value", 0)) for p in positions)
        avg_price = sum(float(p.get("avgPrice", 0)) for p in positions) / len(positions) if positions else 0
        markets = set()
        for p in positions:
            market = p.get("market", p.get("slug", ""))
            if market:
                markets.add(market)

        result["patterns"] = {
            "active_positions": len(positions),
            "unique_markets": len(markets),
            "total_value": round(total_value, 2),
            "avg_entry_price": round(avg_price, 4),
        }

    log.info("Analysis complete for %s: %d positions, %d markets",
             address, len(positions), result["patterns"].get("unique_markets", 0))
    return result


def print_leaderboard(traders):
    """Print a formatted leaderboard table."""
    print()
    print(f"{'Rank':<6}{'Username':<24}{'Profit':>14}{'Volume':>16}{'Markets':>10}")
    print("-" * 70)
    for t in traders:
        username = t["username"] or t["address"][:12] + "..."
        profit = t["profit"]
        profit_str = f"${profit:,.2f}" if isinstance(profit, (int, float)) else str(profit)
        volume = t["volume"]
        volume_str = f"${volume:,.0f}" if isinstance(volume, (int, float)) else str(volume)
        print(f"{t['rank']:<6}{username:<24}{profit_str:>14}{volume_str:>16}{t['markets_traded']:>10}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Polymarket Leaderboard Analysis")
    parser.add_argument("--top", type=int, default=20, help="Number of top traders to fetch")
    parser.add_argument("--analyze", type=str, help="Analyze a specific trader address")
    args = parser.parse_args()

    if args.analyze:
        result = analyze_trader(args.analyze)
        print(f"\nTrader: {result['address']}")
        if result["profile"]:
            print(f"Username: {result['profile'].get('username', 'N/A')}")
        patterns = result["patterns"]
        if patterns:
            print(f"Active positions: {patterns['active_positions']}")
            print(f"Unique markets: {patterns['unique_markets']}")
            print(f"Total value: ${patterns['total_value']:,.2f}")
            print(f"Avg entry price: {patterns['avg_entry_price']:.4f}")
        else:
            print("No position data available.")
    else:
        traders = get_top_traders(limit=args.top)
        if traders:
            print_leaderboard(traders)
        else:
            print("No leaderboard data retrieved.")


if __name__ == "__main__":
    main()
