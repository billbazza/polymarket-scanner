"""Perplexity AI research — real-time web context for market signals.

Uses Perplexity's sonar model (OpenAI-compatible API) to fetch current
news and developments relevant to a prediction market before Claude validates.

Flow: A+ signal found → research_signal() → context string → brain.validate_signal()

Cost: ~$0.005 per research query (sonar pricing).
Degrades gracefully if PERPLEXITY_API_KEY is not set.
"""
import logging
import os

log = logging.getLogger("scanner.perplexity")

PERPLEXITY_BASE_URL = "https://api.perplexity.ai"
PERPLEXITY_MODEL = "sonar"  # fast + cheap; swap to "sonar-pro" for deeper research


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
