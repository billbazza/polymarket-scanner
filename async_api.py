"""Async Polymarket API client — parallel requests via httpx.

Drop-in replacement for api.py when running under asyncio.
Falls back gracefully on connection errors with retries.
"""
import asyncio
import logging
import httpx

log = logging.getLogger("scanner.async_api")

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

# Shared client — connection pooling across all requests
_client = None


def _get_client():
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Accept": "application/json"},
        )
    return _client


async def close():
    """Close the shared client. Call on shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def _get(base, path, params=None, retries=2):
    client = _get_client()
    for attempt in range(retries + 1):
        try:
            resp = await client.get(f"{base}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout) as e:
            if attempt < retries:
                wait = 1.0 * (attempt + 1)
                log.warning("Async retry %d/%d for %s: %s", attempt + 1, retries, path, e)
                await asyncio.sleep(wait)
            else:
                log.error("Async API failed after %d retries: %s %s", retries, path, e)
                raise


async def get_events(limit=100, offset=0, active=True):
    return await _get(GAMMA_BASE, "/events", {
        "limit": limit, "offset": offset,
        "active": str(active).lower(), "closed": "false",
    })


async def get_price_history(token_id, interval="1w", fidelity=100):
    data = await _get(CLOB_BASE, "/prices-history", {
        "market": token_id, "interval": interval, "fidelity": fidelity,
    })
    return data.get("history", []) if isinstance(data, dict) else data


async def get_midpoint(token_id):
    data = await _get(CLOB_BASE, "/midpoint", {"token_id": token_id})
    return float(data.get("mid", 0))


async def get_book(token_id):
    return await _get(CLOB_BASE, "/book", {"token_id": token_id})


async def get_spread(token_id):
    return await _get(CLOB_BASE, "/spread", {"token_id": token_id})


# --- Batch helpers ---

async def get_price_histories(token_ids, interval="1w", fidelity=100, max_concurrent=10):
    """Fetch price histories for multiple tokens in parallel.

    Returns dict of {token_id: history_list}.
    """
    sem = asyncio.Semaphore(max_concurrent)

    async def fetch_one(tid):
        async with sem:
            try:
                return tid, await get_price_history(tid, interval, fidelity)
            except Exception as e:
                log.warning("Failed to fetch history for %s: %s", tid[:16], e)
                return tid, []

    results = await asyncio.gather(*[fetch_one(tid) for tid in token_ids])
    return dict(results)
