"""Claude AI brain — probability estimation for market signals.

Uses Haiku for cost efficiency (~$0.003 per signal batch).
Processes signals in batches to minimize API calls.
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("scanner.brain")

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "v1"


def _load_prompt():
    """Load the current prompt template."""
    path = PROMPTS_DIR / f"{PROMPT_VERSION}_probability.txt"
    return path.read_text()


def _build_prompt(question, price, context=""):
    """Fill in prompt template for a single market."""
    template = _load_prompt()
    return (template
            .replace("{{question}}", question)
            .replace("{{price}}", f"{price:.1%}")
            .replace("{{context}}", context or "No additional context"))


def _get_client():
    """Get Anthropic client. Returns None if no API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("No ANTHROPIC_API_KEY set — brain disabled")
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        log.warning("anthropic package not installed — run: pip install anthropic")
        return None


def estimate_probability(question, price, context="", model="claude-haiku-4-5-20251001"):
    """Ask Claude to estimate probability for a single market.

    Returns dict with probability, confidence, reasoning, or None if brain is unavailable.
    """
    client = _get_client()
    if not client:
        return None

    prompt = _build_prompt(question, price, context)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        # Parse JSON response
        result = json.loads(text)
        result["model"] = model
        result["prompt_version"] = PROMPT_VERSION

        log.info("Brain estimate: %s → %.1f%% (confidence=%s, edge=%+.1f%%)",
                 question[:40], result["probability"] * 100,
                 result["confidence"], result.get("edge_vs_market", 0) * 100)

        return result

    except json.JSONDecodeError as e:
        log.warning("Brain returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.error("Brain API error: %s", e)
        return None


def estimate_batch(signals, model="claude-haiku-4-5-20251001"):
    """Estimate probabilities for a batch of signals.

    Processes each signal's market_a and market_b through Claude.
    Returns list of signals enriched with brain estimates.

    Cost: ~$0.001 per market question (Haiku pricing).
    A batch of 10 signals = 20 questions ≈ $0.02.
    """
    client = _get_client()
    if not client:
        log.info("Brain unavailable — returning signals without AI estimates")
        return signals

    enriched = []
    for signal in signals:
        event_context = f"Event: {signal.get('event', '')}. " \
                        f"Liquidity: ${signal.get('liquidity', 0):,.0f}. " \
                        f"24h volume: ${signal.get('volume_24h', 0):,.0f}."

        # Estimate for market A
        est_a = estimate_probability(
            signal.get("market_a", ""),
            signal.get("price_a", 0.5),
            context=event_context,
            model=model,
        )

        # Estimate for market B
        est_b = estimate_probability(
            signal.get("market_b", ""),
            signal.get("price_b", 0.5),
            context=event_context,
            model=model,
        )

        signal["brain"] = {
            "market_a": est_a,
            "market_b": est_b,
            "has_edge": False,
        }

        # Check if Claude sees edge on either side
        if est_a and est_b:
            edge_a = abs(est_a.get("edge_vs_market", 0))
            edge_b = abs(est_b.get("edge_vs_market", 0))
            signal["brain"]["has_edge"] = edge_a > 0.05 or edge_b > 0.05
            signal["brain"]["max_edge"] = max(edge_a, edge_b)

        enriched.append(signal)

    edges = sum(1 for s in enriched if s["brain"].get("has_edge"))
    log.info("Brain batch: %d signals, %d with edge (>5%%)", len(enriched), edges)

    return enriched


def validate_signal(signal, model="claude-haiku-4-5-20251001"):
    """Quick validation — should we trade this signal?

    Fetches real-time Perplexity research context first (if available),
    then asks Claude to validate with current news in hand.

    Returns True/False with reasoning. Used as final gate before paper trading.
    """
    client = _get_client()
    if not client:
        return True, "Brain unavailable — defaulting to statistical signal"

    # Fetch real-time context from Perplexity if available
    research_context = ""
    try:
        import perplexity
        if perplexity.is_available():
            research = perplexity.research_signal(signal)
            research_context = research.get("combined", "")
            if research_context:
                log.info("Perplexity context fetched for: %s", signal.get("event", "?")[:40])
    except Exception as e:
        log.warning("Perplexity research failed, continuing without context: %s", e)

    context_block = f"\nReal-time research context:\n{research_context}\n" if research_context else ""

    prompt = f"""You are a prediction market risk analyst. A statistical scanner found this trading signal:

Event: {signal.get('event', '')}
Signal: {signal.get('action', '')}
Z-Score: {signal.get('z_score', 0):+.2f} (spread deviation from mean)
Cointegration p-value: {signal.get('coint_pvalue', 1):.4f}
Half-life: {signal.get('half_life', 999):.1f} periods
Market A: {signal.get('market_a', '')} @ {signal.get('price_a', 0):.1%}
Market B: {signal.get('market_b', '')} @ {signal.get('price_b', 0):.1%}
{context_block}
Should this signal be traded? Consider:
1. Could there be a FUNDAMENTAL reason these markets diverged (not just noise)?
2. Is the event likely to resolve soon, making mean-reversion impossible?
3. Are there external factors (news, regulation) that break the statistical relationship?

Respond with ONLY valid JSON:
{{"trade": true/false, "reasoning": "1-2 sentences", "risk_flags": ["list of concerns"]}}"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        result = json.loads(response.content[0].text.strip())
        should_trade = result.get("trade", False)

        log.info("Brain validation: %s → %s (%s)",
                 signal.get("event", "?")[:40],
                 "TRADE" if should_trade else "SKIP",
                 result.get("reasoning", "")[:60])

        return should_trade, result.get("reasoning", "")

    except Exception as e:
        log.error("Brain validation error: %s", e)
        return True, f"Brain error — defaulting to trade: {e}"


def recommend_wallet(address: str, label: str, score_result: dict, model="claude-haiku-4-5-20251001") -> dict | None:
    """Ask Claude whether this wallet is worth copy-trading.

    Returns dict with verdict/reasoning/risk_flags, or None if brain unavailable.
    """
    client = _get_client()
    if not client:
        return None

    b = score_result.get("breakdown") or {}
    score = score_result.get("score", 0)
    classification = score_result.get("classification", "unknown")

    components = b.get("components") or {}
    comp_str = "  ".join(f"{k}={v:.0f}" for k, v in components.items()) if components else "n/a"

    prompt = f"""You are a prediction market copy-trading analyst. Evaluate whether to copy-trade this Polymarket wallet.

Wallet: {label} ({address[:16]}...)
Score: {score:.1f}/100  Classification: {classification.upper()}
Trade count: {b.get('trade_count', '?')}  Avg size: ${b.get('avg_size_usd', 0):,.0f}  Total volume: ${b.get('total_volume_usd', 0):,.0f}
Categories traded: {b.get('n_categories', '?')} categories, top={b.get('top_category', '?')} ({b.get('top_cat_pct', 0):.0f}%)
Trades/month: {b.get('trades_per_month', 0):.0f}  Sell ratio: {b.get('sell_ratio', 0):.2f}
Unrealised P&L: ${b.get('unrealised_pnl', 0):,.0f}  Realised P&L: ${b.get('realised_pnl', 0):,.0f}
Score components: {comp_str}

Assess: Does this wallet show genuine information edge, or is this luck/bot activity?
Consider category specialisation, position sizing conviction, hold behaviour, and P&L quality.

Respond with ONLY valid JSON:
{{"verdict": "copy"|"caution"|"skip", "reasoning": "1-2 sentences", "risk_flags": ["list of concerns"], "confidence": "high"|"medium"|"low"}}"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
        result["model"] = model
        log.info("Brain wallet rec: %s → %s (%s)", label, result.get("verdict"), result.get("reasoning", "")[:60])
        return result
    except json.JSONDecodeError as e:
        log.warning("Brain wallet rec returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.error("Brain wallet rec error: %s", e)
        return None
