"""Longshot bias scanner — exploits systematic YES overpricing on low-probability markets.

Research finding (ideas01.md):
  At the 5¢ bucket, YES tokens win 4.18% of the time vs 5% implied (-16.4% mispricing).
  At the 1¢ bucket, win rate is 0.43% vs 1% implied (-57% mispricing).
  This is the classic longshot bias: retail bettors overpay for unlikely YES outcomes.

Strategy:
  Place maker SELL-YES limit orders inside the spread on markets where YES is 3–15¢.
  A filled SELL-YES at price P means:
    - We receive P per share
    - We're effectively long NO at cost (1 - P)
    - Payout: $1 if NO wins, $0 if YES wins
  As maker (0% fee) + calibration edge → positive expected return.

Calibration table (empirical Polymarket data from research/ideas01.md):
  YES price   Actual YES win%   Implied win%   NO edge
  1¢          0.43%             1.00%          +0.57pp
  5¢          4.18%             5.00%          +0.82pp
  10¢         9.10%             10.00%         +0.90pp
  15¢         14.30%            15.00%         +0.70pp

Execution note:
  Most longshot markets have STALE CLOB books (bid=0.001, ask=0.999). These are
  NOT real orders — they're remnants with no real counterparty. We filter these
  out and only flag markets with genuine order book activity:
    - YES best bid  > 0.5¢  (real buyer present)
    - YES best ask  < 0.25  (real seller present)
    - YES spread    < 0.15  (market is being actively made)

  Markets without real books are logged as calibration_only (informational, no trade).
"""
import json
import logging
import time

import api
import math_engine

log = logging.getLogger("scanner.longshot")

# ── Strategy parameters ─────────────────────────────────────────────────────
YES_MIN        = 0.03   # only markets where YES is priced between 3¢ and 15¢
YES_MAX        = 0.15
MIN_LIQUIDITY  = 2000   # skip very thin events
MIN_EV_PCT     = 0.30   # minimum EV% after fill-probability adjustment

# Filters for "real" CLOB activity on YES book
REAL_BOOK_BID_MIN  = 0.005   # YES best bid must be > 0.5¢ (not a garbage order)
REAL_BOOK_ASK_MAX  = 0.25    # YES best ask must be < 25¢
REAL_BOOK_SPREAD   = 0.15    # absolute spread < 15¢

# Maker order placement: we post SELL YES at the YES ask (or inside the spread).
# MAKER_AGGRESSION=0 means at the ask. 0.5 means halfway into the spread (lower
# fill price, lower EV, but faster fill). Default: post at the ask for max EV.
MAKER_AGGRESSION = 0.0

# Probability that a maker SELL-YES order fills within the order TTL.
# Conservative for longshot markets: lower volume, lower fill rate.
FILL_PROB = 0.50

# Calibration table: YES_price → actual_YES_win_rate (empirical from research)
_CALIBRATION = [
    (0.01, 0.0043),
    (0.05, 0.0418),
    (0.10, 0.0910),
    (0.15, 0.1430),
]


def _calibrated_yes_prob(yes_price: float) -> float:
    """Interpolate the empirical YES win probability from the calibration table."""
    if yes_price <= _CALIBRATION[0][0]:
        return _CALIBRATION[0][1]
    if yes_price >= _CALIBRATION[-1][0]:
        return _CALIBRATION[-1][1]

    for i in range(len(_CALIBRATION) - 1):
        x0, y0 = _CALIBRATION[i]
        x1, y1 = _CALIBRATION[i + 1]
        if x0 <= yes_price <= x1:
            t = (yes_price - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return yes_price  # fallback


def _score_market(event_title, market, event_liquidity):
    """Score a single market for longshot YES-sell maker opportunity.

    Returns a scored dict (tradeable or calibration_only) or None.
    """
    tokens_raw  = market.get("clobTokenIds", "")
    prices_raw  = market.get("outcomePrices", "")

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
        no_price  = float(price_list[1])
    except (ValueError, TypeError):
        return None

    # Only longshot YES markets
    if not (YES_MIN <= yes_price <= YES_MAX):
        return None

    # Basic NO sanity check
    if not (0.80 <= no_price <= 0.99):
        return None

    yes_token = token_list[0]
    no_token  = token_list[1]

    # ── Calibration edge ────────────────────────────────────────────────────
    calibrated_yes_prob = _calibrated_yes_prob(yes_price)
    calibrated_no_prob  = 1.0 - calibrated_yes_prob
    calibration_edge    = calibrated_no_prob - no_price  # positive = NO underpriced

    # ── Check YES order book for real activity ───────────────────────────────
    try:
        book = api.get_book(yes_token)
    except Exception as e:
        log.debug("YES book fetch failed for %s: %s", yes_token[:16], e)
        return None

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    has_real_book = False
    best_yes_bid  = 0.0
    best_yes_ask  = 0.0
    spread_abs    = 0.0
    spread_pct    = 0.0
    limit_price   = yes_price   # fallback: use AMM mid

    if bids and asks:
        b0 = float(bids[0].get("price", 0))
        a0 = float(asks[0].get("price", 1))
        if (b0 > REAL_BOOK_BID_MIN
                and a0 < REAL_BOOK_ASK_MAX
                and (a0 - b0) < REAL_BOOK_SPREAD
                and b0 < a0):
            has_real_book = True
            best_yes_bid  = b0
            best_yes_ask  = a0
            spread_abs    = a0 - b0
            spread_pct    = spread_abs / a0 * 100 if a0 > 0 else 0

            # Maker SELL-YES limit: at the ask (AGGRESSION=0) or inside the spread.
            # Lower limit = YES is sold cheaper = NO acquired at higher cost = lower EV.
            limit_price = round(best_yes_ask - MAKER_AGGRESSION * spread_abs, 4)

    # ── EV calculation (SELL-YES mechanics) ─────────────────────────────────
    # When our SELL-YES limit order fills at limit_price:
    #   Effective NO cost = 1.0 - limit_price
    #   Win (NO resolves): payout $1.00, profit = limit_price
    #   Lose (YES resolves): payout $0.00, loss = (1.0 - limit_price)
    # Fee: maker = 0%.
    no_cost     = 1.0 - limit_price
    win_payout  = limit_price          # profit when we're right
    loss_amount = no_cost              # loss when we're wrong

    gross_ev = (calibrated_no_prob * win_payout) - (calibrated_yes_prob * loss_amount)

    # Effective EV after fill probability discount
    fill_prob = FILL_PROB if has_real_book else 0.0
    ev        = gross_ev * fill_prob
    ev_pct    = round(ev / no_cost * 100, 3) if no_cost > 0 else 0.0

    kelly_f = math_engine.kelly_fraction(calibrated_no_prob, win_payout, loss_amount)

    tradeable = (
        has_real_book
        and ev_pct >= MIN_EV_PCT
        and kelly_f > 0
        and calibration_edge > 0
    )

    result = {
        "event":              event_title,
        "market":             market.get("question", market.get("groupItemTitle", "")),
        "market_id":          str(market.get("id", "")),
        "yes_token":          yes_token,
        "no_token":           no_token,
        "yes_price":          round(yes_price, 4),
        "no_price":           round(no_price, 4),
        "calibrated_yes_prob": round(calibrated_yes_prob, 4),
        "calibrated_no_prob": round(calibrated_no_prob, 4),
        "calibration_edge":   round(calibration_edge, 4),
        "has_real_book":      has_real_book,
        "best_yes_bid":       round(best_yes_bid, 4),
        "best_yes_ask":       round(best_yes_ask, 4),
        "spread_abs":         round(spread_abs, 4),
        "spread_pct":         round(spread_pct, 3),
        "limit_price":        limit_price,   # the YES sell limit we'd post
        "no_cost":            round(no_cost, 4),
        "win_payout":         round(win_payout, 4),
        "loss_amount":        round(loss_amount, 4),
        "gross_ev":           round(gross_ev, 4),
        "fill_prob":          fill_prob,
        "ev":                 round(ev, 4),
        "ev_pct":             ev_pct,
        "kelly_fraction":     kelly_f,
        "liquidity":          event_liquidity,
        "action":             "SELL_YES",   # = effective BUY_NO via maker limit
        "tradeable":          tradeable,
    }

    if tradeable:
        log.info(
            "Longshot TRADEABLE: %s YES=%.3f bid=%.3f ask=%.3f limit=%.3f "
            "calib_edge=+%.4f ev=%.2f%%",
            result["market"][:50], yes_price, best_yes_bid, best_yes_ask,
            limit_price, calibration_edge, ev_pct,
        )
    else:
        log.debug(
            "Longshot skip: %s YES=%.3f real_book=%s ev=%.2f%%",
            result["market"][:40], yes_price, has_real_book, ev_pct,
        )

    return result


def scan(min_liquidity=MIN_LIQUIDITY, min_ev_pct=MIN_EV_PCT, verbose=True):
    """Scan all active markets for longshot YES-sell maker opportunities.

    Returns:
        (opportunities, stats) where opportunities is a list of scored dicts
        and stats is a summary dict.
    """
    t0 = time.time()

    if verbose:
        print("Fetching active events...", end=" ", flush=True)

    try:
        events = api.get_all_active_events(max_pages=10)
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        return [], {"error": str(e)}

    if verbose:
        print(f"{len(events)} events")

    markets_checked  = 0
    in_range         = 0
    no_real_book     = 0
    opportunities    = []

    for event in events:
        liq = float(event.get("liquidity", 0) or 0)
        if liq < min_liquidity:
            continue

        event_title = event.get("title", "")
        markets = event.get("markets", [])

        for market in markets:
            markets_checked += 1

            # Quick pre-filter before making book API call
            prices_raw = market.get("outcomePrices", "")
            if prices_raw:
                try:
                    pl = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                    if pl and len(pl) >= 1:
                        yp = float(pl[0])
                        if not (YES_MIN <= yp <= YES_MAX):
                            continue
                        in_range += 1
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue

            result = _score_market(event_title, market, liq)
            if result is None:
                continue

            if not result["has_real_book"]:
                no_real_book += 1
                # Still track calibration_only signals at low volume
                if verbose and len(opportunities) < 3:
                    pass  # skip logging here, too noisy
                continue  # don't add to opportunities without real book

            opportunities.append(result)

    # Sort: tradeable first, then by ev_pct descending
    opportunities.sort(key=lambda x: (not x["tradeable"], -x["ev_pct"]))

    duration = round(time.time() - t0, 1)
    tradeable_count = sum(1 for o in opportunities if o["tradeable"])

    stats = {
        "markets_checked":  markets_checked,
        "in_price_range":   in_range,
        "no_real_book":     no_real_book,
        "total_found":      len(opportunities),
        "tradeable":        tradeable_count,
        "duration_s":       duration,
    }

    log.info(
        "Longshot scan: %d checked, %d in range, %d real books, %d tradeable in %.1fs",
        markets_checked, in_range, len(opportunities), tradeable_count, duration,
    )

    if verbose:
        print(f"\nLongshot scan complete in {duration}s")
        print(f"  {markets_checked} markets checked, {in_range} in YES range")
        print(f"  {no_real_book} have no real CLOB book (skipped)")
        print(f"  {len(opportunities)} with real books, {tradeable_count} tradeable\n")
        for opp in opportunities[:10]:
            flag = "TRADE" if opp["tradeable"] else "skip "
            print(
                f"  [{flag}] {opp['event'][:30]:30s} | "
                f"YES={opp['yes_price']:.3f} bid={opp['best_yes_bid']:.3f} "
                f"ask={opp['best_yes_ask']:.3f} edge=+{opp['calibration_edge']:.4f} "
                f"EV={opp['ev_pct']:+.2f}%"
            )

    return opportunities, stats
