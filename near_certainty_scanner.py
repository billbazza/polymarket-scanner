"""Near-certainty scanner — exploits slight underpricing of near-certain YES outcomes.

Research finding (ideas01.md):
  At 90¢, YES tokens win 91.5% of the time vs 90% implied (+1.7% edge).
  At 99¢, YES tokens win 99.5% vs 99% implied (+0.5% edge).
  Near-certainty events are systematically underpriced — the market maintains
  a small "doubt premium" that rarely materialises.

Strategy:
  Post maker BUY-YES limit orders inside the spread on markets at 85–99¢.
  Maker orders pay 0% fee. The 1.5pp calibration edge is too small to survive
  taker fees (2% wipes it). As maker:

    YES=90¢ market, YES bid=88¢, YES ask=92¢
    We post a limit BUY at 89¢ (inside spread, halfway):
      win_payout  = $1.00 − 89¢ = 11¢  (when YES resolves)
      loss_amount = 89¢               (when NO resolves)
      EV = 91.5% × 11¢ − 8.5% × 89¢ = 10.1¢ − 7.6¢ = +2.5¢ → +2.8% ✓

  Requires REAL YES book activity (bid > 80¢, ask < 100¢, spread < 15¢).

Two-layer filter:
  1. Calibration check: calibrated_yes_prob > yes_price + MIN_CALIBRATION_EDGE
  2. Brain check (optional, requires ANTHROPIC_API_KEY): Claude estimates true
     probability. Only proceed if brain_estimate ≥ MIN_BRAIN_CONFIDENCE.

  Layer 1 catches structural mispricing.
  Layer 2 prevents betting on markets where doubt IS justified (e.g., a political
  outcome that could genuinely go either way, priced at 90¢ on momentum alone).

Calibration table (empirical Polymarket data from research/ideas01.md):
  YES price   Actual win%   Implied win%   Edge
  85¢         86.6%         85.0%          +1.6pp
  90¢         91.5%         90.0%          +1.5pp  ← sweet spot
  95¢         96.0%         95.0%          +1.0pp
  99¢         99.5%         99.0%          +0.5pp

Execution:
  Maker BUY-YES limit at best_yes_bid + MAKER_AGGRESSION * spread.
  Fee = 0% (maker). Single leg, no exit — market resolves.
  Fill probability depends on spread width and market volume.
"""
import json
import logging
import time

import api
import math_engine

log = logging.getLogger("scanner.near_certainty")

# ── Strategy parameters ─────────────────────────────────────────────────────
YES_MIN              = 0.85   # only markets at 85¢ or above
YES_MAX              = 0.99   # don't touch markets effectively resolved (99¢+)
MIN_LIQUIDITY        = 5000   # need liquid markets for fills
MIN_CALIBRATION_EDGE = 0.005  # at least 0.5pp above market price (calibration)
MIN_EV_PCT           = 0.50   # min EV% (maker, no fee) to be tradeable
MIN_BRAIN_CONFIDENCE = 0.90   # brain must give ≥90% to confirm near-certainty

# Use brain validation (requires ANTHROPIC_API_KEY). Skip if unavailable.
USE_BRAIN = True

# Maker fee = 0%. No taker execution — taker fee (2%) wipes the calibration edge.
MAKER_FEE_PCT = 0.00

# How far inside the YES spread to post our limit BUY.
# 0.5 = halfway (mid). Lower = closer to bid (higher fill rate, less EV).
# Higher = closer to ask (lower fill rate, more EV since we pay less for YES).
MAKER_AGGRESSION = 0.5

# Conservative estimate of fill probability for YES bid inside the spread.
# Near-certainty markets are actively traded, so 60% is reasonable.
FILL_PROB = 0.60

# Filters for real CLOB book activity on YES side
REAL_BOOK_BID_MIN = 0.75   # YES best bid must be > 75¢ (real buyer present)
REAL_BOOK_ASK_MAX = 1.00   # YES best ask < $1 (trivially true but sanity check)
REAL_BOOK_SPREAD  = 0.15   # absolute spread < 15¢ (market being actively made)

# Calibration table: YES_price → actual_YES_win_rate
_CALIBRATION = [
    (0.85, 0.866),
    (0.90, 0.915),
    (0.95, 0.960),
    (0.99, 0.995),
]


def _calibrated_yes_prob(yes_price: float) -> float:
    """Interpolate the empirical near-certainty YES win rate."""
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

    return yes_price


def _ev_maker(limit_price: float, calibrated_prob: float) -> dict:
    """EV of a maker BUY-YES limit order filled at limit_price.

    Maker fee = 0%. We pay limit_price per YES share.
    Win (YES resolves): $1.00 payout, profit = 1.0 - limit_price
    Lose (NO resolves): $0.00 payout, loss = limit_price
    """
    win_payout  = 1.0 - limit_price
    loss_amount = limit_price

    ev     = calibrated_prob * win_payout - (1 - calibrated_prob) * loss_amount
    ev_pct = ev / limit_price * 100 if limit_price > 0 else 0.0

    return {
        "cost":        round(limit_price, 4),
        "fee":         0.0,
        "win_payout":  round(win_payout, 4),
        "loss_amount": round(loss_amount, 4),
        "ev":          round(ev, 4),
        "ev_pct":      round(ev_pct, 3),
    }


def _score_market(event_title, market, event_liquidity, use_brain=True):
    """Score a single market for near-certainty YES maker edge.

    Returns a scored dict or None if the market doesn't qualify.
    """
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
        no_price  = float(price_list[1])
    except (ValueError, TypeError):
        return None

    # Only near-certain YES markets in target range
    if not (YES_MIN <= yes_price <= YES_MAX):
        return None

    yes_token = token_list[0]
    no_token  = token_list[1]

    # ── Calibration edge ────────────────────────────────────────────────────
    calibrated_yes   = _calibrated_yes_prob(yes_price)
    calibration_edge = calibrated_yes - yes_price   # positive = YES underpriced

    if calibration_edge < MIN_CALIBRATION_EDGE:
        return None

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
    limit_price   = yes_price  # fallback: use AMM mid

    if bids and asks:
        b0 = float(bids[0].get("price", 0))
        a0 = float(asks[0].get("price", 1))
        if (b0 > REAL_BOOK_BID_MIN
                and a0 <= REAL_BOOK_ASK_MAX
                and (a0 - b0) < REAL_BOOK_SPREAD
                and b0 < a0):
            has_real_book = True
            best_yes_bid  = b0
            best_yes_ask  = a0
            spread_abs    = a0 - b0
            # Maker BUY limit: halfway inside the spread on the bid side
            limit_price   = round(best_yes_bid + MAKER_AGGRESSION * spread_abs, 4)

    # ── EV calculation (maker, 0% fee) ──────────────────────────────────────
    ev_result = _ev_maker(limit_price, calibrated_yes)

    # Adjust for fill probability
    fill_prob      = FILL_PROB if has_real_book else 0.0
    ev_adj         = round(ev_result["ev"] * fill_prob, 4)
    ev_pct_adj     = round(ev_adj / limit_price * 100, 3) if limit_price > 0 else 0.0

    kelly_f = math_engine.kelly_fraction(
        calibrated_yes,
        ev_result["win_payout"],
        ev_result["loss_amount"],
    )

    # ── Brain validation (optional) ─────────────────────────────────────────
    brain_result    = None
    brain_prob      = None
    brain_confirmed = False

    if use_brain and has_real_book:  # only spend API calls on real opportunities
        try:
            import brain
            question = market.get("question", market.get("groupItemTitle", ""))
            context  = (f"Event: {event_title}. "
                        f"Current market price: {yes_price:.1%}. "
                        f"Liquidity: ${event_liquidity:,.0f}.")
            brain_result = brain.estimate_probability(question, yes_price, context=context)
            if brain_result:
                brain_prob      = brain_result.get("probability", 0)
                brain_confirmed = brain_prob >= MIN_BRAIN_CONFIDENCE
        except Exception as e:
            log.debug("Brain validation failed: %s", e)

    # ── Tradeable determination ──────────────────────────────────────────────
    if brain_result is not None:
        tradeable = (
            has_real_book
            and ev_pct_adj >= MIN_EV_PCT
            and kelly_f > 0
            and brain_confirmed
        )
    else:
        tradeable = (
            has_real_book
            and ev_pct_adj >= MIN_EV_PCT
            and kelly_f > 0
        )

    result = {
        "event":             event_title,
        "market":            market.get("question", market.get("groupItemTitle", "")),
        "market_id":         str(market.get("id", "")),
        "yes_token":         yes_token,
        "no_token":          no_token,
        "yes_price":         round(yes_price, 4),
        "no_price":          round(no_price, 4),
        "calibrated_yes":    round(calibrated_yes, 4),
        "calibration_edge":  round(calibration_edge, 4),
        "has_real_book":     has_real_book,
        "best_yes_bid":      round(best_yes_bid, 4),
        "best_yes_ask":      round(best_yes_ask, 4),
        "spread_abs":        round(spread_abs, 4),
        "limit_price":       limit_price,
        "ev_pct":            ev_pct_adj,
        "ev":                ev_adj,
        "cost":              ev_result["cost"],
        "fee":               0.0,
        "kelly_fraction":    kelly_f,
        "fill_prob":         fill_prob,
        "liquidity":         event_liquidity,
        "brain_prob":        brain_prob,
        "brain_confirmed":   brain_confirmed,
        "action":            "BUY_YES",
        "tradeable":         tradeable,
    }

    if has_real_book:
        log.info(
            "Near-certainty: %s YES=%.3f bid=%.3f ask=%.3f limit=%.3f "
            "calib=%.4f ev=%.2f%% brain=%s [%s]",
            result["market"][:50], yes_price, best_yes_bid, best_yes_ask,
            limit_price, calibrated_yes, ev_pct_adj,
            f"{brain_prob:.1%}" if brain_prob else "n/a",
            "TRADE" if tradeable else "skip",
        )
    else:
        log.debug("Near-certainty skip (no real book): %s YES=%.3f",
                  result["market"][:40], yes_price)

    return result


def scan(min_liquidity=MIN_LIQUIDITY, min_ev_pct=MIN_EV_PCT,
         use_brain=USE_BRAIN, verbose=True):
    """Scan all active markets for near-certainty YES edge.

    Returns:
        (opportunities, stats) where opportunities is sorted by ev_pct descending.
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
    opportunities    = []

    for event in events:
        liq = float(event.get("liquidity", 0) or 0)
        if liq < min_liquidity:
            continue

        event_title = event.get("title", "")
        markets = event.get("markets", [])

        for market in markets:
            markets_checked += 1

            # Quick pre-filter
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

            result = _score_market(event_title, market, liq, use_brain=use_brain)
            if result is not None and result["has_real_book"]:
                opportunities.append(result)

    # Sort: tradeable first, then by ev_pct descending
    opportunities.sort(key=lambda x: (not x["tradeable"], -x["ev_pct"]))

    duration = round(time.time() - t0, 1)
    tradeable_count = sum(1 for o in opportunities if o["tradeable"])
    brain_confirmed  = sum(1 for o in opportunities if o.get("brain_confirmed"))

    stats = {
        "markets_checked": markets_checked,
        "in_price_range":  in_range,
        "total_found":     len(opportunities),
        "tradeable":       tradeable_count,
        "brain_confirmed": brain_confirmed,
        "duration_s":      duration,
    }

    log.info(
        "Near-certainty scan: %d checked, %d in range, %d tradeable (%d brain-confirmed) in %.1fs",
        markets_checked, in_range, tradeable_count, brain_confirmed, duration,
    )

    if verbose:
        print(f"\nNear-certainty scan complete in {duration}s")
        print(f"  {markets_checked} markets checked, {in_range} in YES range [85-99¢]")
        print(f"  {len(opportunities)} candidates, {tradeable_count} tradeable, "
              f"{brain_confirmed} brain-confirmed\n")
        for opp in opportunities[:10]:
            flag = "TRADE" if opp["tradeable"] else "skip "
            brain_str = f"brain={opp['brain_prob']:.1%}" if opp["brain_prob"] else "brain=n/a"
            print(
                f"  [{flag}] {opp['event'][:30]:30s} | "
                f"YES={opp['yes_price']:.3f} edge=+{opp['calibration_edge']:.4f} "
                f"EV={opp['ev_pct']:+.2f}% {brain_str}"
            )

    return opportunities, stats
