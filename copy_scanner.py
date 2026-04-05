"""Copy trader scanner — Phase 1: investigation and analysis of watched wallets.

Pulls trade history and current positions from Polymarket's data API,
scores each wallet, and surfaces their current open positions.

Usage:
    python3 copy_scanner.py                    # analyse all watched wallets
    python3 copy_scanner.py --wallet 0xabc...  # single wallet
    python3 copy_scanner.py --positions        # show open positions only
"""
import argparse
import logging
import time
from collections import defaultdict

import requests

log = logging.getLogger("scanner.copy")

DATA_API = "https://data-api.polymarket.com"
_TIMEOUT = 15
_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

# Wallets under investigation
WATCHED_WALLETS = {
    "0xfdc07e182e6f959256295567e450a8727272fa79": "FedWillWin",
    "0xf9151529abce6aa8357b99707ec06607cf238720": "Weather Trader",
    "0x7d2299a379eb0b1c6077c7c419a383da6fb7f0cf": "Geopolitics",
    "0xae76d7798abbc9445d5027b715279c0aef879ba9": "NBA Punter",
}


# ── API helpers ────────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None) -> list | dict | None:
    try:
        resp = _session.get(f"{DATA_API}{path}", params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("data-api %s failed: %s", path, e)
        return None


def get_activity(address: str, limit: int = 500) -> list[dict]:
    """Full trade history for a wallet, newest first."""
    data = _get("/activity", {"user": address, "limit": limit})
    if not isinstance(data, list):
        return []
    return [r for r in data if r.get("type") == "TRADE"]


def get_positions(address: str) -> list[dict]:
    """Current open positions with live P&L."""
    data = _get("/positions", {"user": address})
    return data if isinstance(data, list) else []


def get_portfolio_value(address: str) -> float:
    """Total portfolio value in USD."""
    val = _get("/value", {"user": address})
    if isinstance(val, (int, float)):
        return float(val)
    # Try alternative endpoint if /value doesn't work
    try:
        data = _get("/positions", {"user": address})
        if isinstance(data, list):
            return sum(p.get("currentValue", 0) for p in data)
    except:
        pass
    return 0.0


# ── Analysis ───────────────────────────────────────────────────────────────────

def _categorise(title: str) -> str:
    """Rough market category from title text."""
    t = (title or "").lower()
    for kw, cat in [
        ("trump", "US Politics"), ("biden", "US Politics"), ("election", "Politics"),
        ("congress", "US Politics"), ("president", "US Politics"),
        ("crypto", "Crypto"), ("bitcoin", "Crypto"), ("btc", "Crypto"),
        ("eth", "Crypto"), ("solana", "Crypto"),
        ("temperature", "Weather"), ("weather", "Weather"),
        ("nba", "Sports"), ("nfl", "Sports"), ("soccer", "Sports"),
        ("world cup", "Sports"), ("champions", "Sports"), ("premier league", "Sports"),
        ("fed ", "Finance"), ("rate", "Finance"), ("inflation", "Finance"),
        ("israel", "Geopolitics"), ("ukraine", "Geopolitics"), ("russia", "Geopolitics"),
        ("china", "Geopolitics"), ("taiwan", "Geopolitics"),
    ]:
        if kw in t:
            return cat
    return "Other"


def analyse_wallet(address: str, label: str = "", limit: int = 500) -> dict:
    """Full analysis of a wallet's trading history and current positions."""
    t0 = time.time()
    label = label or address[:10] + "..."

    trades = get_activity(address, limit=limit)
    positions = get_positions(address)
    portfolio_usd = get_portfolio_value(address)

    if not trades:
        return {
            "address": address, "label": label,
            "error": "No trade history found",
            "portfolio_usd": portfolio_usd,
            "positions": positions,
        }

    # ── Trade stats ────────────────────────────────────────────────────────────
    total_volume = sum(t.get("usdcSize", 0) for t in trades)
    avg_size = total_volume / len(trades) if trades else 0

    # Category breakdown
    cat_counts = defaultdict(int)
    cat_volume = defaultdict(float)
    for t in trades:
        cat = _categorise(t.get("title", ""))
        cat_counts[cat] += 1
        cat_volume[cat] += t.get("usdcSize", 0)

    top_categories = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]

    # Recent trades (last 10)
    recent = trades[:10]

    # Buy vs sell breakdown
    buys = [t for t in trades if t.get("side") == "BUY"]
    sells = [t for t in trades if t.get("side") == "SELL"]

    # ── Position stats ─────────────────────────────────────────────────────────
    total_unrealised = sum(p.get("cashPnl", 0) for p in positions)
    total_realised = sum(p.get("realizedPnl", 0) for p in positions)
    winners = [p for p in positions if p.get("cashPnl", 0) > 0]
    losers = [p for p in positions if p.get("cashPnl", 0) < 0]

    # Largest current positions
    top_positions = sorted(positions, key=lambda p: -p.get("currentValue", 0))[:10]

    return {
        "address": address,
        "label": label,
        "portfolio_usd": portfolio_usd,
        "trade_count": len(trades),
        "total_volume_usd": round(total_volume, 2),
        "avg_trade_size_usd": round(avg_size, 2),
        "buy_count": len(buys),
        "sell_count": len(sells),
        "open_positions": len(positions),
        "unrealised_pnl_usd": round(total_unrealised, 2),
        "realised_pnl_usd": round(total_realised, 2),
        "positions_winning": len(winners),
        "positions_losing": len(losers),
        "top_categories": top_categories,
        "top_positions": top_positions,
        "recent_trades": recent,
        "duration_s": round(time.time() - t0, 1),
    }


def scan(wallets: dict = None, verbose: bool = True) -> list[dict]:
    """Analyse all watched wallets. Returns list of analysis dicts."""
    wallets = wallets or WATCHED_WALLETS
    results = []
    for address, label in wallets.items():
        if verbose:
            print(f"\n{'─'*60}")
            print(f"  {label}  ({address[:10]}...{address[-6:]})")
            print(f"{'─'*60}")
        result = analyse_wallet(address, label=label)
        results.append(result)
        if verbose:
            _print_analysis(result)
    return results


# ── CLI display ────────────────────────────────────────────────────────────────

def _fmt_usd(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.2f}"


def _print_analysis(r: dict) -> None:
    if r.get("error"):
        print(f"  ERROR: {r['error']}")
        print(f"  Portfolio: {_fmt_usd(r.get('portfolio_usd', 0))}")
        return

    print(f"  Portfolio:      {_fmt_usd(r['portfolio_usd'])}")
    print(f"  Trades (last {r['trade_count']}):  vol={_fmt_usd(r['total_volume_usd'])}  avg={_fmt_usd(r['avg_trade_size_usd'])}  buys={r['buy_count']} sells={r['sell_count']}")
    print(f"  Open positions: {r['open_positions']}  unrealised P&L: {_fmt_usd(r['unrealised_pnl_usd'])}  ({r['positions_winning']} up / {r['positions_losing']} down)")
    print(f"  Realised P&L:   {_fmt_usd(r['realised_pnl_usd'])}")

    if r["top_categories"]:
        cats = "  ".join(f"{cat}:{n}" for cat, n in r["top_categories"])
        print(f"  Categories:     {cats}")

    if r["top_positions"]:
        print(f"\n  Top open positions:")
        for p in r["top_positions"][:5]:
            pnl = p.get("cashPnl", 0)
            pnl_str = f"+{_fmt_usd(pnl)}" if pnl >= 0 else _fmt_usd(pnl)
            print(f"    {_fmt_usd(p.get('currentValue',0)):>8}  {p.get('outcome','?'):3}  @{p.get('curPrice',0):.3f}  pnl={pnl_str:>10}  {p.get('title','')[:55]}")

    if r["recent_trades"]:
        print(f"\n  Recent trades:")
        for t in r["recent_trades"][:5]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.get("timestamp", 0)))
            print(f"    {ts}  {t.get('side','?'):4}  {_fmt_usd(t.get('usdcSize',0)):>8}  @{t.get('price',0):.3f}  {t.get('outcome','?'):3}  {t.get('title','')[:45]}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import log_setup
    import runtime_config
    log_setup.init_logging()
    runtime_config.log_runtime_status("copy_scanner.py")

    parser = argparse.ArgumentParser(description="Polymarket copy trader — wallet analysis")
    parser.add_argument("--wallet", help="Single wallet address to analyse")
    parser.add_argument("--positions", action="store_true", help="Show open positions only")
    parser.add_argument("--limit", type=int, default=500, help="Max trades to fetch (default 500)")
    args = parser.parse_args()

    if args.wallet:
        wallets = {args.wallet: args.wallet[:10] + "..."}
    else:
        wallets = WATCHED_WALLETS

    if args.positions:
        print("\n── Current Open Positions ──\n")
        for address, label in wallets.items():
            positions = get_positions(address)
            value = get_portfolio_value(address)
            print(f"{label} ({address[:10]}...)  portfolio={_fmt_usd(value)}  open={len(positions)}")
            for p in sorted(positions, key=lambda x: -x.get("currentValue", 0))[:10]:
                pnl = p.get("cashPnl", 0)
                print(f"  {_fmt_usd(p.get('currentValue',0)):>8}  {p.get('outcome','?'):3}  @{p.get('curPrice',0):.3f}  {'+' if pnl>=0 else ''}{_fmt_usd(pnl)}  {p.get('title','')[:60]}")
    else:
        scan(wallets)
