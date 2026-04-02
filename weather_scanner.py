"""Weather edge scanner — threshold weather strategy over shared provider sources.

Strategy:
  Two independent forecast sources give us a probability for each temperature
  bucket market. Retail users price these on phone apps or vibes.

  The default provider layer still uses:
    NOAA NWS  — US government model, hyper-accurate 24-48h, hourly resolution.
    Open-Meteo — Open-source aggregator that blends NOAA, ECMWF, GFS and more.
               Returns daily high/low directly. Free, no key required.

  For each market we compute:
    noaa_prob      — probability from NOAA hourly forecast
    om_prob        — probability from Open-Meteo daily forecast
    combined_prob  — simple mean of the two
    sources_agree  — both independently point the same direction vs market price

  tradeable = sources_agree AND |combined_edge| >= min_edge AND EV > 0

  Agreement is the key gate: when two independent forecast systems both say
  the market is mispriced, confidence is substantially higher.

APIs:
  NOAA NWS:   https://api.weather.gov  (free, no key, US only)
  Open-Meteo: https://api.open-meteo.com/v1/forecast  (free, no key, global)

Probability model (both sources):
  actual_high ~ N(forecast_high, σ²)
  σ = 2.5°F ≤24h | 3.5°F ≤48h | 5.0°F ≤72h | 6.5°F >72h
  P(high > T) = 1 − Φ((T − forecast_high) / σ)
  P(high < T) = Φ((T − forecast_high) / σ)
"""
import json
import logging
import re
import time
from datetime import date, timedelta

from scipy.stats import norm

import api
import math_engine
import weather_sources

log = logging.getLogger("scanner.weather")

# Forecast σ (°F) by hours ahead — calibrated to NWS verification statistics
_SIGMA_BY_HOURS = [(24, 2.5), (48, 3.5), (72, 5.0), (999, 6.5)]

MIN_EDGE = 0.06        # 6pp minimum combined edge to surface (for display)
MIN_TRADE_EDGE = 0.15  # 15pp minimum edge required to mark tradeable
MIN_TRADE_PRICE = 0.35 # never buy a token below this price (avoids near-wipeout long shots)
MIN_LIQUIDITY = 200    # minimum event liquidity USD

_WEATHER_KEYWORDS = [
    "temperature", "°f", "°c", "degrees", "high temp", "low temp",
    "heat", "cold", "warm", "weather", "fahrenheit", "celsius",
]

# City name → (lat, lon)
CITIES = {
    "new york city": (40.7128, -74.0060),
    "new york":      (40.7128, -74.0060),
    "nyc":           (40.7128, -74.0060),
    "los angeles":   (34.0522, -118.2437),
    "chicago":       (41.8781, -87.6298),
    "houston":       (29.7604, -95.3698),
    "phoenix":       (33.4484, -112.0740),
    "philadelphia":  (39.9526, -75.1652),
    "san antonio":   (29.4241, -98.4936),
    "san diego":     (32.7157, -117.1611),
    "dallas":        (32.7767, -96.7970),
    "san francisco": (37.7749, -122.4194),
    "seattle":       (47.6062, -122.3321),
    "denver":        (39.7392, -104.9903),
    "boston":        (42.3601, -71.0589),
    "miami":         (25.7617, -80.1918),
    "atlanta":       (33.7490, -84.3880),
    "minneapolis":   (44.9778, -93.2650),
    "washington dc": (38.9072, -77.0369),
    "washington":    (38.9072, -77.0369),
    "las vegas":     (36.1699, -115.1398),
    "portland":      (45.5051, -122.6750),
    "nashville":     (36.1627, -86.7816),
    "memphis":       (35.1495, -90.0490),
    "baltimore":     (39.2904, -76.6122),
    "milwaukee":     (43.0389, -87.9065),
    "albuquerque":   (35.0844, -106.6504),
    "sacramento":    (38.5816, -121.4944),
    "kansas city":   (39.0997, -94.5786),
    "raleigh":       (35.7796, -78.6382),
    "tampa":         (27.9506, -82.4572),
    "new orleans":   (29.9511, -90.0715),
    "cleveland":     (41.4993, -81.6944),
    "pittsburgh":    (40.4406, -79.9959),
    "detroit":       (42.3314, -83.0458),
    "indianapolis":  (39.7684, -86.1581),
    "jacksonville":  (30.3322, -81.6557),
    "charlotte":     (35.2271, -80.8431),
    "austin":        (30.2672, -97.7431),
    "orlando":       (28.5383, -81.3792),
    "cincinnati":    (39.1031, -84.5120),
    "st. louis":     (38.6270, -90.1994),
    "st louis":      (38.6270, -90.1994),
    # International cities (active on Polymarket weather tab) — no NOAA coverage
    "hong kong":     (22.3193,  114.1694),
    "taipei":        (25.0330,  121.5654),
    "seoul":         (37.5665,  126.9780),
    "shanghai":      (31.2304,  121.4737),
    "shenzhen":      (22.5431,  114.0579),
    "beijing":       (39.9042,  116.4074),
    "chengdu":       (30.5728,  104.0668),
    "chongqing":     (29.5630,  106.5516),
    "wuhan":         (30.5928,  114.3052),
    "tokyo":         (35.6762,  139.6503),
    "singapore":     (1.3521,   103.8198),
    "istanbul":      (41.0082,   28.9784),
    "ankara":        (39.9334,   32.8597),
    "dubai":         (25.2048,   55.2708),
    "tel aviv":      (32.0853,   34.7818),
    "london":        (51.5074,   -0.1278),
    "paris":         (48.8566,    2.3522),
    "madrid":        (40.4168,   -3.7038),
    "milan":         (45.4642,    9.1900),
    "munich":        (48.1351,   11.5820),
    "warsaw":        (52.2297,   21.0122),
    "sydney":        (-33.8688, 151.2093),
    "melbourne":     (-37.8136, 144.9631),
    "wellington":    (-41.2865, 174.7762),
    "toronto":       (43.6532,  -79.3832),
    "vancouver":     (49.2827, -123.1207),
    "mexico city":   (19.4326,  -99.1332),
    "sao paulo":     (-23.5505, -46.6333),
    "buenos aires":  (-34.6037, -58.3816),
    "lucknow":       (26.8467,   80.9462),
}

_MONTH_NUMS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# ---------------------------------------------------------------------------
# Market question parser
# ---------------------------------------------------------------------------

def _parse_question(question):
    """Extract city, threshold_f, direction, target_date from a market question.

    Returns dict or None if unparseable.
    """
    q = question.lower()

    if not any(kw in q for kw in _WEATHER_KEYWORDS):
        return None

    # Temperature threshold — detect unit, always store in °F internally
    celsius = False
    m = re.search(r'(\d+(?:\.\d+)?)\s*°?\s*c\b', q)
    if m:
        celsius = True
    if not m:
        m = re.search(r'(\d+(?:\.\d+)?)\s*degrees?\s*celsius', q)
        if m:
            celsius = True
    if not m:
        m = re.search(r'(\d+(?:\.\d+)?)\s*°?\s*f\b', q)
    if not m:
        m = re.search(r'(\d+(?:\.\d+)?)\s*degrees?\s*fahrenheit', q)
    if not m:
        m = re.search(r'(\d+(?:\.\d+)?)\s*degrees?\b', q)
    if not m:
        return None
    raw = float(m.group(1))
    threshold_f = raw * 9 / 5 + 32 if celsius else raw
    if not (0 <= threshold_f <= 130):
        return None

    # Direction
    above_kws = ["above", "exceed", "exceeds", "exceeded", "hit", "reach",
                 "at least", "or higher", "or more", "higher than", "over"]
    below_kws = ["below", "under", "not reach", "lower", "no more than",
                 "or less", "or lower", "beneath", "less than"]
    direction = None
    for kw in above_kws:
        if kw in q:
            direction = "above"
            break
    if not direction:
        for kw in below_kws:
            if kw in q:
                direction = "below"
                break
    if not direction:
        return None

    # City — longest match first to avoid "new york" eating "new york city"
    city_name = None
    coords = None
    for name in sorted(CITIES, key=len, reverse=True):
        if name in q:
            city_name = name
            coords = CITIES[name]
            break
    if not coords:
        return None

    target = _parse_date(q)
    if target is None:
        return None

    days_ahead = (target - date.today()).days
    if days_ahead < 0 or days_ahead > 7:
        return None

    return {
        "city": city_name,
        "city_key": city_name,
        "lat": coords[0],
        "lon": coords[1],
        "threshold_f": threshold_f,
        "direction": direction,
        "target_date": target.isoformat(),
        "days_ahead": days_ahead,
    }


def _parse_date(q):
    """Return a date from a lowercase question string, or None."""
    today = date.today()

    if "today" in q:
        return today
    if "tomorrow" in q:
        return today + timedelta(days=1)

    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for i, name in enumerate(days):
        if name in q:
            days_ahead = (i - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return today + timedelta(days=days_ahead)

    for name in sorted(_MONTH_NUMS, key=len, reverse=True):
        m = re.search(rf'{name}\s+(\d{{1,2}})', q)
        if m:
            month = _MONTH_NUMS[name]
            day = int(m.group(1))
            year = today.year
            try:
                d = date(year, month, day)
                if d < today:
                    d = date(year + 1, month, day)
                return d
            except ValueError:
                return None

    return None


# ---------------------------------------------------------------------------
# Probability model (shared)
# ---------------------------------------------------------------------------

def _forecast_sigma(hours_ahead):
    for hours, sigma in _SIGMA_BY_HOURS:
        if hours_ahead <= hours:
            return sigma
    return 6.5


def _calc_probability(forecast_val, threshold_f, direction, hours_ahead):
    """Convert a point forecast to a probability using a normal error model.

    Returns (probability, sigma_used).
    """
    sigma = _forecast_sigma(hours_ahead)
    if direction == "above":
        prob = float(1 - norm.cdf((threshold_f - forecast_val) / sigma))
    else:
        prob = float(norm.cdf((threshold_f - forecast_val) / sigma))
    return round(prob, 4), sigma


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan(min_edge=MIN_EDGE, min_liquidity=MIN_LIQUIDITY, verbose=True):
    """Scan Polymarket temperature markets for edge using NOAA + Open-Meteo forecasts.

    Both sources must agree for a signal to be marked tradeable.
    Combined probability = mean(noaa_prob, om_prob).

    Args:
        min_edge: minimum |combined_prob - market_price| (default 0.06)
        min_liquidity: minimum event liquidity USD
        verbose: print progress to stdout

    Returns:
        list of opportunity dicts sorted by |combined_edge| descending
    """
    t0 = time.time()

    if verbose:
        print("Fetching active events...", end=" ", flush=True)

    try:
        # Use tag_slug=weather to fetch only weather events — much faster than
        # scanning all events, and catches markets the general scan misses
        all_weather = []
        for page in range(5):
            batch = api.get_events(limit=100, offset=page * 100, tag_slug="weather")
            if not batch:
                break
            all_weather.extend(batch)
            if len(batch) < 100:
                break
        events = all_weather
    except Exception as e:
        log.error("Failed to fetch events: %s", e)
        return []

    if verbose:
        print(f"{len(events)} events")

    opportunities = []
    markets_checked = 0
    parsed_count = 0
    fetch_errors = {"noaa": 0, "om": 0}

    for event in events:
        liq = float(event.get("liquidity", 0) or 0)
        if liq < min_liquidity:
            continue

        event_title = event.get("title", "")

        for market in event.get("markets", []):
            question = market.get("question", market.get("groupItemTitle", ""))
            if not question:
                continue

            markets_checked += 1
            parsed = _parse_question(question)
            if parsed is None:
                continue
            parsed_count += 1

            # Current market YES price
            try:
                price_list = json.loads(market.get("outcomePrices", "[]"))
                token_list = json.loads(market.get("clobTokenIds", "[]"))
                yes_price = float(price_list[0])
                yes_token = token_list[0] if token_list else None
                no_token  = token_list[1] if len(token_list) > 1 else None
            except (json.JSONDecodeError, TypeError, IndexError, ValueError):
                continue

            if not (0.02 <= yes_price <= 0.98):
                continue

            lat, lon = parsed["lat"], parsed["lon"]
            target_date = parsed["target_date"]
            threshold_f = parsed["threshold_f"]
            direction = parsed["direction"]
            hours_ahead = parsed["days_ahead"] * 24

            source_results = weather_sources.fetch_threshold_forecasts(
                lat,
                lon,
                target_date,
                city_key=parsed.get("city_key"),
            )
            source_map = {result["source_id"]: result for result in source_results}

            noaa_result = source_map.get("noaa", {})
            noaa_high = noaa_result.get("value_f")
            if noaa_result.get("attempted") and noaa_high is None:
                fetch_errors["noaa"] += 1
            noaa_prob, noaa_sigma = (
                _calc_probability(noaa_high, threshold_f, direction, hours_ahead)
                if noaa_high is not None else (None, None)
            )

            om_result = source_map.get("open-meteo", {})
            om_high = om_result.get("value_f")
            if om_result.get("attempted") and om_high is None:
                fetch_errors["om"] += 1
            om_prob, _ = (
                _calc_probability(om_high, threshold_f, direction, hours_ahead)
                if om_high is not None else (None, None)
            )

            # Need at least one source
            available = [p for p in [noaa_prob, om_prob] if p is not None]
            if not available:
                continue

            combined_prob = round(sum(available) / len(available), 4)

            # Agreement: both sources available and both point the same direction
            # relative to the market price
            sources_agree = (
                noaa_prob is not None and om_prob is not None
                and (noaa_prob - yes_price) * (om_prob - yes_price) > 0
            )
            # Single-source (international cities: NOAA unavailable) — Open-Meteo only
            single_source_ok = noaa_prob is None and om_prob is not None

            combined_edge = combined_prob - yes_price
            if abs(combined_edge) < min_edge:
                continue

            action = "BUY_YES" if combined_edge > 0 else "BUY_NO"
            our_price = yes_price if action == "BUY_YES" else (1 - yes_price)
            our_prob  = combined_prob if action == "BUY_YES" else (1 - combined_prob)

            ev_pct = round(
                (our_prob * (1 - our_price) - (1 - our_prob) * our_price) * 100, 2
            )
            kelly_f = math_engine.kelly_fraction(our_prob, 1 - our_price, our_price)

            # Tradeable: BOTH sources must agree (no single-source trades for international)
            # AND edge >= 15pp AND the token we're buying must be >= 35¢ (no long shots)
            tradeable = (
                sources_agree
                and ev_pct > 0
                and kelly_f > 0
                and abs(combined_edge) >= MIN_TRADE_EDGE
                and our_price >= MIN_TRADE_PRICE
            )

            opp = {
                # Identity
                "event": event_title,
                "market": question,
                "market_id": str(market.get("id", "")),
                "yes_token": yes_token,
                "no_token": no_token,
                "city": parsed["city"],
                "lat": lat,
                "lon": lon,
                "target_date": target_date,
                "threshold_f": threshold_f,
                "direction": direction,
                # Market
                "market_price": round(yes_price, 4),
                # NOAA stream
                "noaa_forecast_f": noaa_high,
                "noaa_prob": noaa_prob,
                "noaa_sigma_f": noaa_sigma,
                # Open-Meteo stream
                "om_forecast_f": om_high,
                "om_prob": om_prob,
                # Combined
                "combined_prob": combined_prob,
                "combined_edge": round(combined_edge, 4),
                "combined_edge_pct": round(combined_edge * 100, 2),
                "sources_agree": sources_agree,
                "sources_available": len(available),
                "source_details": source_results,
                # Scoring
                "hours_ahead": hours_ahead,
                "ev_pct": ev_pct,
                "kelly_fraction": kelly_f,
                "action": action,
                "tradeable": tradeable,
                "liquidity": liq,
            }

            opportunities.append(opp)
            log.info(
                "%s | %s %s %.0f°F | price=%.3f noaa=%.3f om=%s combined=%.3f "
                "edge=%+.1f%% agree=%s action=%s",
                parsed["city"], target_date, direction, threshold_f,
                yes_price,
                noaa_prob if noaa_prob is not None else -1,
                f"{om_prob:.3f}" if om_prob is not None else "n/a",
                combined_prob, combined_edge * 100,
                sources_agree, action,
            )

    opportunities.sort(key=lambda x: (not x["tradeable"], -abs(x["combined_edge"])))

    duration = round(time.time() - t0, 1)
    tradeable_count = sum(1 for o in opportunities if o["tradeable"])
    log.info(
        "Weather scan: %d markets, %d weather, %d opps (%d tradeable) in %.1fs "
        "[noaa_errors=%d om_errors=%d]",
        markets_checked, parsed_count, len(opportunities), tradeable_count, duration,
        fetch_errors["noaa"], fetch_errors["om"],
    )

    if verbose:
        print(
            f"Checked {markets_checked} → {parsed_count} weather markets "
            f"→ {len(opportunities)} opportunities ({tradeable_count} tradeable) "
            f"in {duration}s\n"
        )
        if any(fetch_errors.values()):
            print(f"  Fetch errors — NOAA: {fetch_errors['noaa']}  Open-Meteo: {fetch_errors['om']}\n")
        for opp in opportunities[:10]:
            flag = "TRADE" if opp["tradeable"] else ("1-src" if opp["sources_available"] == 1 else "no-agree")
            noaa_str = f"{opp['noaa_prob']:.3f}({opp['noaa_forecast_f']}°)" if opp["noaa_prob"] is not None else "n/a"
            om_str   = f"{opp['om_prob']:.3f}({opp['om_forecast_f']}°)"   if opp["om_prob"]   is not None else "n/a"
            print(
                f"  [{flag:7s}] {opp['city'].title():15s} {opp['target_date']} "
                f"{opp['direction']:5s} {opp['threshold_f']:.0f}°F | "
                f"mkt={opp['market_price']:.3f}  "
                f"NOAA={noaa_str}  OM={om_str}  "
                f"combined={opp['combined_prob']:.3f}  "
                f"edge={opp['combined_edge_pct']:+.1f}%  → {opp['action']}"
            )

    return opportunities, {"markets_checked": markets_checked, "weather_found": parsed_count, "fetch_errors": fetch_errors}
