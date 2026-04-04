"""Whale & Insider Detector — flags suspicious large trades in low-liquidity markets.

Scans active Polymarket events for markets with unusual activity patterns:
- High 24h volume relative to liquidity (volume spike)
- Large orders sitting in thin order books
- Sudden price movements in niche markets

Each flagged market gets a suspicion score (0-100) based on multiple heuristics.

Usage:
    python3 whale_detector.py              # run scan, print results
    python3 whale_detector.py --min-score 60  # only show high-suspicion
"""
import argparse
import logging
import time

import api
import brain
import db
import execution

log = logging.getLogger("scanner.whale")

# ── Thresholds ────────────────────────────────────────────────────────────────

MIN_VOLUME_24H = 1_000        # ignore dead markets
MAX_LIQUIDITY = 40_000        # whales move thin markets, not deep ones
VOLUME_SPIKE_RATIO = 3.0      # 24h vol / liquidity — normal is ~0.5-1.0
PRICE_MOVE_THRESHOLD = 0.08   # 8% move = something happened
BIG_ORDER_USD = 3_000         # >$3K resting order is notable in thin markets
MIN_SUSPICION_SCORE = 60      # below this, not worth storing


def _is_sports(event_name, market_name):
    """Check if the market or event is sports-related."""
    keywords = [
        "nba", "nfl", "mlb", "nhl", "soccer", "football", "tennis", "baseball",
        "basketball", "hockey", "ufc", "boxing", "fifa", "world cup", "premier league",
        "champions league", "super bowl", "world series", "stanley cup", "olympics",
        "formula 1", "nascar", "golf", "pga", "masters", "wimbledon", "euro 2024",
        "copa america", "cricket", "rugby", "horse racing", "grand prix",
    ]
    text = f"{event_name} {market_name}".lower()
    return any(kw in text for kw in keywords)


def _score_volume_spike(volume_24h, liquidity):
    """0-25 points: how abnormal is 24h volume vs available liquidity."""
    if not liquidity or liquidity <= 0:
        return 0
    ratio = volume_24h / liquidity
    if ratio < 1.0:
        return 0
    if ratio >= VOLUME_SPIKE_RATIO * 2:
        return 25
    # Linear scale: ratio 1.0 -> 0, ratio 6.0 -> 25
    return min(25, int((ratio - 1.0) / (VOLUME_SPIKE_RATIO * 2 - 1.0) * 25))


def _score_price_move(best_bid, best_ask, outcomes):
    """0-25 points: how far has price moved from 50/50 or expected range.

    Extreme prices (near 0 or 1) in active markets with volume spikes
    suggest someone knows something.
    """
    if not outcomes:
        return 0
    try:
        prices = [float(p) for p in outcomes.split(",") if p.strip()]
    except (ValueError, AttributeError):
        return 0
    if not prices:
        return 0
    # Most extreme price (closest to 0 or 1) in a multi-outcome market
    extremity = max(abs(p - 0.5) for p in prices) * 2  # 0 at 50%, 1 at 0%/100%
    if extremity < 0.3:
        return 0  # price between 35-65%, not extreme enough
    return min(25, int((extremity - 0.3) / 0.7 * 25))


def _score_book_imbalance(book):
    """0-25 points: large resting orders on one side of the book."""
    if not book:
        return 0, 0, None
    try:
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        bid_total = sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids[:10])
        ask_total = sum(float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks[:10])
    except (ValueError, TypeError):
        return 0, 0, None

    total = bid_total + ask_total
    if total < 100:
        return 0, 0, None

    # Check for single large order
    max_bid = max((float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids[:5]), default=0)
    max_ask = max((float(a.get("size", 0)) * float(a.get("price", 0)) for a in asks[:5]), default=0)
    biggest = max(max_bid, max_ask)

    score = 0
    side = None

    # Imbalance: one side has >70% of liquidity
    if total > 0:
        imbalance = abs(bid_total - ask_total) / total
        if imbalance > 0.4:
            score += min(15, int(imbalance / 0.6 * 15))
            side = "BID" if bid_total > ask_total else "ASK"

    # Single big order
    if biggest >= BIG_ORDER_USD:
        score += min(10, int((biggest - BIG_ORDER_USD) / 5000 * 10))

    return min(25, score), biggest, side


def _score_liquidity_thinness(liquidity):
    """0-25 points: thinner markets are easier to manipulate."""
    if not liquidity or liquidity <= 0:
        return 0
    if liquidity >= MAX_LIQUIDITY:
        return 0
    if liquidity <= 1000:
        return 25
    # Inverse scale: $1K -> 25, $40K -> 0
    return min(25, int((1 - liquidity / MAX_LIQUIDITY) * 25))


def _fmt_usd(v):
    if v >= 1_000_000:
        return f"${v/1e6:.1f}M"
    if v >= 1_000:
        return f"${v/1e3:.1f}K"
    return f"${v:,.0f}"


def _generate_analysis(question, vol_ratio, volume_24h, liquidity,
                       biggest_order, dominant_side, current_price,
                       score_vol, score_price, score_book, score_thin):
    """Generate a human-readable analysis of why this alert was flagged."""
    parts = []

    # Volume spike narrative
    if vol_ratio >= 5:
        parts.append(f"{_fmt_usd(volume_24h)} traded in 24h on only {_fmt_usd(liquidity)} liquidity ({vol_ratio:.0f}x ratio) — extreme volume spike.")
    elif vol_ratio >= 2:
        parts.append(f"{_fmt_usd(volume_24h)} traded in 24h vs {_fmt_usd(liquidity)} liquidity ({vol_ratio:.1f}x ratio) — elevated activity.")

    # Big order narrative
    if biggest_order >= 3000 and dominant_side:
        side_meaning = "selling YES" if dominant_side == "ASK" else "buying YES"
        parts.append(f"{_fmt_usd(biggest_order)} resting on the {dominant_side} side — someone is aggressively {side_meaning}.")

    # Thin liquidity narrative
    if liquidity < 5000:
        parts.append(f"Very thin market ({_fmt_usd(liquidity)}) — a single whale can move the price significantly.")
    elif liquidity < 15000:
        parts.append(f"Thin market ({_fmt_usd(liquidity)}) — vulnerable to large single trades.")

    # Price extremity narrative
    if current_price is not None:
        if current_price >= 0.9:
            parts.append(f"Price at {current_price*100:.0f}% — near certainty, yet still attracting unusual volume.")
        elif current_price <= 0.1:
            parts.append(f"Price at {current_price*100:.0f}% — long shot with suspicious activity.")

    # Overall interpretation
    top_score = max(score_vol, score_price, score_book, score_thin)
    if score_book == top_score and biggest_order >= 5000:
        parts.append("Pattern: large resting order in thin book — likely informed positioning or whale accumulation.")
    elif score_vol == top_score and vol_ratio >= 5:
        parts.append("Pattern: volume spike — sudden interest from large players, possible insider knowledge.")
    elif score_thin == top_score:
        parts.append("Pattern: activity in illiquid market — small capital can create outsized moves.")

    return " ".join(parts) if parts else "Flagged by multiple weak signals."


def scan_market(market, event_name):
    """Analyse a single market for whale/insider activity. Returns alert dict or None."""
    question = market.get("question") or market.get("groupItemTitle") or ""
    
    # Filter out sports markets
    if _is_sports(event_name, question):
        return None

    volume_24h = float(market.get("volume24hr") or market.get("volume24Hr") or 0)
    liquidity = float(market.get("liquidity") or 0)
    outcomes = market.get("outcomePrices") or ""
    market_id = market.get("conditionId") or market.get("id") or ""

    if volume_24h < MIN_VOLUME_24H:
        return None

    # Get token for order book check
    tokens = market.get("clobTokenIds")
    token_id = None
    if tokens:
        try:
            if isinstance(tokens, str):
                import json
                tokens = json.loads(tokens)
            token_id = api.normalize_token_id(tokens[0] if tokens else None)
        except Exception:
            pass

    # Score components
    vol_score = _score_volume_spike(volume_24h, liquidity)
    price_score = _score_price_move(None, None, outcomes)
    thin_score = _score_liquidity_thinness(liquidity)

    # Only check order book if preliminary score is promising (saves API calls)
    book_score = 0
    biggest_order = 0
    dominant_side = None
    if vol_score + price_score + thin_score >= 20 and token_id:
        try:
            book = api.get_book(token_id)
            book_score, biggest_order, dominant_side = _score_book_imbalance(book)
        except Exception as e:
            print(f"Book fetch failed for {market_id}: {e}")

    total_score = vol_score + price_score + book_score + thin_score

    if total_score < MIN_SUSPICION_SCORE:
        return None

    # Parse current price
    try:
        price_list = [float(p) for p in outcomes.split(",") if p.strip()]
        current_price = price_list[0] if price_list else None
    except (ValueError, AttributeError):
        current_price = None

    vol_ratio = volume_24h / liquidity if liquidity > 0 else 0

    analysis = _generate_analysis(
        question, vol_ratio, volume_24h, liquidity,
        biggest_order, dominant_side, current_price,
        vol_score, price_score, book_score, thin_score,
    )

    return {
        "timestamp": time.time(),
        "event": event_name,
        "market": question,
        "market_id": market_id,
        "token_id": token_id,
        "current_price": current_price,
        "volume_24h": volume_24h,
        "liquidity": liquidity,
        "volume_ratio": round(vol_ratio, 2),
        "biggest_order_usd": round(biggest_order, 2),
        "dominant_side": dominant_side,
        "suspicion_score": total_score,
        "score_volume": vol_score,
        "score_price": price_score,
        "score_book": book_score,
        "score_thinness": thin_score,
        "analysis": analysis,
        "status": "new",
    }


def ask_ai_about_position(alert):
    """Ask AI about the position using the Brain module."""
    try:
        # Create a detailed prompt for the AI
        current_price_str = f"{alert['current_price']:.2%}" if alert['current_price'] is not None else 'N/A'
        prompt = f"""Analyze this Polymarket position for potential trading opportunities:

Event: {alert['event']}
Market: {alert['market']}
Current Price: {current_price_str}
Volume (24h): ${alert['volume_24h']:,.0f}
Liquidity: ${alert['liquidity']:,.0f}
Volume Ratio: {alert['volume_ratio']:.1f}x
Suspicion Score: {alert['suspicion_score']}/100

Key Metrics:
- Volume Score: {alert['score_volume']}/25
- Price Score: {alert['score_price']}/25  
- Book Score: {alert['score_book']}/25
- Thinness Score: {alert['score_thinness']}/25

Biggest Order: ${alert['biggest_order_usd']:,.0f} ({alert['dominant_side'] or 'N/A'})
Analysis: {alert['analysis']}

Please provide:
1. A risk assessment of this position
2. Potential trading strategy recommendations
3. Key factors to monitor
4. Estimated probability of the outcome based on the activity

Keep the analysis concise but actionable."""
        
        response = brain.ask(prompt)
        return response
    except Exception as e:
        print(f"Failed to get AI analysis: {e}")
        return "AI analysis unavailable at this time."


def should_trade_whale_alert(alert, ai_analysis=None):
    """Determine if a whale alert should be traded based on suspicion score and AI analysis."""
    # High suspicion threshold
    if alert['suspicion_score'] < 70:
        return False, "Suspicion score too low"
    
    # Check for strong indicators
    strong_indicators = 0
    if alert['score_volume'] >= 20:  # Strong volume spike
        strong_indicators += 1
    if alert['score_book'] >= 15:    # Large resting order
        strong_indicators += 1
    if alert['score_thinness'] >= 20:  # Very thin market
        strong_indicators += 1
    
    if strong_indicators < 2:
        return False, f"Insufficient strong indicators ({strong_indicators}/2)"
    
    # AI validation if available
    if ai_analysis:
        ai_text = ai_analysis.lower()
        if any(phrase in ai_text for phrase in ['trade', 'buy', 'sell', 'position']):
            if any(phrase in ai_text for phrase in ['high risk', 'avoid', 'skip', 'no']):
                return False, "AI recommends avoiding"
            return True, "AI supports trade"
    
    return True, "High suspicion with strong indicators"


def create_whale_trade(alert, size_usd=20, mode="paper"):
    """Create a whale trade using the execution layer (paper by default)."""
    try:
        result = execution.execute_whale_trade(alert, size_usd=size_usd, mode=mode)
        if result.get("ok"):
            return result.get("trade_id")
        log.warning(
            "Whale trade blocked for alert %s: %s",
            alert.get("id"),
            result.get("error"),
        )
        return None
    except Exception as e:
        log.exception("Error creating whale trade for alert %s: %s", alert.get("id"), e)
        return None

def scan(min_score=MIN_SUSPICION_SCORE, max_pages=10, verbose=False, auto_trade=False):
    """Scan all active markets for whale/insider activity.

    Returns (alerts, stats) where alerts is a list of dicts sorted by score desc.
    If auto_trade=True, high-confidence alerts will automatically create paper trades.
    """
    t0 = time.time()
    events = api.get_all_active_events(max_pages=max_pages)
    markets_checked = 0
    alerts = []
    trades_created = 0

    for event in events:
        event_name = event.get("title") or event.get("slug") or "Unknown"
        markets = event.get("markets") or []
        for market in markets:
            markets_checked += 1
            result = scan_market(market, event_name)
            if result and result["suspicion_score"] >= min_score:
                # Save alert to database
                alert_id = db.save_whale_alert(result)
                if alert_id:
                    result["id"] = alert_id  # Add ID for trade creation
                    alerts.append(result)
                    
                    # Auto-trade high-confidence alerts
                    if auto_trade:
                        should_trade, reason = should_trade_whale_alert(result)
                        if should_trade:
                            # Get AI analysis for better decision making
                            ai_analysis = ask_ai_about_position(result)
                            should_trade, reason = should_trade_whale_alert(result, ai_analysis)
                            
                            if should_trade:
                                trade_id = create_whale_trade(result, size_usd=20)
                                if trade_id:
                                    trades_created += 1
                                    print(f"Auto-trade created: #{trade_id} for {result['market'][:40]}")
                                else:
                                    print(f"Failed to create auto-trade for {result['market'][:40]}")
                            else:
                                if verbose:
                                    print(f"Auto-trade skipped for {result['market'][:40]}: {reason}")
                        else:
                            if verbose:
                                print(f"Auto-trade skipped for {result['market'][:40]}: {reason}")
                    
                    if verbose:
                        print(f"WHALE ALERT [{result['suspicion_score']}]: {event_name} — {result['market'][:50]} (vol_ratio={result['volume_ratio']:.1f}, liq=${result['liquidity']:.0f})")

    # Sort by suspicion score descending
    alerts.sort(key=lambda a: a["suspicion_score"], reverse=True)

    duration = round(time.time() - t0, 1)
    stats = {
        "markets_checked": markets_checked,
        "events_checked": len(events),
        "alerts_found": len(alerts),
        "trades_created": trades_created,
        "duration_secs": duration,
    }

    print(f"Whale scan: {markets_checked} markets checked, {len(alerts)} alerts, {trades_created} trades created ({duration:.1f}s)")

    return alerts, stats


if __name__ == "__main__":
    # Initialize logging
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="Whale & Insider Detector")
    parser.add_argument("--min-score", type=int, default=MIN_SUSPICION_SCORE)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--brain", "-b", action="store_true", help="Ask AI about each alert")
    parser.add_argument("--auto-trade", "-t", action="store_true", help="Auto-create paper trades for high-confidence alerts")
    args = parser.parse_args()

    alerts, stats = scan(min_score=args.min_score, verbose=args.verbose, auto_trade=args.auto_trade)
    print(f"\n{'='*60}")
    print(f"Whale Scan Complete: {stats['markets_checked']} markets, {stats['alerts_found']} alerts, {stats['trades_created']} trades ({stats['duration_secs']}s)")
    print(f"{'='*60}\n")

    for i, a in enumerate(alerts):
        score_bar = "#" * (a["suspicion_score"] // 5) + "." * (20 - a["suspicion_score"] // 5)
        print(f"[{a['suspicion_score']:3d}] [{score_bar}] {a['event'][:40]}")
        print(f"       {a['market'][:60]}")
        print(f"       vol=${a['volume_24h']:,.0f}  liq=${a['liquidity']:,.0f}  ratio={a['volume_ratio']:.1f}x")
        if a["biggest_order_usd"]:
            print(f"       biggest order: ${a['biggest_order_usd']:,.0f} ({a['dominant_side'] or '?'})")
        print(f"       scores: vol={a['score_volume']} price={a['score_price']} book={a['score_book']} thin={a['score_thinness']}")
        
        if args.brain:
            print(f"\n🧠 [Brain Analysis for Alert #{i+1}]")
            ai_analysis = ask_ai_about_position(a)
            print(f"{ai_analysis}")
            print()
        else:
            print(f"💡 Use --brain or -b flag to get AI analysis for this alert")
        
        print()
