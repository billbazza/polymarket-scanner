"""Polymarket API client — read-only, no auth required."""
import json
import logging
import time

import requests

log = logging.getLogger("scanner.api")

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

_session = requests.Session()
_session.headers.update({"Accept": "application/json"})

_last_request = 0
_min_interval = 0.2
_INVALID_LOOKUP_VALUES = {
    "n/a",
    "na",
    "none",
    "null",
    "placeholder",
    "token_id",
    "unknown",
}
_INVALID_TOKEN_PREFIXES = ("dummy", "example", "fake", "mock", "placeholder", "sample", "test")


def _get(base, path, params=None, retries=2):
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < _min_interval:
        time.sleep(_min_interval - elapsed)
    _last_request = time.time()

    for attempt in range(retries + 1):
        try:
            resp = _session.get(f"{base}{path}", params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < retries:
                wait = 1.0 * (attempt + 1)
                log.warning("API retry %d/%d for %s: %s (waiting %.1fs)",
                            attempt + 1, retries, path, e, wait)
                time.sleep(wait)
            else:
                log.error("API failed after %d retries: %s %s", retries, path, e)
                raise


def get_events(limit=100, offset=0, active=True, tag_slug=None):
    """Fetch events (groups of related markets)."""
    params = {
        "limit": limit,
        "offset": offset,
        "active": str(active).lower(),
        "closed": "false",
    }
    if tag_slug:
        params["tag_slug"] = tag_slug
    return _get(GAMMA_BASE, "/events", params)


def get_all_active_events(max_pages=10):
    """Page through all active events."""
    all_events = []
    for page in range(max_pages):
        events = get_events(limit=100, offset=page * 100)
        if not events:
            break
        all_events.extend(events)
        if len(events) < 100:
            break
    return all_events


def get_price_history(token_id, interval="1w", fidelity=100):
    """Get historical prices. Returns list of {t: timestamp, p: price}."""
    data = _get(CLOB_BASE, "/prices-history", {
        "market": token_id,
        "interval": interval,
        "fidelity": fidelity,
    })
    return data.get("history", []) if isinstance(data, dict) else data


def get_midpoint(token_id):
    """Get midpoint price for a token."""
    data = _get(CLOB_BASE, "/midpoint", {"token_id": token_id})
    return float(data.get("mid", 0))


def get_book(token_id):
    """Get order book for a token."""
    return _get(CLOB_BASE, "/book", {"token_id": token_id})


def get_spread(token_id):
    """Get bid-ask spread."""
    return _get(CLOB_BASE, "/spread", {"token_id": token_id})


def normalize_token_id(token_id):
    """Return a cleaned token id or None when the input is clearly malformed."""
    if token_id is None:
        return None
    if isinstance(token_id, (int, float)):
        token_id = str(token_id)
    if not isinstance(token_id, str):
        return None

    token_id = token_id.strip()
    if not token_id:
        return None

    lowered = token_id.lower()
    if lowered in _INVALID_LOOKUP_VALUES:
        return None
    if any(lowered == f"{prefix}_token_id" for prefix in _INVALID_TOKEN_PREFIXES):
        return None
    if any(lowered.startswith(f"{prefix}_") for prefix in _INVALID_TOKEN_PREFIXES):
        return None
    if any(ch.isspace() for ch in token_id):
        return None
    if any(ch in token_id for ch in '[]{}",'):
        return None
    return token_id


def _normalize_lookup_value(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None
    if value.lower() in _INVALID_LOOKUP_VALUES:
        return None
    if any(ch.isspace() for ch in value):
        return None
    return value


def get_market(condition_id=None, token_id=None, market_id=None):
    """Fetch a single Gamma market by condition id, token id, or market id."""
    params = {}
    condition_id = _normalize_lookup_value(condition_id)
    token_id = normalize_token_id(token_id)
    market_id = _normalize_lookup_value(market_id)
    if condition_id:
        params["condition_ids"] = [condition_id]
    elif token_id:
        params["clob_token_ids"] = [token_id]
    elif market_id:
        params["id"] = str(market_id)
    else:
        raise ValueError("condition_id, token_id, or market_id is required")

    markets = _get(GAMMA_BASE, "/markets", params)
    return markets[0] if isinstance(markets, list) and markets else None


def extract_market_price(market, token_id):
    """Return the outcome price for a token from a Gamma market payload."""
    if not market or not token_id:
        return None
    try:
        token_ids = json.loads(market.get("clobTokenIds") or "[]")
        prices = json.loads(market.get("outcomePrices") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None

    for idx, candidate in enumerate(token_ids):
        if str(candidate) == str(token_id) and idx < len(prices):
            try:
                return float(prices[idx])
            except (TypeError, ValueError):
                return None
    return None
