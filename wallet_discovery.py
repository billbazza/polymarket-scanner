"""Automated wallet discovery — mines high-volume markets for informed traders.

Strategy:
  1. Fetch top active markets by volume (gamma-api), filter noise.
  2. Sample recent trades per market (/trades?conditionId=...).
  3. Collect wallets with human names and meaningful trade sizes.
  4. Skip already-watched wallets.
  5. Score each candidate (wallet_monitor.score_wallet).
  6. Ask Claude for a recommendation (brain.recommend_wallet).
  7. Auto-add wallets that score ≥ AUTO_ADD_SCORE and Claude says "copy".
  8. Save others as pending candidates for manual review.

Can be triggered via API, called from the autonomy loop, or run standalone.
"""
import logging
import time
from collections import defaultdict

import requests

import db
import brain
import wallet_monitor
from copy_scanner import get_activity, _categorise

log = logging.getLogger("scanner.wallet_discovery")

# ── Config ──────────────────────────────────────────────────────────────────

GAMMA_API    = "https://gamma-api.polymarket.com"
DATA_API     = "https://data-api.polymarket.com"

TRADES_PER_MKT  = 500    # trades to fetch per market
MIN_TRADE_SIZE  = 200    # USD — ignore small trades when sampling
MIN_MKTS_SEEN   = 1      # wallet must appear in ≥ N markets to be considered
AUTO_ADD_SCORE  = 65     # auto-add if score ≥ this AND Claude says "copy"
MAX_CANDIDATES  = 40     # max wallets to score per run

# Hard noise patterns — any match = skip market
_NOISE_KEYWORDS = [
    # Esports / gaming
    "valorant", "dota", "esport", "rocket league", "call of duty",
    "league of legends", "cs2", "csgo", "r6", "nba 2k", "gta vi", "gta 6",
    "before gta", "carti album", "rihanna album", "jesus christ return",
    # Sub-hourly crypto
    "up or down", "5-minute", "5 min", "updown", "btc-updown", "eth-updown",
    # Single-game sports
    "game 1 winner", "game 2 winner", "map 1", "map 2",
    # Spam / low-info
    "will the republican party win the", "will the democratic party win the",
]

_session = requests.Session()
_session.headers["User-Agent"] = "polymarket-scanner/1.0"


# ── Market sampling ──────────────────────────────────────────────────────────

def _is_noise(market: dict) -> bool:
    text = " ".join([
        (market.get("slug") or ""),
        (market.get("question") or ""),
        (market.get("title") or ""),
    ]).lower()
    return any(kw in text for kw in _NOISE_KEYWORDS)


def _fetch_market_batch(params: dict, limit_per_call: int = 100) -> list[dict]:
    """Fetch a batch of markets with given params, noise-filtered."""
    try:
        r = _session.get(f"{GAMMA_API}/markets",
                         params={**params, "limit": limit_per_call}, timeout=15)
        r.raise_for_status()
        markets = r.json()
        return [m for m in markets if not _is_noise(m) and m.get("conditionId")]
    except Exception as e:
        log.warning("Discovery: market fetch failed (%s): %s", params, e)
        return []


def _fetch_diverse_markets(target: int = 30) -> list[dict]:
    """
    Sample markets from multiple categories to avoid domination by any one
    meme/event cluster. Strategy:
      - Recently active political/financial/crypto events
      - Recently resolved informative markets (last 60 days)
      - Mid-volume markets ($100K–$5M) where top 500 trades include real positions
    """
    seen_cids: set[str] = set()
    markets: list[dict] = []

    def add(batch):
        for m in batch:
            cid = m.get("conditionId", "")
            if cid and cid not in seen_cids:
                seen_cids.add(cid)
                markets.append(m)

    # 1. Active markets: informative event categories, mid volume
    for tag in ["politics", "crypto", "economics", "science", "sports"]:
        add(_fetch_market_batch({
            "active": True, "closed": False,
            "tag": tag,
            "sortBy": "volumeNum", "sortOrder": "desc",
        }))

    # 2. Recently resolved (last 60 days), high-volume informative
    add(_fetch_market_batch({
        "closed": True, "active": False,
        "end_date_min": "2025-10-01",
        "sortBy": "volumeNum", "sortOrder": "desc",
    }, limit_per_call=200))

    # 3. Active markets with substantial volume across all categories
    #    (catches near-certainty plays and high-conviction markets)
    add(_fetch_market_batch({
        "active": True, "closed": False,
        "sortBy": "volumeNum", "sortOrder": "desc",
        "volume_num_min": 500000,
    }, limit_per_call=200))

    # 4. Recently resolved (last 6 months) — informed traders made their moves here
    add(_fetch_market_batch({
        "closed": True, "active": False,
        "end_date_min": "2025-06-01",
        "sortBy": "volumeNum", "sortOrder": "desc",
    }, limit_per_call=200))

    # Prefer mid-volume range — big enough for real traders, small enough
    # that their trades are still in the top-500
    def sort_key(m):
        vol = float(m.get("volumeNum") or 0)
        # Prefer $100K–$5M range
        in_sweet_spot = 100_000 <= vol <= 5_000_000
        return (0 if in_sweet_spot else 1, -vol)

    markets.sort(key=sort_key)
    result = markets[:target]
    log.info("Discovery: %d diverse markets selected (from %d candidates)", len(result), len(markets))
    return result


def _fetch_traders_from_market(condition_id: str) -> list[dict]:
    """Return trades from a market condition, newest first."""
    try:
        r = _session.get(f"{DATA_API}/trades", params={
            "conditionId": condition_id,
            "limit": TRADES_PER_MKT,
        }, timeout=15)
        r.raise_for_status()
        return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        log.warning("Discovery: trades fetch failed for %s: %s", condition_id[:20], e)
        return []


def _get_portfolio_value(address: str) -> float:
    """Quick portfolio value lookup for pre-filtering."""
    try:
        r = _session.get(f"{DATA_API}/value", params={"user": address}, timeout=8)
        if r.ok:
            data = r.json()
            if isinstance(data, list) and data:
                return float(data[0].get("value", 0))
    except Exception:
        pass
    return 0.0


# ── Candidate scoring ────────────────────────────────────────────────────────

def _already_watching(address: str) -> bool:
    watched = {r["address"] for r in db.get_watched_wallets(active_only=False)}
    return address in watched


def _already_candidate(address: str) -> bool:
    pending = {r["address"] for r in db.get_wallet_candidates(status="pending")}
    return address in pending


def run_discovery(
    n_markets: int = 30,
    auto_add: bool = True,
    verbose: bool = True,
) -> dict:
    """
    Run a full discovery cycle. Returns summary dict.

    auto_add=True: wallets scoring >= AUTO_ADD_SCORE with Claude verdict 'copy'
                   are automatically added to the watch list.
    """
    t0 = time.time()
    log.info("Discovery: starting (n_markets=%d, auto_add=%s)", n_markets, auto_add)

    markets = _fetch_diverse_markets(target=n_markets)
    if not markets:
        return {"ok": False, "error": "No markets fetched", "candidates": 0, "auto_added": 0}

    # ── Step 1: collect traders across markets ────────────────────────────────
    trader_data: dict[str, dict] = defaultdict(lambda: {
        "volume": 0.0, "markets": set(), "trades": 0, "name": "", "market_titles": [],
    })

    for m in markets:
        cid = m["conditionId"]
        title = m.get("question") or m.get("title") or ""
        trades = _fetch_traders_from_market(cid)
        if verbose:
            log.info("Discovery: %s — %d trades", title[:50], len(trades))

        for t in trades:
            addr = (t.get("proxyWallet") or "").lower()
            if not addr:
                continue
            size_usd = t.get("size", 0) * t.get("price", 1)
            if size_usd < MIN_TRADE_SIZE:
                continue
            name = t.get("name", "")
            # Skip unnamed hex wallets — likely bots or very new accounts
            if not name or name.startswith("0x"):
                continue
            trader_data[addr]["volume"] += size_usd
            trader_data[addr]["markets"].add(cid)
            trader_data[addr]["trades"] += 1
            trader_data[addr]["name"] = name
            if title and title not in trader_data[addr]["market_titles"]:
                trader_data[addr]["market_titles"].append(title[:60])

        time.sleep(0.15)    # gentle rate limit

    log.info("Discovery: %d unique named traders found with size>$%d",
             len(trader_data), MIN_TRADE_SIZE)

    # ── Step 2: filter to multi-market traders not already watched ────────────
    candidates_raw = [
        (addr, d) for addr, d in trader_data.items()
        if len(d["markets"]) >= MIN_MKTS_SEEN
        and not _already_watching(addr)
        and not _already_candidate(addr)
    ]
    candidates_raw.sort(key=lambda x: (-len(x[1]["markets"]), -x[1]["volume"]))

    log.info("Discovery: %d candidates after filter (≥%d markets, not already watched)",
             len(candidates_raw), MIN_MKTS_SEEN)

    # ── Step 2b: pre-filter by portfolio value — skip wallets with < $1K ──────
    # This eliminates small traders without burning Claude tokens on full scoring.
    PORTFOLIO_MIN = 1000
    qualified = []
    for addr, d in candidates_raw[:MAX_CANDIDATES * 2]:
        pv = _get_portfolio_value(addr)
        d["portfolio_value"] = pv
        if pv >= PORTFOLIO_MIN:
            qualified.append((addr, d))
            log.info("Discovery: %s portfolio=$%.0f ✓ (will score)", d["name"], pv)
        else:
            log.info("Discovery: %s portfolio=$%.0f — skip (below $%d)",
                     d["name"], pv, PORTFOLIO_MIN)
        time.sleep(0.1)

    candidates_raw = qualified
    log.info("Discovery: %d candidates pass portfolio pre-filter ($%d+)",
             len(candidates_raw), PORTFOLIO_MIN)

    # ── Step 3: score + AI recommendation ────────────────────────────────────
    auto_added = 0
    saved = 0
    dismissed = 0

    for addr, d in candidates_raw[:MAX_CANDIDATES]:
        label = d["name"]
        log.info("Discovery: scoring %s (%s)…", label, addr[:16])

        try:
            score_result = wallet_monitor.score_wallet(addr, label)
        except Exception as e:
            log.warning("Discovery: score failed for %s: %s", label, e)
            continue

        score = score_result.get("score", 0)
        cls   = score_result.get("classification", "unknown")

        # Skip clear failures early — don't burn Claude tokens
        if cls in ("bot", "insufficient_data", "no_data") or score < 35:
            log.info("Discovery: skip %s — %s score=%.0f", label, cls, score)
            dismissed += 1
            continue

        # Save as dismissed candidate so UI can show what was found but rejected
        if score < 50:
            db.save_wallet_candidate({
                "address": addr, "label": label,
                "score": score, "classification": cls,
                "will_copy": False, "breakdown": score_result.get("breakdown"),
                "ai_verdict": "skip", "ai_reasoning": f"Auto-skip: {cls} score={score:.0f}",
                "ai_risk_flags": [], "source_markets": d["market_titles"][:3],
                "status": "dismissed",
            })
            dismissed += 1
            continue

        # AI recommendation
        ai_result = None
        try:
            ai_result = brain.recommend_wallet(addr, label, score_result)
        except Exception as e:
            log.warning("Discovery: brain failed for %s: %s", label, e)

        candidate = {
            "address": addr,
            "label": label,
            "score": score,
            "classification": cls,
            "will_copy": score_result.get("will_copy"),
            "breakdown": score_result.get("breakdown"),
            "ai_verdict": ai_result.get("verdict") if ai_result else None,
            "ai_reasoning": ai_result.get("reasoning") if ai_result else None,
            "ai_risk_flags": ai_result.get("risk_flags") if ai_result else [],
            "source_markets": d["market_titles"][:5],
        }

        # Auto-add high scorers with Claude's blessing
        verdict = (ai_result or {}).get("verdict")
        if auto_add and score >= AUTO_ADD_SCORE and verdict == "copy":
            row_id = db.add_watched_wallet(addr, label, added_by="auto_discovery")
            db.update_wallet_score(addr, score_result)
            if ai_result:
                db.update_wallet_ai(addr, verdict,
                                    ai_result.get("reasoning", ""),
                                    ai_result.get("risk_flags", []))
            log.info("Discovery: AUTO-ADDED %s score=%.0f verdict=%s", label, score, verdict)
            auto_added += 1
            db.update_candidate_status(
                db.save_wallet_candidate({**candidate, "status": "pending"}),
                "added",
            )
        else:
            db.save_wallet_candidate(candidate)
            saved += 1
            log.info("Discovery: candidate saved %s score=%.0f verdict=%s",
                     label, score, verdict)

        time.sleep(0.3)

    duration = round(time.time() - t0, 1)
    log.info("Discovery: done in %.1fs — %d auto-added, %d pending, %d dismissed",
             duration, auto_added, saved, dismissed)

    return {
        "ok": True,
        "markets_sampled": len(markets),
        "traders_found": len(trader_data),
        "candidates_scored": len(candidates_raw),
        "auto_added": auto_added,
        "candidates_pending": saved,
        "candidates_dismissed": dismissed,
        "duration_secs": duration,
    }


if __name__ == "__main__":
    from log_setup import init_logging
    from dotenv import load_dotenv
    load_dotenv()
    init_logging()
    result = run_discovery(n_markets=30, auto_add=False, verbose=True)
    print(result)
