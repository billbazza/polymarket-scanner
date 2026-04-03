"""Perplexity AI research — real-time web context for market signals.

Uses Perplexity's sonar model (OpenAI-compatible API) to fetch current
news and developments relevant to a prediction market before Claude validates.

Flow: A+ signal found → research_signal() → context string → brain.validate_signal()

Cost: ~$0.005 per research query (sonar pricing).
Degrades gracefully if PERPLEXITY_API_KEY is not set.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("scanner.perplexity")

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
PERPLEXITY_MODEL = "sonar"  # fast + cheap; swap to "sonar-pro" for deeper research
PERPLEXITY_CACHE_FILE = Path(__file__).parent / "perplexity_cache.json"
PERPLEXITY_CACHE_TTL = 4 * 60 * 60  # seconds


def _load_cache():
    if not PERPLEXITY_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(PERPLEXITY_CACHE_FILE.read_text())
    except Exception as exc:
        log.warning("Failed to load Perplexity cache: %s", exc)
        return {}


def _save_cache():
    try:
        PERPLEXITY_CACHE_FILE.write_text(json.dumps(_PERPLEXITY_CACHE, indent=2))
    except Exception as exc:
        log.warning("Failed to save Perplexity cache: %s", exc)


def _cache_key(signal: dict) -> str:
    key_data = {
        "event": signal.get("event", ""),
        "market_a": signal.get("market_a", ""),
        "market_b": signal.get("market_b", ""),
        "z_score": round(float(signal.get("z_score") or 0), 3),
        "price_a": round(float(signal.get("price_a") or 0), 3),
        "price_b": round(float(signal.get("price_b") or 0), 3),
        "action": signal.get("action", ""),
    }
    return hashlib.sha1(json.dumps(key_data, sort_keys=True).encode()).hexdigest()


def _cached_result(signal: dict):
    entry = _PERPLEXITY_CACHE.get(_cache_key(signal))
    if not entry:
        return None
    if time.time() - entry.get("cached_at", 0) > PERPLEXITY_CACHE_TTL:
        return None
    cached = entry.get("result")
    if cached:
        cached = dict(cached)
        cached["cached"] = True
    return cached


def _store_result(signal: dict, result: dict):
    if result.get("status") != "ok":
        return
    key = _cache_key(signal)
    entry = {
        "cached_at": time.time(),
        "result": result,
    }
    _PERPLEXITY_CACHE[key] = entry
    _save_cache()


_PERPLEXITY_CACHE = _load_cache()


def _get_client():
    """Get OpenAI-compatible client pointed at Perplexity. Returns None if no key."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        log.debug("No PERPLEXITY_API_KEY set — research disabled")
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key, base_url=PERPLEXITY_BASE_URL)
    except ImportError:
        log.warning("openai package not installed — run: pip install openai")
        return None


def research_market(question, event=""):
    """Research a single prediction market question using Perplexity.

    Args:
        question: The market question (e.g. "Will Chelsea finish in 3rd place?")
        event: The parent event title for extra context.

    Returns:
        str: A concise summary of relevant recent news, or empty string if unavailable.
    """
    client = _get_client()
    if not client:
        return ""

    prompt = (
        f"I'm evaluating a prediction market: '{question}'\n"
        f"Event context: {event}\n\n"
        f"Provide a concise 2-3 sentence summary of the most recent relevant news, "
        f"current odds/probabilities if available, and any key factors that would affect "
        f"this outcome. Focus only on facts relevant to predicting this market. "
        f"Be brief and factual."
    )

    try:
        response = client.chat.completions.create(
            model=PERPLEXITY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise research assistant for prediction markets. "
                        "Provide only relevant, factual, up-to-date information. "
                        "No disclaimers or filler text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,
        )
        context = response.choices[0].message.content.strip()
        log.info("Perplexity research: %s → %d chars", question[:40], len(context))
        return context

    except Exception as e:
        log.warning("Perplexity research failed for '%s': %s", question[:40], e)
        return ""


def research_signal(signal):
    """Research both legs of a pairs signal.

    Args:
        signal: dict with market_a, market_b, event fields.

    Returns:
        dict with keys 'context_a', 'context_b', 'combined' — all strings.
        Returns empty strings if Perplexity is unavailable.
    """
    client = _get_client()
    if not client:
        return {"context_a": "", "context_b": "", "combined": ""}

    event = signal.get("event", "")
    market_a = signal.get("market_a", "")
    market_b = signal.get("market_b", "")

    log.info("Researching signal: %s", event[:60])

    context_a = research_market(market_a, event)
    context_b = research_market(market_b, event)

    combined = ""
    if context_a or context_b:
        parts = []
        if context_a:
            parts.append(f"Market A ({market_a[:50]}): {context_a}")
        if context_b:
            parts.append(f"Market B ({market_b[:50]}): {context_b}")
        combined = "\n\n".join(parts)

    return {
        "context_a": context_a,
        "context_b": context_b,
        "combined": combined,
    }


def is_available():
    """Check if Perplexity is configured and usable."""
    return bool(os.environ.get("PERPLEXITY_API_KEY"))


def _build_evaluation_prompt(signal, research_context):
    context_block = ""
    if research_context:
        context_block = f"\nReal-time context:\n{research_context}\n"

    ev_pct = signal.get("ev", {}).get("ev_pct")
    ev_info = f"EV: {ev_pct:.1f}%\n" if isinstance(ev_pct, (int, float)) else ""
    return (
        "You are a Polymarket research analyst validating a cointegration signal. "
        "Evaluate whether the pair is a profitable candidate for the Stage 2 trial bucket. "
        "Consider the z-score, cointegration p-value, half-life, liquidity, and any recent news.\n\n"
        f"Event: {signal.get('event', '')}\n"
        f"A: {signal.get('market_a', '')} @ {signal.get('price_a', 0):.1%}\n"
        f"B: {signal.get('market_b', '')} @ {signal.get('price_b', 0):.1%}\n"
        f"Z-score: {signal.get('z_score', 0):+.2f}\n"
        f"Half-life: {signal.get('half_life', 0):.2f}\n"
        f"Liquidity: ${signal.get('liquidity', 0):,.0f}\n"
        f"24h Volume: ${signal.get('volume_24h', 0):,.0f}\n"
        f"{ev_info}"
        f"{context_block}"
        "Respond with ONLY valid JSON containing:\n"
        "{\"profitable_candidate\": true/false, \"confidence\": 0-1, \"reasoning\": \"1-2 sentences\"}."
        "If you lack fresh news, set profitable_candidate to false and explain why."
    )


def _parse_confidence(value):
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(conf, 1.0))


def evaluate_signal(signal):
    """Evaluate whether a signal is a Stage 2 profitable candidate using Perplexity."""
    cached = _cached_result(signal)
    if cached:
        log.info(
            "Perplexity cache hit (%s): %s",
            cached.get("status"),
            signal.get("event", "?")[:40],
        )
        return cached

    client = _get_client()
    if not client:
        return {
            "status": "disabled",
            "profitable_candidate": False,
            "confidence": 0.0,
            "reason": "Perplexity disabled (no API key or missing client)",
            "context": "",
        }

    research = research_signal(signal)
    context = research.get("combined", "")
    prompt = _build_evaluation_prompt(signal, context)

    try:
        response = client.chat.completions.create(
            model=PERPLEXITY_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise research partner for prediction markets. "
                        "Stick to facts and do not add extra explanation beyond the requested JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=250,
            temperature=0.0,
        )
        text = response.choices[0].message.content.strip()
        payload = json.loads(text)
        candidate = bool(payload.get("profitable_candidate"))
        result = {
            "status": "ok",
            "profitable_candidate": candidate,
            "confidence": _parse_confidence(payload.get("confidence")),
            "reason": payload.get("reasoning") or payload.get("reason") or "",
            "context": context,
            "response_text": text,
        }
        _store_result(signal, result)
        log.info(
            "Perplexity verdict (%s): %s (confidence %.2f) for %s",
            "profitable" if candidate else "reject",
            signal.get("event", "?")[:40],
            result["confidence"],
        )
        return result
    except json.JSONDecodeError as exc:
        log.warning(
            "Perplexity response malformed JSON for '%s': %s",
            signal.get("event", "?")[:40],
            exc,
        )
        return {
            "status": "parse_error",
            "profitable_candidate": False,
            "confidence": 0.0,
            "reason": "Perplexity returned invalid JSON",
            "context": context,
        }
    except Exception as exc:
        log.warning(
            "Perplexity evaluation failed for '%s': %s",
            signal.get("event", "?")[:40],
            exc,
        )
        return {
            "status": "error",
            "profitable_candidate": False,
            "confidence": 0.0,
            "reason": str(exc),
            "context": context,
        }


def annotate_profitable_candidate(signal):
    """Attach Perplexity verdict metadata to the opportunity."""
    existing = signal.get("perplexity")
    if existing and existing.get("status") == "ok":
        return existing

    result = evaluate_signal(signal)
    signal["perplexity"] = result
    signal["profitable_candidate_feature"] = bool(result.get("profitable_candidate"))
    signal["perplexity_status"] = result.get("status")
    signal["profitable_candidate_reason"] = result.get("reason")
    return result
