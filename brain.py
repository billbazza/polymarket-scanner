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
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
OPUS_MODEL = "claude-opus-4-1-20250805"
MODEL_FALLBACKS = (DEFAULT_MODEL,)


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


def _normalise_text_response(text: str) -> str:
    """Strip markdown wrappers so JSON parsing is less brittle."""
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    return text.strip()


def _message_text(client, prompt: str, model: str, max_tokens: int) -> str:
    """Call Anthropic and retry with a known-good fallback model if needed."""
    tried = []
    for candidate in (model, *MODEL_FALLBACKS):
        if candidate in tried:
            continue
        tried.append(candidate)
        try:
            response = client.messages.create(
                model=candidate,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            if candidate != model:
                log.warning("Brain model fallback: %s -> %s", model, candidate)
            return _normalise_text_response(response.content[0].text)
        except Exception as e:
            message = str(e)
            if "not_found_error" in message or "model:" in message:
                log.warning("Brain model unavailable: %s (%s)", candidate, message)
                continue
            raise
    raise RuntimeError(f"No available brain model for requested model {model}")


def estimate_probability(question, price, context="", model=DEFAULT_MODEL):
    """Ask Claude to estimate probability for a single market.

    Returns dict with probability, confidence, reasoning, or None if brain is unavailable.
    """
    client = _get_client()
    if not client:
        return None

    prompt = _build_prompt(question, price, context)

    try:
        text = _message_text(client, prompt, model=model, max_tokens=300)

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


def estimate_batch(signals, model=DEFAULT_MODEL):
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


def validate_signal(signal, model=DEFAULT_MODEL):
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
        text = _message_text(client, prompt, model=model, max_tokens=300)
        result = json.loads(text)
        should_trade = result.get("trade", False)

        log.info("Brain validation: %s → %s (%s)",
                 signal.get("event", "?")[:40],
                 "TRADE" if should_trade else "SKIP",
                 result.get("reasoning", "")[:60])

        return should_trade, result.get("reasoning", "")

    except Exception as e:
        log.error("Brain validation error: %s", e)
        return True, f"Brain error — defaulting to trade: {e}"


def recommend_wallet(address: str, label: str, score_result: dict, model=DEFAULT_MODEL) -> dict | None:
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
        text = _message_text(client, prompt, model=model, max_tokens=250)
        result = json.loads(text)
        result["model"] = model
        log.info("Brain wallet rec: %s → %s (%s)", label, result.get("verdict"), result.get("reasoning", "")[:60])
        return result
    except json.JSONDecodeError as e:
        log.warning("Brain wallet rec returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.error("Brain wallet rec error: %s", e)
        return None


def ask(prompt, model=DEFAULT_MODEL):
    """Ask Claude a generic question and return the text response.

    Useful for free-form analysis (like whale alerts) where we don't
    necessarily need structured JSON for everything.
    """
    client = _get_client()
    if not client:
        return "Brain unavailable"

    try:
        return _message_text(client, prompt, model=model, max_tokens=500)
    except Exception as e:
        log.error("Brain ask error: %s", e)
        return f"Brain error: {e}"


def validate_whale(alert, model=DEFAULT_MODEL):
    """Ask Claude to analyze a whale/insider alert.

    Returns dict with verdict, reasoning, and risk factors.
    """
    client = _get_client()
    if not client:
        return None

    # Try to get real-time context if possible
    research_context = ""
    try:
        import perplexity
        if perplexity.is_available():
            # Create a pseudo-signal for perplexity
            pseudo_signal = {
                "event": alert.get("event", ""),
                "market_a": alert.get("market", ""),
                "market_b": "",
                "action": "WHALE ALERT"
            }
            research = perplexity.research_signal(pseudo_signal)
            research_context = research.get("combined", "")
    except Exception:
        pass

    context_block = f"\nReal-time research context:\n{research_context}\n" if research_context else ""

    current_price = alert.get("current_price")
    current_price_pct = f"{(current_price if current_price is not None else 0.5) * 100:.0f}%"
    volume_24h = alert.get("volume_24h") or 0
    liquidity = alert.get("liquidity") or 0
    volume_ratio = alert.get("volume_ratio") or 0
    biggest_order = alert.get("biggest_order_usd") or 0

    prompt = f"""You are a prediction market integrity analyst. Evaluate this "whale alert" for potential insider trading or significant informed action.

Event: {alert.get('event', '')}
Market: {alert.get('market', '')}
Price: {current_price_pct}
24h Volume: ${volume_24h:,.0f}
Liquidity: ${liquidity:,.0f} (Ratio: {volume_ratio:.1f}x)
Biggest resting order: ${biggest_order:,.0f} ({alert.get('dominant_side', '?')})
Suspicion Score: {alert.get('suspicion_score', 0)}/100
Internal Analysis: {alert.get('analysis', '')}
{context_block}
Assess: Is this likely a sophisticated player with an edge, a simple whale market-making/speculating, or potentially wash trading/noise?
Consider if the volume spike makes sense given current news or if it's "leaking" info.

Respond with ONLY valid JSON:
{{"verdict": "suspicious"|"normal"|"manipulation", "reasoning": "1-2 sentences", "risk_flags": ["list of concerns"], "confidence": "high"|"medium"|"low"}}"""

    try:
        text = _message_text(client, prompt, model=model, max_tokens=300)
        result = json.loads(text)
        result["model"] = model
        log.info("Brain whale validation: %s → %s", alert.get("market", "")[:40], result.get("verdict"))
        return result
    except Exception as e:
        log.error("Brain whale validation error: %s", e)
        return None


def generate_daily_report(context: dict, model=OPUS_MODEL) -> dict | None:
    """Generate a structured daily report for the dashboard."""
    client = _get_client()
    if not client:
        return None

    prompt = f"""You are reviewing a Polymarket scanner/trading system.

Use the structured context below to produce a concise operational report.

Context JSON:
{json.dumps(context, indent=2)}

Respond with ONLY valid JSON in this shape:
{{
  "summary": "2-4 sentences on current system health",
  "working": ["3 to 6 concise bullets"],
  "not_working": ["3 to 6 concise bullets"],
  "improvements": ["exactly 5 concrete improvements ordered by impact"],
  "confidence": "high"|"medium"|"low"
}}"""

    try:
        text = _message_text(client, prompt, model=model, max_tokens=800)
        result = json.loads(text)
        result["model"] = model
        log.info("Brain daily report generated (confidence=%s)", result.get("confidence"))
        return result
    except Exception as e:
        log.error("Brain daily report error: %s", e)
        return None
