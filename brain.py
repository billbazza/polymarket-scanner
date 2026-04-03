"""AI brain — probability estimation and optional validation for market signals.

Supports staged provider migration:
- Anthropic remains the preferred backend while `ANTHROPIC_API_KEY` works.
- OpenAI can be configured as a warm standby via `OPENAI_API_KEY`.
- `BRAIN_PROVIDER=auto` falls forward to OpenAI when Anthropic is unavailable
  or exhausted, preserving operational continuity.
"""
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("scanner.brain")

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_VERSION = "v1"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"
PROVIDER_XAI = "xai"
PROVIDER_AUTO = "auto"

DEFAULT_MODEL = "default"
OPUS_MODEL = "complex"

DEFAULT_MODEL_ALIASES = {
    DEFAULT_MODEL: {
        PROVIDER_ANTHROPIC: "claude-haiku-4-5-20251001",
        PROVIDER_OPENAI: "gpt-5-mini",
        PROVIDER_XAI: "grok-4.20-beta-latest-non-reasoning",
    },
    OPUS_MODEL: {
        PROVIDER_ANTHROPIC: "claude-opus-4-1-20250805",
        PROVIDER_OPENAI: "gpt-5",
        PROVIDER_XAI: "grok-4.20-beta-latest-reasoning",
    },
}


def _env_value(name: str) -> str:
    """Read an env var once and normalize surrounding whitespace."""
    return (os.environ.get(name) or "").strip()


def _model_aliases() -> dict[str, dict[str, str]]:
    """Resolve model aliases at call time so cutover config is reversible."""
    return {
        DEFAULT_MODEL: {
            PROVIDER_ANTHROPIC: _env_value("BRAIN_ANTHROPIC_MODEL") or DEFAULT_MODEL_ALIASES[DEFAULT_MODEL][PROVIDER_ANTHROPIC],
            PROVIDER_OPENAI: _env_value("BRAIN_OPENAI_MODEL") or DEFAULT_MODEL_ALIASES[DEFAULT_MODEL][PROVIDER_OPENAI],
            PROVIDER_XAI: _env_value("BRAIN_XAI_MODEL") or DEFAULT_MODEL_ALIASES[DEFAULT_MODEL][PROVIDER_XAI],
        },
        OPUS_MODEL: {
            PROVIDER_ANTHROPIC: _env_value("BRAIN_ANTHROPIC_COMPLEX_MODEL") or DEFAULT_MODEL_ALIASES[OPUS_MODEL][PROVIDER_ANTHROPIC],
            PROVIDER_OPENAI: _env_value("BRAIN_OPENAI_COMPLEX_MODEL") or DEFAULT_MODEL_ALIASES[OPUS_MODEL][PROVIDER_OPENAI],
            PROVIDER_XAI: _env_value("BRAIN_XAI_COMPLEX_MODEL") or DEFAULT_MODEL_ALIASES[OPUS_MODEL][PROVIDER_XAI],
        },
    }


def _provider_api_key_name(provider: str) -> str:
    if provider == PROVIDER_ANTHROPIC:
        return "ANTHROPIC_API_KEY"
    if provider == PROVIDER_OPENAI:
        return "OPENAI_API_KEY"
    if provider == PROVIDER_XAI:
        return "XAI_API_KEY"
    raise ValueError(f"Unknown provider {provider}")


def _provider_is_configured(provider: str) -> bool:
    return bool(_env_value(_provider_api_key_name(provider)))


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


def _brain_provider():
    """Configured provider preference."""
    provider = (_env_value("BRAIN_PROVIDER") or PROVIDER_AUTO).lower()
    if provider not in {PROVIDER_AUTO, PROVIDER_ANTHROPIC, PROVIDER_OPENAI, PROVIDER_XAI}:
        log.warning("Unknown BRAIN_PROVIDER=%s — falling back to auto", provider)
        return PROVIDER_AUTO
    return provider


def _available_provider_order():
    """Provider order honoring staged migration and configured keys."""
    provider = _brain_provider()
    has_anthropic = _provider_is_configured(PROVIDER_ANTHROPIC)
    has_openai = _provider_is_configured(PROVIDER_OPENAI)
    has_xai = _provider_is_configured(PROVIDER_XAI)

    if provider == PROVIDER_ANTHROPIC:
        return [PROVIDER_ANTHROPIC] if has_anthropic else []
    if provider == PROVIDER_OPENAI:
        return [PROVIDER_OPENAI] if has_openai else []
    if provider == PROVIDER_XAI:
        return [PROVIDER_XAI] if has_xai else []

    order = []
    if has_anthropic:
        order.append(PROVIDER_ANTHROPIC)
    if has_openai:
        order.append(PROVIDER_OPENAI)
    if has_xai:
        order.append(PROVIDER_XAI)
    return order


def _get_anthropic_client():
    """Get Anthropic client. Returns None if unavailable."""
    api_key = _env_value("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        log.warning("anthropic package not installed — Anthropic brain disabled")
        return None


def _get_openai_client():
    """Get OpenAI client. Returns None if unavailable."""
    api_key = _env_value("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        base_url = _env_value("OPENAI_BASE_URL")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)
    except ImportError:
        log.warning("openai package not installed — OpenAI brain disabled")
        return None

def _get_xai_client():
    """Get xAI client. Returns None if unavailable."""
    api_key = _env_value("XAI_API_KEY")
    if not api_key:
        return None
    try:
        from xai_sdk import Client
        base_url = _env_value("XAI_BASE_URL")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return Client(**kwargs)
    except ImportError:
        log.warning("xai_sdk package not installed — xAI brain disabled")
        return None


def _get_provider_client(provider: str):
    """Get client for a single provider."""
    if provider == PROVIDER_ANTHROPIC:
        return _get_anthropic_client()
    if provider == PROVIDER_OPENAI:
        return _get_openai_client()
    if provider == PROVIDER_XAI:
        return _get_xai_client()
    raise ValueError(f"Unknown provider {provider}")


def get_runtime_status() -> dict:
    """Return provider/runtime status for reversible operator cutover."""
    aliases = _model_aliases()
    configured_order = _available_provider_order()
    client_ready_order = []
    for provider in configured_order:
        if _get_provider_client(provider):
            client_ready_order.append(provider)

    status = {
        "mode": _brain_provider(),
        "configured_order": configured_order,
        "client_ready_order": client_ready_order,
        "fallback_enabled": _brain_provider() == PROVIDER_AUTO and len(configured_order) > 1,
        "brain_enabled": bool(client_ready_order),
        "providers": {
            PROVIDER_ANTHROPIC: {
                "configured": _provider_is_configured(PROVIDER_ANTHROPIC),
                "default_model": aliases[DEFAULT_MODEL][PROVIDER_ANTHROPIC],
                "complex_model": aliases[OPUS_MODEL][PROVIDER_ANTHROPIC],
            },
            PROVIDER_OPENAI: {
                "configured": _provider_is_configured(PROVIDER_OPENAI),
                "default_model": aliases[DEFAULT_MODEL][PROVIDER_OPENAI],
                "complex_model": aliases[OPUS_MODEL][PROVIDER_OPENAI],
                "base_url": _env_value("OPENAI_BASE_URL") or None,
            },
            PROVIDER_XAI: {
                "configured": _provider_is_configured(PROVIDER_XAI),
                "default_model": aliases[DEFAULT_MODEL][PROVIDER_XAI],
                "complex_model": aliases[OPUS_MODEL][PROVIDER_XAI],
                "base_url": _env_value("XAI_BASE_URL") or None,
            },
        },
    }
    return status


def _get_client_candidates():
    """Get configured brain clients in preference order."""
    clients = []
    for provider in _available_provider_order():
        client = _get_provider_client(provider)
        if client:
            clients.append({"provider": provider, "client": client})
    if not clients:
        status = get_runtime_status()
        if status["mode"] != PROVIDER_AUTO and not status["providers"][status["mode"]]["configured"]:
            log.warning("Brain provider %s selected but API key is missing — brain disabled", status["mode"])
        elif status["configured_order"]:
            log.warning("Brain provider clients unavailable for configured providers %s — brain disabled",
                        ",".join(status["configured_order"]))
        else:
            log.warning("No brain provider configured — brain disabled")
    return clients


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


def _resolve_model_candidates(provider: str, requested_model: str | None) -> list[str]:
    """Resolve provider-specific models for an alias or explicit name."""
    aliases = _model_aliases()
    default_model = aliases[DEFAULT_MODEL][provider]
    if not requested_model:
        requested_model = DEFAULT_MODEL

    if requested_model in aliases:
        model = aliases[requested_model][provider]
        return [model]

    if requested_model == default_model:
        return [requested_model]

    return [requested_model, default_model]


def _anthropic_should_fallback(exc: Exception) -> bool:
    message = str(exc).lower()
    fallback_markers = (
        "credit",
        "quota",
        "billing",
        "not_found_error",
        "rate limit",
        "429",
        "401",
        "authentication",
        "model:",
    )
    return any(marker in message for marker in fallback_markers)


def _openai_should_fallback(exc: Exception) -> bool:
    message = str(exc).lower()
    fallback_markers = (
        "credit",
        "quota",
        "billing",
        "rate limit",
        "429",
        "401",
        "authentication",
        "model",
    )
    return any(marker in message for marker in fallback_markers)


def _extract_openai_text(response) -> str:
    """Normalize OpenAI response text across client variants."""
    output_text = getattr(response, "output_text", None)
    if output_text:
        return _normalise_text_response(output_text)

    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return _normalise_text_response(content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(text)
        return _normalise_text_response("\n".join(parts))
    return _normalise_text_response(str(content))


def _anthropic_message_text(client, prompt: str, model: str, max_tokens: int) -> tuple[str, str]:
    """Call Anthropic and retry with a known-good provider-local fallback model if needed."""
    tried = []
    for candidate in _resolve_model_candidates(PROVIDER_ANTHROPIC, model):
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
            return _normalise_text_response(response.content[0].text), candidate
        except Exception as e:
            if _anthropic_should_fallback(e):
                log.warning("Brain model unavailable: %s (%s)", candidate, e)
                continue
            raise
    raise RuntimeError(f"No available brain model for requested model {model}")


def _openai_message_text(client, prompt: str, model: str, max_tokens: int) -> tuple[str, str]:
    """Call OpenAI using the installed client surface."""
    tried = []
    for candidate in _resolve_model_candidates(PROVIDER_OPENAI, model):
        if candidate in tried:
            continue
        tried.append(candidate)
        try:
            if hasattr(client, "responses"):
                response = client.responses.create(
                    model=candidate,
                    input=prompt,
                    max_output_tokens=max_tokens,
                )
            else:
                try:
                    response = client.chat.completions.create(
                        model=candidate,
                        messages=[{"role": "user", "content": prompt}],
                        max_completion_tokens=max_tokens,
                    )
                except TypeError:
                    response = client.chat.completions.create(
                        model=candidate,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                    )
            if candidate != model:
                log.warning("Brain model fallback: %s -> %s", model, candidate)
            return _extract_openai_text(response), candidate
        except Exception as e:
            if _openai_should_fallback(e):
                log.warning("Brain model unavailable: %s (%s)", candidate, e)
                continue
            raise
    raise RuntimeError(f"No available brain model for requested model {model}")


def _xai_should_fallback(exc: Exception) -> bool:
    message = str(exc).lower()
    fallback_markers = (
        "credit",
        "quota",
        "billing",
        "rate limit",
        "timeout",
        "429",
        "401",
        "authentication",
        "model",
    )
    return any(marker in message for marker in fallback_markers)


def _extract_xai_text(response) -> str:
    """Normalize xAI response text across client surfaces."""
    text = getattr(response, "output_text", None)
    if text:
        return _normalise_text_response(text)

    output = getattr(response, "output", None) or []
    for item in output:
        if getattr(item, "type", "") == "message":
            for chunk in getattr(item, "content", []) or []:
                if getattr(chunk, "type", "") == "output_text":
                    candidate = getattr(chunk, "text", "")
                    if candidate:
                        return _normalise_text_response(candidate)
            for chunk in getattr(item, "content", []) or []:
                text_value = getattr(chunk, "text", None)
                if text_value:
                    return _normalise_text_response(text_value)

    return _normalise_text_response(str(response))


def _xai_message_text(client, prompt: str, model: str, max_tokens: int) -> tuple[str, str]:
    """Call Grok via the xai_sdk client."""
    tried = []
    for candidate in _resolve_model_candidates(PROVIDER_XAI, model):
        if candidate in tried:
            continue
        tried.append(candidate)
        try:
            response = client.responses.create(
                model=candidate,
                input=[{"role": "user", "content": prompt}],
                max_output_tokens=max_tokens,
            )
            if candidate != model:
                log.warning("Brain model fallback: %s -> %s", model, candidate)
            return _extract_xai_text(response), candidate
        except Exception as e:
            if _xai_should_fallback(e):
                log.warning("Brain model unavailable: %s (%s)", candidate, e)
                continue
            raise
    raise RuntimeError(f"No available brain model for requested model {model}")


def _brain_request(prompt: str, model: str, max_tokens: int) -> dict | None:
    """Execute a brain request across configured providers."""
    candidates = _get_client_candidates()
    if not candidates:
        return None

    last_error = None
    for candidate in candidates:
        provider = candidate["provider"]
        client = candidate["client"]
        try:
            if provider == PROVIDER_ANTHROPIC:
                text, used_model = _anthropic_message_text(client, prompt, model=model, max_tokens=max_tokens)
            elif provider == PROVIDER_OPENAI:
                text, used_model = _openai_message_text(client, prompt, model=model, max_tokens=max_tokens)
            else:
                text, used_model = _xai_message_text(client, prompt, model=model, max_tokens=max_tokens)
            return {
                "provider": provider,
                "model": used_model,
                "text": text,
            }
        except Exception as e:
            last_error = e
            should_fallback = (
                _anthropic_should_fallback(e) if provider == PROVIDER_ANTHROPIC
                else _openai_should_fallback(e) if provider == PROVIDER_OPENAI
                else _xai_should_fallback(e)
            )
            if should_fallback:
                log.warning("Brain provider fallback: %s failed (%s)", provider, e)
                continue
            raise

    if last_error:
        raise last_error
    return None


def estimate_probability(question, price, context="", model=DEFAULT_MODEL):
    """Ask the configured AI provider to estimate probability for a single market.

    Returns dict with probability, confidence, reasoning, or None if brain is unavailable.
    """
    prompt = _build_prompt(question, price, context)

    try:
        result = _brain_request(prompt, model=model, max_tokens=300)
        if not result:
            return None

        payload = json.loads(result["text"])
        payload["model"] = result["model"]
        payload["provider"] = result["provider"]
        payload["prompt_version"] = PROMPT_VERSION

        log.info("Brain estimate (%s): %s → %.1f%% (confidence=%s, edge=%+.1f%%)",
                 result["provider"], question[:40], payload["probability"] * 100,
                 payload["confidence"], payload.get("edge_vs_market", 0) * 100)

        return payload

    except json.JSONDecodeError as e:
        log.warning("Brain returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.error("Brain API error: %s", e)
        return None


def estimate_batch(signals, model=DEFAULT_MODEL):
    """Estimate probabilities for a batch of signals.

    Processes each signal's market_a and market_b through the configured AI provider.
    Returns list of signals enriched with brain estimates.

    Cost depends on the active provider/model configuration.
    """
    if not _get_client_candidates():
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

        # Check if the AI provider sees edge on either side
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
    then asks the configured AI provider to validate with current news in hand.

    Returns True/False with reasoning. Used as final gate before paper trading.
    """
    if not _get_client_candidates():
        return True, "Brain unavailable — defaulting to statistical signal"

    # Fetch real-time context from Perplexity if available
    context_block = ""
    perplexity_result = signal.get("perplexity")
    try:
        import perplexity
        if perplexity.is_available():
            needs_eval = not perplexity_result or perplexity_result.get("status") != "ok"
            if needs_eval:
                perplexity_result = perplexity.evaluate_signal(signal)
                signal["perplexity"] = perplexity_result
            context = perplexity_result.get("context", "") if perplexity_result else ""
            if context:
                context_block = f"\nReal-time research context:\n{context}\n"
                log.info("Perplexity context fetched for: %s", signal.get("event", "?")[:40])
            if perplexity_result and perplexity_result.get("status") != "ok":
                log.info(
                    "Perplexity fallback (%s) for %s: %s",
                    perplexity_result.get("status"),
                    signal.get("event", "?")[:40],
                    perplexity_result.get("reason"),
                )
    except Exception as e:
        log.warning("Perplexity research failed, continuing without context: %s", e)

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
        response = _brain_request(prompt, model=model, max_tokens=300)
        if not response:
            return True, "Brain unavailable — defaulting to statistical signal"
        result = json.loads(response["text"])
        result["provider"] = response["provider"]
        result["model"] = response["model"]
        should_trade = result.get("trade", False)

        log.info("Brain validation (%s): %s → %s (%s)",
                 result["provider"],
                 signal.get("event", "?")[:40],
                 "TRADE" if should_trade else "SKIP",
                 result.get("reasoning", "")[:60])

        return should_trade, result.get("reasoning", "")

    except Exception as e:
        log.error("Brain validation error: %s", e)
        return True, f"Brain error — defaulting to trade: {e}"


def recommend_wallet(address: str, label: str, score_result: dict, model=DEFAULT_MODEL) -> dict | None:
    """Ask the configured AI provider whether this wallet is worth copy-trading.

    Returns dict with verdict/reasoning/risk_flags, or None if brain unavailable.
    """
    if not _get_client_candidates():
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
        response = _brain_request(prompt, model=model, max_tokens=250)
        if not response:
            return None
        result = json.loads(response["text"])
        result["model"] = response["model"]
        result["provider"] = response["provider"]
        log.info("Brain wallet rec (%s): %s → %s (%s)",
                 result["provider"], label, result.get("verdict"), result.get("reasoning", "")[:60])
        return result
    except json.JSONDecodeError as e:
        log.warning("Brain wallet rec returned invalid JSON: %s", e)
        return None
    except Exception as e:
        log.error("Brain wallet rec error: %s", e)
        return None


def ask(prompt, model=DEFAULT_MODEL):
    """Ask the configured AI provider a generic question and return the text response.

    Useful for free-form analysis (like whale alerts) where we don't
    necessarily need structured JSON for everything.
    """
    if not _get_client_candidates():
        return "Brain unavailable"

    try:
        response = _brain_request(prompt, model=model, max_tokens=500)
        return response["text"] if response else "Brain unavailable"
    except Exception as e:
        log.error("Brain ask error: %s", e)
        return f"Brain error: {e}"


def validate_whale(alert, model=DEFAULT_MODEL):
    """Ask the configured AI provider to analyze a whale/insider alert.

    Returns dict with verdict, reasoning, and risk factors.
    """
    if not _get_client_candidates():
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
        response = _brain_request(prompt, model=model, max_tokens=300)
        if not response:
            return None
        result = json.loads(response["text"])
        result["model"] = response["model"]
        result["provider"] = response["provider"]
        log.info("Brain whale validation (%s): %s → %s",
                 result["provider"], alert.get("market", "")[:40], result.get("verdict"))
        return result
    except Exception as e:
        log.error("Brain whale validation error: %s", e)
        return None


def generate_daily_report(context: dict, model=OPUS_MODEL) -> dict | None:
    """Generate a structured daily report for the dashboard."""
    if not _get_client_candidates():
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
        response = _brain_request(prompt, model=model, max_tokens=800)
        if not response:
            return None
        result = json.loads(response["text"])
        result["model"] = response["model"]
        result["provider"] = response["provider"]
        log.info("Brain daily report generated via %s (confidence=%s)",
                 result["provider"], result.get("confidence"))
        return result
    except Exception as e:
        log.error("Brain daily report error: %s", e)
        return None
