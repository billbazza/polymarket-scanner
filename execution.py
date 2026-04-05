from __future__ import annotations

"""Order execution engine — paper and live trading for Polymarket pairs.

Paper mode (default): simulates orders against current midpoint prices.
Live mode: uses py-clob-client for real orders on Polymarket (requires POLYMARKET_PRIVATE_KEY).
"""
import logging
import time

import api
import db
import math_engine
import runtime_config
import weather_guard_state

log = logging.getLogger("scanner.execution")

MAX_SLIPPAGE_PCT  = 2.5
PAPER_BALANCE_USD = 2_000.0
WHALE_MAX_SLIPPAGE_PCT = 2.0

# Execution mode: "maker" (GTC limit orders, 0% fee) or "taker" (market orders, 2% fee).
# Default is maker — post inside the spread, pay no fees, capture better prices.
EXECUTION_MODE = runtime_config.get("EXECUTION_MODE", "maker")

# How far inside the spread we post our limit (fraction of half-spread).
# 0.5 = halfway between mid and best bid/ask.
MAKER_AGGRESSION = 0.5

# GTC orders expire and are cancelled after this many hours if unfilled.
ORDER_TTL_HOURS = 4


def _stage2_enabled():
    """Return True when Stage 2 polygon gating instrumentation is active."""
    return runtime_config.get_bool("STAGE2_POLYGON_GATING", default=False)


def _fetch_stage2_rollout():
    """Pull the latest Polygon rollout snapshot."""
    try:
        import blockchain
    except ImportError as exc:
        log.warning("Stage 2 polygon rollout disabled: %s", exc)
        return {"ok": False, "error": "blockchain module unavailable"}
    try:
        return blockchain.capture_polygon_rollout()
    except Exception as exc:
        log.warning("Stage 2 polygon rollout failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _runtime_scope_for_mode(mode: str | None, runtime_scope: str | None = None) -> str:
    if runtime_scope is not None:
        return db.normalize_runtime_scope(runtime_scope)
    if mode == "live":
        return db.RUNTIME_SCOPE_PENNY
    return db.RUNTIME_SCOPE_PAPER

def _get_mode():
    """Determine trading mode from environment."""
    key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
    if key:
        return "live"
    return "paper"


def _extract_confidence_decision(signal: dict | None) -> dict | None:
    """Return the applied paper-sizing decision, if any."""
    if not isinstance(signal, dict):
        return None
    decision = signal.get("paper_sizing")
    if not isinstance(decision, dict):
        return None
    return decision


def _apply_confidence_sizing(signal: dict | None, size_usd: float) -> tuple[float, dict | None]:
    """Override size_usd with confidence-recommended sizing when active and expose gating."""
    decision = _extract_confidence_decision(signal)
    if not decision:
        return size_usd, None
    size_before_confidence = round(float(size_usd or 0.0), 2)
    selected_size = round(float(decision.get("selected_size_usd") or size_before_confidence), 2)
    activation_status = decision.get("activation_status") or {}
    meta = {
        "confidence_score": round(float(decision.get("confidence_score") or 0.0), 4),
        "confidence_policy": decision.get("selected_policy"),
        "confidence_requested_policy": decision.get("active_policy"),
        "confidence_baseline_size_usd": size_before_confidence,
        "confidence_selected_size_usd": selected_size,
        "confidence_inputs": decision.get("confidence_inputs"),
        "confidence_components": decision.get("confidence_components"),
        "confidence_constraints": decision.get("constraints"),
        "confidence_applied": bool(decision.get("applied")),
        "activation_status": activation_status,
        "rollout_state": decision.get("rollout_state"),
        "compare_only": bool(decision.get("compare_only")),
        "gate_blocker_codes": activation_status.get("blocker_codes"),
        "gate_blockers": activation_status.get("blockers"),
        "gate_can_apply_confidence": activation_status.get("can_apply_confidence"),
    }
    if not decision.get("applied") or selected_size <= 0:
        return size_usd, meta
    return selected_size, meta


def _cap_quarter_kelly(size_usd: float, balance_usd: float) -> tuple[float, bool]:
    """Cap trade size to 25% of available balance (quarter Kelly)."""
    try:
        balance = float(balance_usd or 0.0)
    except (TypeError, ValueError):
        balance = 0.0
    cap = round(balance * 0.25, 2)
    if cap <= 0 or size_usd <= cap:
        return size_usd, False
    return cap, True


def check_balance(mode=None, runtime_scope: str | None = None):
    """Check available USDC balance.

    Paper mode: returns simulated balance.
    Live mode: queries on-chain USDC.e balance via blockchain module.

    Returns:
        dict with balance_usd, mode, and any error info.
    """
    mode = mode or _get_mode()
    runtime_scope = _runtime_scope_for_mode(mode, runtime_scope)

    if mode == "paper":
        account = db.get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
        log.debug(
            "Paper balance check: available=$%.2f committed=$%.2f equity=$%.2f",
            account["available_cash"],
            account["committed_capital"],
            account["total_equity"],
        )
        return {
            "ok": True,
            "balance_usd": account["available_cash"],
            "mode": "paper",
            "runtime_scope": runtime_scope,
            "paper_account": account,
        }

    # Live mode — use blockchain module
    try:
        import blockchain
        snapshot_getter = getattr(blockchain, "get_verified_wallet_snapshot", None)
        if callable(snapshot_getter):
            snapshot = snapshot_getter(max_block_age_seconds=db.LIVE_LEDGER_MAX_AGE_SECONDS)
            if not snapshot.get("verified"):
                return {
                    "ok": False,
                    "balance_usd": 0,
                    "mode": "live",
                    "runtime_scope": runtime_scope,
                    "error": snapshot.get("verification_error") or snapshot.get("wallet_error") or "Live Polygon wallet verification failed",
                    "wallet_snapshot": snapshot,
                }
            balance = float(snapshot.get("available_balance_usd") or 0.0)
            wallet = snapshot.get("wallet_address")
            if wallet:
                log.info("Live balance for %s: $%.2f", wallet[:10] + "...", balance)
            return {
                "ok": True,
                "balance_usd": balance,
                "mode": "live",
                "runtime_scope": runtime_scope,
                "wallet_snapshot": snapshot,
            }
        wallet = blockchain.get_wallet_address()
        if not wallet:
            return {"ok": False, "balance_usd": 0, "mode": "live",
                    "error": "No wallet address available"}
        balance = blockchain.get_usdc_balance(wallet)
        log.info("Live balance for %s: $%.2f", wallet[:10] + "...", balance)
        return {"ok": True, "balance_usd": balance, "mode": "live", "runtime_scope": runtime_scope}
    except Exception as e:
        log.error("Failed to check live balance: %s", e)
        return {"ok": False, "balance_usd": 0, "mode": "live", "runtime_scope": runtime_scope,
                "error": str(e)}


def _get_maker_prices(token_a, token_b, side_a, side_b):
    """Return limit prices inside the spread for both legs.

    For a BUY we post at bid + aggression × half_spread (we improve on the bid).
    For a SELL we post at ask - aggression × half_spread (we improve on the ask).
    Falls back to midpoint if the order book is unavailable.
    """
    def _limit_price(token, side):
        try:
            book = api.get_book(token)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids or not asks:
                return api.get_midpoint(token)
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            half_spread = (best_ask - best_bid) / 2
            if side == "BUY":
                return round(best_bid + MAKER_AGGRESSION * half_spread, 4)
            else:
                return round(best_ask - MAKER_AGGRESSION * half_spread, 4)
        except Exception:
            return api.get_midpoint(token)

    return _limit_price(token_a, side_a), _limit_price(token_b, side_b)


def _get_single_leg_price(token_id, side, exec_mode=None):
    """Return a price for a single-leg order using the shared maker/taker policy."""
    exec_mode = exec_mode or EXECUTION_MODE
    if exec_mode != "maker":
        return api.get_midpoint(token_id)
    try:
        book = api.get_book(token_id)
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return api.get_midpoint(token_id)
        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
        half_spread = (best_ask - best_bid) / 2
        if side == "BUY":
            return round(best_bid + MAKER_AGGRESSION * half_spread, 4)
        return round(best_ask - MAKER_AGGRESSION * half_spread, 4)
    except Exception:
        return api.get_midpoint(token_id)


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _order_response_dict(result, fallback_order_id=None):
    if isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {"order_id": str(result) if result is not None else None}
    payload.setdefault("order_id", fallback_order_id)
    return payload


def _extract_order_id(order_payload):
    for key in ("order_id", "id", "orderID"):
        value = order_payload.get(key)
        if value:
            return str(value)
    return None


def _extract_tx_hash(order_payload):
    for key in ("tx_hash", "transaction_hash", "txHash"):
        value = order_payload.get(key)
        if value:
            return str(value)
    return None


def _estimate_leg_fee_usd(exec_mode: str, notional_usd: float) -> float:
    fee_rate = 0.02 if exec_mode == "taker" else 0.0
    return round(float(notional_usd or 0.0) * fee_rate, 2)


def _failure_result(mode: str, reason_code: str, error: str, **extra) -> dict:
    runtime_scope = extra.pop("runtime_scope", None)
    blocker_source = extra.pop("blocker_source", None)
    payload = {
        "ok": False,
        "mode": mode,
        "reason_code": reason_code,
        "error": error,
    }
    if runtime_scope is not None:
        payload["runtime_scope"] = runtime_scope
        payload["blocker_runtime_scope"] = runtime_scope
    if blocker_source is not None:
        payload["blocker_source"] = blocker_source
    payload.update(extra)
    return payload


def execute_trade(signal, size_usd, mode=None, runtime_scope: str | None = None):
    """Execute a pairs trade from a signal.

    Args:
        signal: dict from db.get_signals() — must have market_a, market_b,
                price_a, price_b, z_score, event, etc.
        size_usd: total position size in USD (split across both legs).
        mode: "paper" or "live". Defaults to auto-detect from env.

    Returns:
        dict with trade result, fill prices, trade_id, or error.
    """
    mode = mode or _get_mode()
    runtime_scope = _runtime_scope_for_mode(mode, runtime_scope)
    signal = signal or {}
    signal = dict(signal)
    size_usd = round(float(size_usd or 0.0), 2)
    size_before_confidence = size_usd
    size_usd, confidence_meta = _apply_confidence_sizing(signal, size_usd)
    if confidence_meta and abs(size_usd - size_before_confidence) >= 0.01:
        log.info(
            "  Confidence sizing override: requested $%.2f → $%.2f (score %.4f, policy=%s)",
            size_before_confidence,
            size_usd,
            confidence_meta["confidence_score"],
            confidence_meta["confidence_policy"],
        )
    log.info(
        "Executing trade: %s | size=$%.2f mode=%s runtime_scope=%s z=%.2f",
        signal.get("event", "?")[:50],
        size_usd,
        mode,
        runtime_scope,
        signal.get("z_score", 0),
    )

    stage2_context = None
    if _stage2_enabled() and mode == "paper":
        rollout_snapshot = _fetch_stage2_rollout()
        stage2_context = {"polygon_rollout": rollout_snapshot}
        block_info = rollout_snapshot.get("block") if rollout_snapshot else {}
        log.info(
            "Stage 2 polygon rollout: block #%s chain=%s parity=%s error=%s",
            block_info.get("block_number"),
            rollout_snapshot.get("chain_id"),
            rollout_snapshot.get("chain_parity_ok"),
            rollout_snapshot.get("block_error") or rollout_snapshot.get("chain_error"),
        )

    def _wrap_result(payload):
        if stage2_context is not None:
            payload["stage2_context"] = stage2_context
        return payload

    # 1. Balance pre-check
    bal = check_balance(mode, runtime_scope=runtime_scope)
    if not bal["ok"]:
        log.warning("Balance check failed: %s", bal.get("error"))
        return _wrap_result(_failure_result(
            mode,
            "balance_check_failed",
            f"Balance check failed: {bal.get('error')}",
            runtime_scope=runtime_scope,
            blocker_source="shared-external" if mode == "live" else f"{runtime_scope}-execution",
            balance=bal,
        ))

    quarter_kelly_capped = False
    size_before_cap = size_usd
    size_usd, quarter_kelly_capped = _cap_quarter_kelly(size_usd, bal["balance_usd"])
    if quarter_kelly_capped:
        log.warning(
            "  Size $%.2f exceeds 25%% of balance $%.2f; capping to $%.2f",
            size_before_cap,
            bal["balance_usd"],
            size_usd,
        )
    if bal["balance_usd"] < size_usd:
        log.warning("Insufficient balance: $%.2f < $%.2f", bal["balance_usd"], size_usd)
        return _wrap_result(_failure_result(
            mode,
            "insufficient_balance",
            f"Insufficient balance: ${bal['balance_usd']:.2f} < ${size_usd:.2f}",
            runtime_scope=runtime_scope,
            blocker_source=f"{runtime_scope}-execution",
            balance=bal,
            requested_size_usd=size_usd,
        ))

    # 2. Determine sides and fetch entry prices
    token_a = signal.get("token_id_a") or signal["market_a"]
    token_b = signal.get("token_id_b") or signal["market_b"]
    z = signal.get("z_score", 0)
    side_a = "BUY"  if z < 0 else "SELL"
    side_b = "SELL" if z < 0 else "BUY"

    exec_mode = EXECUTION_MODE  # "maker" or "taker"

    if exec_mode == "maker":
        try:
            price_a, price_b = _get_maker_prices(token_a, token_b, side_a, side_b)
        except Exception as e:
            log.error("Failed to compute maker prices: %s", e)
            return _wrap_result(_failure_result(
                mode,
                "maker_price_fetch_failed",
                f"Maker price fetch failed: {e}",
                runtime_scope=runtime_scope,
                blocker_source="shared-external",
            ))
    else:
        try:
            price_a = api.get_midpoint(token_a)
            price_b = api.get_midpoint(token_b)
        except Exception as e:
            log.error("Failed to fetch current prices: %s", e)
            return _wrap_result(_failure_result(
                mode,
                "price_fetch_failed",
                f"Price fetch failed: {e}",
                runtime_scope=runtime_scope,
                blocker_source="shared-external",
            ))

    if price_a <= 0 or price_b <= 0:
        log.warning("Invalid prices: a=%.4f b=%.4f", price_a, price_b)
        return _wrap_result(_failure_result(
            mode,
            "invalid_prices",
            f"Invalid prices: a={price_a} b={price_b}",
            runtime_scope=runtime_scope,
            blocker_source="shared-external",
            price_a=price_a,
            price_b=price_b,
        ))

    half_size = size_usd / 2

    def _check(token_id):
        return math_engine.check_slippage(
            token_id, trade_size_usd=half_size, max_slippage_pct=MAX_SLIPPAGE_PCT,
        )

    slippage_leg_a = None
    slippage_leg_b = None

    if mode == "live" and exec_mode == "taker":
        slippage_leg_a = _check(token_a)
        if not slippage_leg_a["ok"]:
            log.warning("Slippage check failed for leg A: %s", slippage_leg_a.get("reason"))
            return _wrap_result(_failure_result(
                mode,
                "slippage_block",
                f"Slippage too high: {slippage_leg_a.get('reason')}",
                runtime_scope=runtime_scope,
                blocker_source="shared-external",
                slippage=slippage_leg_a,
                slippage_leg="a",
            ))
        slippage_leg_b = _check(token_b)
        if not slippage_leg_b["ok"]:
            log.warning("Slippage check failed for leg B: %s", slippage_leg_b.get("reason"))
            return _wrap_result(_failure_result(
                mode,
                "slippage_block",
                f"Slippage too high on leg B: {slippage_leg_b.get('reason')}",
                runtime_scope=runtime_scope,
                blocker_source="shared-external",
                slippage=slippage_leg_b,
                slippage_leg="b",
            ))
    elif mode == "paper":
        slippage_leg_a = _check(token_a)
        log.info(
            "Paper slippage (informational, %s): leg A=%.2f%%",
            exec_mode,
            slippage_leg_a.get("slippage_pct") or 0,
        )
        if stage2_context is not None:
            slippage_leg_b = _check(token_b)
            log.debug(
                "Stage 2 polygon slippage leg B: %.2f%%",
                slippage_leg_b.get("slippage_pct") or 0,
            )

    if stage2_context is not None:
        stage2_context["liquidity_gate"] = {
            "token_a": token_a,
            "token_b": token_b,
            "slippage_a": slippage_leg_a,
            "slippage_b": slippage_leg_b,
            "max_slippage_pct": MAX_SLIPPAGE_PCT,
            "trade_size_usd": size_usd,
        }

    # 4. HMRC gate — block live trades if GBP audit logging is unavailable
    if mode == "live":
        try:
            import hmrc
            gbp_rate = hmrc.require_gbp_rate()
        except RuntimeError as e:
            log.error("LIVE TRADE BLOCKED — HMRC compliance failure: %s", e)
            return _wrap_result(_failure_result(mode, "hmrc_gate_blocked", str(e), runtime_scope=runtime_scope, blocker_source=f"{runtime_scope}-execution"))

    # 5. Execute based on mode and execution style
    if mode == "paper":
        result = _execute_paper(
            signal,
            size_usd,
            price_a,
            price_b,
            side_a=side_a,
            side_b=side_b,
            exec_mode=exec_mode,
            confidence_metadata=confidence_meta,
            quarter_kelly_capped=quarter_kelly_capped,
            runtime_scope=runtime_scope,
        )
    else:
        result = _execute_live(
            signal,
            size_usd,
            price_a,
            price_b,
            side_a=side_a,
            side_b=side_b,
            exec_mode=exec_mode,
            confidence_metadata=confidence_meta,
            quarter_kelly_capped=quarter_kelly_capped,
            runtime_scope=runtime_scope,
        )

    # 6. Stamp and audit-log real trades
    if mode == "live" and result.get("ok"):
        try:
            import hmrc
            result["gbp_rate"] = gbp_rate
            result["size_gbp"] = round(size_usd * gbp_rate, 2)
            hmrc.log_real_trade({**signal, **result, "size_usd": size_usd}, action="opened")
        except Exception as e:
            log.error("HMRC audit log failed (trade executed but not logged): %s", e)

    return _wrap_result(result)


def _revalidate_weather_horizon(signal: dict | None):
    """Re-check how many hours remain before the weather resolution."""
    if not signal:
        return {
            "ok": False,
            "reason_code": "horizon_unknown",
            "reason": "Signal data unavailable for horizon check.",
        }
    hours_ahead = signal.get("hours_ahead")
    timestamp = signal.get("timestamp")
    strategy = (
        (signal.get("strategy_name") or signal.get("market_family") or "")
        .strip()
        .lower()
    )
    if strategy.startswith("weather_exact_temp"):
        return {"ok": True, "remaining_hours": hours_ahead if hours_ahead is not None else 0.0}
    if hours_ahead is None or timestamp is None:
        return {
            "ok": False,
            "reason_code": "horizon_unknown",
            "reason": "Signal missing horizon metadata.",
        }
    try:
        age_hours = (time.time() - float(timestamp)) / 3600
    except (TypeError, ValueError):
        age_hours = 0.0
    remaining_hours = hours_ahead - age_hours
    guard = weather_guard_state.current_guard()
    min_hours_required = guard["min_hours_ahead"]
    if remaining_hours < min_hours_required:
        return {
            "ok": False,
            "reason_code": "horizon_too_short",
            "reason": (
                f"Signal horizon now {remaining_hours:.1f}h, below required "
                f"{min_hours_required}h minimum."
            ),
            "remaining_hours": remaining_hours,
        }
    return {"ok": True, "remaining_hours": remaining_hours}


def execute_weather_trade(signal, size_usd, mode=None, runtime_scope: str | None = None):
    """Execute a weather trade from a saved weather signal."""
    mode = mode or _get_mode()
    runtime_scope = _runtime_scope_for_mode(mode, runtime_scope)
    signal = dict(signal or {})
    weather_signal_id = signal.get("id") or signal.get("weather_signal_id")
    if not weather_signal_id:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "signal_not_found",
            "error": "Weather signal missing id.",
        }

    stored_signal = db.get_weather_signal_by_id(weather_signal_id) or {}
    signal = {**stored_signal, **signal}
    strategy_name = (
        signal.get("strategy_name")
        or signal.get("market_family")
        or "weather"
    )
    size_usd = round(float(size_usd or 0.0), 2)
    size_before_confidence = size_usd
    size_usd, confidence_meta = _apply_confidence_sizing(signal, size_usd)
    if confidence_meta and abs(size_usd - size_before_confidence) >= 0.01:
        log.info(
            "  Weather confidence sizing override: requested $%.2f → $%.2f (score %.4f, policy=%s)",
            size_before_confidence,
            size_usd,
            confidence_meta["confidence_score"],
            confidence_meta["confidence_policy"],
        )
    log.info(
        "Executing weather trade: signal=%s strategy=%s size=$%.2f mode=%s runtime_scope=%s",
        weather_signal_id,
        strategy_name,
        size_usd,
        mode,
        runtime_scope,
    )

    if strategy_name.startswith("weather_exact_temp") and mode != "paper":
        log.warning(
            "Weather exact-temp live execution blocked for signal %s strategy=%s",
            weather_signal_id,
            strategy_name,
        )
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "exact_temp_paper_only",
            "error": "Exact-temperature weather execution remains paper-only.",
            "strategy_name": strategy_name,
            "weather_signal_id": weather_signal_id,
            "runtime_scope": runtime_scope,
            "blocker_source": f"{runtime_scope}-weather",
        }

    horizon_check = _revalidate_weather_horizon(signal)
    if not horizon_check["ok"]:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": horizon_check.get("reason_code"),
            "error": horizon_check.get("reason"),
            "weather_signal_id": weather_signal_id,
            "remaining_hours": horizon_check.get("remaining_hours"),
        }

    bal = check_balance(mode, runtime_scope=runtime_scope)
    if not bal["ok"]:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "balance_check_failed",
            "error": f"Balance check failed: {bal.get('error')}",
            "weather_signal_id": weather_signal_id,
            "runtime_scope": runtime_scope,
            "blocker_source": "shared-external" if mode == "live" else f"{runtime_scope}-weather",
        }
    quarter_kelly_capped = False
    size_before_cap = size_usd
    size_usd, quarter_kelly_capped = _cap_quarter_kelly(size_usd, bal["balance_usd"])
    if quarter_kelly_capped:
        log.warning(
            "  Weather size $%.2f exceeds 25%% of balance $%.2f; capping to $%.2f",
            size_before_cap,
            bal["balance_usd"],
            size_usd,
        )
    if bal["balance_usd"] < size_usd:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "insufficient_cash",
            "error": f"Insufficient balance: ${bal['balance_usd']:.2f} < ${size_usd:.2f}",
            "paper_account": bal.get("paper_account"),
            "weather_signal_id": weather_signal_id,
            "runtime_scope": runtime_scope,
            "blocker_source": f"{runtime_scope}-weather",
        }

    decision = db.inspect_weather_trade_open(
        weather_signal_id,
        size_usd=size_usd,
        mode=mode,
        runtime_scope=runtime_scope,
    )
    if not decision["ok"]:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": decision.get("reason_code"),
            "error": decision.get("reason"),
            "decision": decision,
            "weather_signal_id": weather_signal_id,
            "strategy_name": strategy_name,
            "runtime_scope": runtime_scope,
            "blocker_source": decision.get("blocker_source"),
            "blocker_runtime_scope": decision.get("blocker_runtime_scope"),
        }

    if mode == "paper":
        trade_id = db.open_weather_trade(weather_signal_id, size_usd=size_usd, mode=mode, runtime_scope=runtime_scope)
        if not trade_id:
            return {
                "ok": False,
                "mode": "paper",
                "reason_code": "open_failed",
                "error": "Weather trade could not be opened after preflight passed.",
                "weather_signal_id": weather_signal_id,
                "strategy_name": strategy_name,
            }

        account = db.get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
        return {
            "ok": True,
            "mode": "paper",
            "trade_id": trade_id,
            "weather_signal_id": weather_signal_id,
            "signal_id": weather_signal_id,
            "strategy_name": strategy_name,
            "entry_price": decision.get("entry_price"),
            "action": decision.get("action"),
            "runtime_scope": runtime_scope,
            "trade_state_mode": db.TRADE_STATE_PAPER,
            "reconciliation_mode": db.RECONCILIATION_INTERNAL,
            "paper_account": account,
            "confidence_score": confidence_meta.get("confidence_score") if confidence_meta else None,
            "confidence_policy": confidence_meta.get("confidence_policy") if confidence_meta else None,
            "confidence_applied": bool(confidence_meta),
            "confidence_selected_size_usd": confidence_meta.get("confidence_selected_size_usd") if confidence_meta else None,
            "confidence_baseline_size_usd": confidence_meta.get("confidence_baseline_size_usd") if confidence_meta else None,
            "confidence_requested_policy": confidence_meta.get("confidence_requested_policy") if confidence_meta else None,
            "confidence_gate_blockers": confidence_meta.get("gate_blockers") if confidence_meta else None,
            "confidence_gate_blocker_codes": confidence_meta.get("gate_blocker_codes") if confidence_meta else None,
            "confidence_gate_can_apply": confidence_meta.get("gate_can_apply_confidence") if confidence_meta else None,
            "confidence_compare_only": confidence_meta.get("compare_only") if confidence_meta else None,
            "confidence_rollout_state": confidence_meta.get("rollout_state") if confidence_meta else None,
            "quarter_kelly_capped": bool(quarter_kelly_capped),
        }

    entry_token = decision.get("entry_token")
    if not entry_token:
        return {
            "ok": False,
            "mode": "live",
            "reason_code": "token_missing",
            "error": "Weather signal lacks an entry token.",
            "weather_signal_id": weather_signal_id,
            "strategy_name": strategy_name,
            "runtime_scope": runtime_scope,
            "blocker_source": f"{runtime_scope}-weather",
        }

    slippage = math_engine.check_slippage(
        entry_token,
        trade_size_usd=size_usd,
        max_slippage_pct=2.0,
    )
    if not slippage.get("ok"):
        return {
            "ok": False,
            "mode": "live",
            "reason_code": "slippage_block",
            "error": slippage.get("reason"),
            "weather_signal_id": weather_signal_id,
            "strategy_name": strategy_name,
            "slippage": slippage,
            "runtime_scope": runtime_scope,
            "blocker_source": "shared-external",
        }

    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        return {"ok": False, "error": "py-clob-client not installed", "mode": "live"}

    private_key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        return {"ok": False, "error": "POLYMARKET_PRIVATE_KEY not set", "mode": "live"}

    try:
        client = ClobClient(host="https://clob.polymarket.com", key=private_key, chain_id=137)
        side = "BUY"
        exec_mode = EXECUTION_MODE
        price = _get_single_leg_price(entry_token, side, exec_mode=exec_mode)
        shares = round(size_usd / max(price, 0.0001), 4)
        raw_order = client.create_and_post_order({
            "tokenID": entry_token,
            "price": price,
            "size": shares,
            "side": side,
            "type": "GTC",
        })
        order_payload = _order_response_dict(raw_order)
        order_id = _extract_order_id(order_payload) or str(raw_order)

        wallet_address = None
        try:
            import blockchain
            wallet_address = blockchain.get_wallet_address()
        except Exception:
            wallet_address = None

        live_identity = db.build_live_trade_identity(order_id, None, wallet=wallet_address)
        trade_status = "pending_fill" if exec_mode == "maker" else "open"
        estimated_fee = _estimate_leg_fee_usd(exec_mode, size_usd)
        entry_execution = {
            "mode": "live",
            "exec_mode": exec_mode,
            "order_type": "GTC",
            "status": trade_status,
            "wallet_address": wallet_address,
            "requested_prices": {"a": price},
            "orders": {
                "a": {
                    "order_id": order_id,
                    "tx_hash": _extract_tx_hash(order_payload),
                    "token_id": entry_token,
                    "side": side,
                    "price": price,
                    "size_shares": shares,
                    "size_usd": round(size_usd, 2),
                    "response": order_payload,
                },
            },
            "estimated_fee_usd": estimated_fee,
        }
        trade_id = db.open_weather_trade(
            weather_signal_id,
            size_usd=size_usd,
            mode=mode,
            runtime_scope=runtime_scope,
            metadata={
                "status": trade_status,
                "strategy_name": strategy_name,
                "trade_state_mode": db.TRADE_STATE_LIVE,
                "reconciliation_mode": db.RECONCILIATION_ORDERS,
                "runtime_scope": runtime_scope,
                "entry_execution": entry_execution,
                "entry_fee_usd": estimated_fee,
                **live_identity,
            },
        )
        if not trade_id:
            return {
                "ok": False,
                "mode": "live",
                "reason_code": "open_failed",
                "error": "Weather trade could not be opened after live preflight passed.",
                "weather_signal_id": weather_signal_id,
                "strategy_name": strategy_name,
            }

        now = time.time()
        expires = now + ORDER_TTL_HOURS * 3600
        db.save_open_order({
            "order_id": order_id,
            "trade_id": trade_id,
            "signal_id": None,
            "token_id": entry_token,
            "side": side,
            "leg": "a",
            "limit_price": price,
            "size_shares": shares,
            "size_usd": size_usd,
            "status": "pending",
            "mode": "live",
            "placed_at": now,
            "expires_at": expires,
            "purpose": "open",
            "tx_hash": _extract_tx_hash(order_payload),
            "response": order_payload,
        })
        try:
            import hmrc
            hmrc.log_real_trade({
                **signal,
                "trade_id": trade_id,
                "weather_signal_id": weather_signal_id,
                "size_usd": size_usd,
                "entry_execution": entry_execution,
                "fee_total_usd": estimated_fee,
                "runtime_scope": runtime_scope,
            }, action="opened")
        except Exception as exc:
            log.error("HMRC audit log failed during live weather open: %s", exc)

        return {
            "ok": True,
            "mode": "live",
            "trade_id": trade_id,
            "weather_signal_id": weather_signal_id,
            "signal_id": weather_signal_id,
            "strategy_name": strategy_name,
            "entry_price": price,
            "action": decision.get("action"),
            "runtime_scope": runtime_scope,
            "trade_state_mode": db.TRADE_STATE_LIVE,
            "reconciliation_mode": db.RECONCILIATION_ORDERS,
            "fees_usd": estimated_fee,
            "entry_execution": entry_execution,
            "pending": exec_mode == "maker",
            "slippage": slippage,
            "confidence_score": confidence_meta.get("confidence_score") if confidence_meta else None,
            "confidence_policy": confidence_meta.get("confidence_policy") if confidence_meta else None,
            "confidence_applied": bool(confidence_meta),
            "confidence_selected_size_usd": confidence_meta.get("confidence_selected_size_usd") if confidence_meta else None,
            "confidence_baseline_size_usd": confidence_meta.get("confidence_baseline_size_usd") if confidence_meta else None,
            "confidence_requested_policy": confidence_meta.get("confidence_requested_policy") if confidence_meta else None,
            "confidence_gate_blockers": confidence_meta.get("gate_blockers") if confidence_meta else None,
            "confidence_gate_blocker_codes": confidence_meta.get("gate_blocker_codes") if confidence_meta else None,
            "confidence_gate_can_apply": confidence_meta.get("gate_can_apply_confidence") if confidence_meta else None,
            "confidence_compare_only": confidence_meta.get("compare_only") if confidence_meta else None,
            "confidence_rollout_state": confidence_meta.get("rollout_state") if confidence_meta else None,
            "quarter_kelly_capped": bool(quarter_kelly_capped),
        }
    except Exception as exc:
        log.error("Live weather execution failed for signal %s: %s", weather_signal_id, exc)
        return {
            "ok": False,
            "mode": "live",
            "reason_code": "live_execution_failed",
            "error": str(exc),
            "weather_signal_id": weather_signal_id,
            "strategy_name": strategy_name,
        }


def execute_whale_trade(alert, size_usd=20, mode=None):
    """Open a guarded whale trade (paper/live)."""
    mode = mode or _get_mode()
    runtime_scope = _runtime_scope_for_mode(mode)
    alert = alert or {}
    alert_id = alert.get("id")
    size_usd = round(float(size_usd or 0.0), 2)

    if size_usd <= 0:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "invalid_size",
            "error": "Trade size must be greater than zero.",
            "alert_id": alert_id,
            "size_usd": size_usd,
        }

    token_id = api.normalize_token_id(alert.get("token_id"))
    if not token_id:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "token_missing",
            "error": "Alert lacks a valid token id for order book evaluation.",
            "alert_id": alert_id,
            "size_usd": size_usd,
        }

    balance_info = check_balance(mode=mode)
    if not balance_info.get("ok"):
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "balance_error",
            "error": f"Balance check failed: {balance_info.get(error)}",
            "alert_id": alert_id,
            "size_usd": size_usd,
            "balance": balance_info,
        }

    size_before_cap = size_usd
    size_usd, quarter_kelly_capped = _cap_quarter_kelly(size_usd, balance_info["balance_usd"])
    if size_usd <= 0:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "insufficient_balance",
            "error": "Post-Kelly size would be zero; not enough capital.",
            "alert_id": alert_id,
            "size_usd": size_before_cap,
            "balance": balance_info,
        }

    if balance_info["balance_usd"] < size_usd:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "insufficient_balance",
            "error": "Not enough balance after cap.",
            "alert_id": alert_id,
            "size_usd": size_usd,
            "balance": balance_info,
        }

    slippage = math_engine.check_slippage(
        token_id,
        trade_size_usd=size_usd,
        max_slippage_pct=WHALE_MAX_SLIPPAGE_PCT,
    )
    if not slippage.get("ok"):
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "slippage_block",
            "error": slippage.get("reason"),
            "alert_id": alert_id,
            "size_usd": size_usd,
            "slippage": slippage,
            "balance": balance_info,
        }

    decision = db.inspect_whale_trade_open(
        alert_id,
        size_usd=size_usd,
        mode=mode,
        runtime_scope=runtime_scope,
    )
    if not decision.get("ok"):
        return {
            "ok": False,
            "mode": mode,
            "reason_code": decision.get("reason_code"),
            "error": decision.get("reason"),
            "alert_id": alert_id,
            "size_usd": size_usd,
            "token_id": token_id,
            "slippage": slippage,
            "balance": balance_info,
            "decision": decision,
        }

    current_price = alert.get("current_price")
    try:
        if current_price is not None:
            current_price = float(current_price)
    except (TypeError, ValueError):
        current_price = None
    current_price = current_price if current_price is not None else 0.5

    dominant_side = (alert.get("dominant_side") or "").upper()
    if dominant_side == "BID":
        action = "BUY_YES"
        entry_price = current_price
    elif dominant_side == "ASK":
        action = "BUY_NO"
        entry_price = 1.0 - current_price
    else:
        action = "BUY_YES"
        entry_price = current_price

    if entry_price is None or not (0 <= entry_price <= 1):
        entry_price = 0.5

    note_parts = []
    analysis = alert.get("analysis")
    if analysis:
        note_parts.append(analysis)
    suspicion = alert.get("suspicion_score")
    if suspicion is not None:
        note_parts.append(f"Suspicion: {float(suspicion):.0f}/100")
    volume_ratio = alert.get("volume_ratio")
    if volume_ratio is not None:
        note_parts.append(f"Vol ratio: {volume_ratio}x")
    notes = " | ".join(note_parts) if note_parts else None

    trade_data = {
        "trade_type": "whale",
        "opened_at": time.time(),
        "side_a": action,
        "side_b": "",
        "entry_price_a": entry_price,
        "entry_price_b": 0,
        "token_id_a": token_id,
        "size_usd": size_usd,
        "status": "open",
        "whale_alert_id": alert_id,
        "event": alert.get("event"),
        "market_a": alert.get("market"),
        "analysis": analysis,
        "suspicion_score": alert.get("suspicion_score"),
        "notes": notes or f"Suspicion: {suspicion or 0}/100",
        "strategy_name": alert.get("strategy_name") or "whale",
        "trade_state_mode": db.TRADE_STATE_PAPER,
        "reconciliation_mode": db.RECONCILIATION_INTERNAL,
        "runtime_scope": runtime_scope,
    }

    trade_id = db.open_whale_trade(trade_data)
    if not trade_id:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "open_failed",
            "error": "Database insert failed for whale trade.",
            "alert_id": alert_id,
            "size_usd": size_usd,
            "balance": balance_info,
            "slippage": slippage,
            "decision": decision,
        }

    log.info(
        "Whale trade %s created for alert %s (%s) size=$%.2f entry=%.3f",
        trade_id,
        alert_id,
        action,
        size_usd,
        entry_price,
    )

    return {
        "ok": True,
        "mode": mode,
        "trade_id": trade_id,
        "alert_id": alert_id,
        "size_usd": size_usd,
        "token_id": token_id,
        "slippage": slippage,
        "quarter_kelly_capped": bool(quarter_kelly_capped),
        "balance": balance_info,
        "decision": decision,
    }


def _execute_paper(signal, size_usd, price_a, price_b,
                   side_a="BUY", side_b="SELL", exec_mode="maker",
                   confidence_metadata=None, quarter_kelly_capped=False,
                   runtime_scope: str = db.RUNTIME_SCOPE_PAPER):
    """Simulate order fill.

    Maker mode: records limit prices (better than mid) with 0% fee — optimistic
    but correct for benchmarking maker strategy vs taker.
    Taker mode: fills at midpoint with fee already baked into EV model.
    """
    signal_id = signal.get("id")
    if not signal_id:
        log.error("Signal missing 'id' field, cannot record trade")
        return _failure_result(
            "paper",
            "signal_id_missing",
            "Signal missing id",
            runtime_scope=runtime_scope,
            blocker_source=f"{runtime_scope}-execution",
        )

    trade_id = db.open_trade(
        signal_id,
        size_usd=size_usd,
        metadata={
            "strategy_name": "cointegration",
            "trade_state_mode": db.TRADE_STATE_PAPER,
            "reconciliation_mode": db.RECONCILIATION_INTERNAL,
            "runtime_scope": runtime_scope,
            "entry_grade_label": signal.get("grade_label"),
            "admission_path": signal.get("admission_path"),
            "experiment_name": signal.get("experiment_name"),
            "experiment_status": signal.get("experiment_status"),
            "entry_z_score": signal.get("z_score"),
            "entry_half_life": signal.get("half_life"),
            "entry_liquidity": signal.get("liquidity"),
            "ev": signal.get("ev"),
            "slippage": signal.get("trial_slippage"),
            "guardrails": signal.get("experiment_guardrails"),
        },
    )
    if not trade_id:
        log.error("Failed to open trade in DB for signal %s", signal_id)
        return _failure_result(
            "paper",
            "db_open_trade_failed",
            "DB open_trade failed",
            runtime_scope=runtime_scope,
            blocker_source=f"{runtime_scope}-cointegration",
        )
    account = db.get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)

    log.info("PAPER %s FILL: trade=%d | A(%s)=%.4f B(%s)=%.4f | $%.2f | bal=$%.2f",
             exec_mode.upper(), trade_id, side_a, price_a, side_b, price_b,
             size_usd, account["available_cash"])

    return {
        "ok": True,
        "mode": "paper",
        "exec_mode": exec_mode,
        "trade_id": trade_id,
        "signal_id": signal_id,
        "fill_price_a": price_a,
        "fill_price_b": price_b,
        "size_usd": size_usd,
        "remaining_balance": account["available_cash"],
        "runtime_scope": runtime_scope,
        "paper_account": account,
        "confidence_score": confidence_metadata.get("confidence_score") if confidence_metadata else None,
        "confidence_policy": confidence_metadata.get("confidence_policy") if confidence_metadata else None,
        "confidence_applied": bool(confidence_metadata),
        "confidence_selected_size_usd": confidence_metadata.get("confidence_selected_size_usd") if confidence_metadata else None,
        "confidence_baseline_size_usd": confidence_metadata.get("confidence_baseline_size_usd") if confidence_metadata else None,
        "confidence_requested_policy": confidence_metadata.get("confidence_requested_policy") if confidence_metadata else None,
        "confidence_gate_blockers": confidence_metadata.get("gate_blockers") if confidence_metadata else None,
        "confidence_gate_blocker_codes": confidence_metadata.get("gate_blocker_codes") if confidence_metadata else None,
        "confidence_gate_can_apply": confidence_metadata.get("gate_can_apply_confidence") if confidence_metadata else None,
        "confidence_compare_only": confidence_metadata.get("compare_only") if confidence_metadata else None,
        "confidence_rollout_state": confidence_metadata.get("rollout_state") if confidence_metadata else None,
        "quarter_kelly_capped": bool(quarter_kelly_capped),
    }


def settle_paper_trade(trade_id, pnl_usd):
    """Retained for compatibility. Paper accounting is now derived from SQLite."""
    trade = db.get_trade(trade_id) or {}
    runtime_scope = trade.get("runtime_scope") or db.RUNTIME_SCOPE_PAPER
    account = db.get_paper_account_state(refresh_unrealized=False, runtime_scope=runtime_scope)
    log.info(
        "PAPER CLOSE: trade=%d | pnl=$%.2f | available=$%.2f equity=$%.2f",
        trade_id,
        pnl_usd or 0,
        account["available_cash"],
        account["total_equity"],
    )
    return True


def close_live_trade(trade: dict, notes: str = "") -> dict:
    """Best-effort live close using offsetting GTC orders and persisted execution metadata."""
    trade = dict(trade or {})
    trade_id = trade.get("id")
    if not trade_id:
        return {"ok": False, "error": "Trade missing id.", "mode": "live"}

    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        return {"ok": False, "error": "py-clob-client not installed", "mode": "live"}

    private_key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        return {"ok": False, "error": "POLYMARKET_PRIVATE_KEY not set", "mode": "live"}

    try:
        client = ClobClient(host="https://clob.polymarket.com", key=private_key, chain_id=137)
        trade_type = trade.get("trade_type") or "pairs"
        close_time = time.time()
        exit_execution = {
            "mode": "live",
            "requested_at": close_time,
            "status": "submitted",
            "orders": {},
        }

        if trade_type in {"weather", "copy", "whale"}:
            token_a = trade.get("token_id_a")
            if not token_a:
                return {"ok": False, "error": "Trade is missing token_id_a.", "mode": "live"}
            current_a = api.get_midpoint(token_a)
            close_side_a = "SELL" if "BUY" in str(trade.get("side_a") or "").upper() else "BUY"
            shares_a = round((_safe_float(trade.get("size_usd")) / max(_safe_float(trade.get("entry_price_a")), 0.0001)), 4)
            raw_order_a = client.create_and_post_order({
                "tokenID": token_a,
                "price": current_a,
                "size": shares_a,
                "side": close_side_a,
                "type": "GTC",
            })
            order_a = _order_response_dict(raw_order_a)
            exit_execution["orders"]["a"] = {
                "order_id": _extract_order_id(order_a) or str(raw_order_a),
                "tx_hash": _extract_tx_hash(order_a),
                "token_id": token_a,
                "side": close_side_a,
                "price": current_a,
                "size_shares": shares_a,
                "response": order_a,
            }
            exit_fee_usd = _estimate_leg_fee_usd(EXECUTION_MODE, trade.get("size_usd"))
            pnl = db.close_trade(
                trade_id,
                current_a,
                notes=notes or "Operator live close",
                metadata={
                    "exit_execution": exit_execution,
                    "exit_fee_usd": exit_fee_usd,
                },
            )
            if pnl is not None:
                try:
                    import hmrc
                    hmrc.log_real_trade({**trade, "pnl": pnl, "exit_execution": exit_execution, "fee_total_usd": trade.get("fee_total_usd", 0) + exit_fee_usd}, action="closed")
                except Exception as exc:
                    log.error("HMRC audit log failed during live close: %s", exc)
            return {
                "ok": pnl is not None,
                "mode": "live",
                "trade_id": trade_id,
                "pnl": round(float(pnl or 0.0), 2),
                "exit_execution": exit_execution,
                "fees_usd": exit_fee_usd,
            }

        token_a = trade.get("token_id_a")
        token_b = trade.get("token_id_b")
        current_a = api.get_midpoint(token_a)
        current_b = api.get_midpoint(token_b)
        close_side_a = "BUY" if (trade.get("side_a") or "").upper() == "SELL" else "SELL"
        close_side_b = "BUY" if (trade.get("side_b") or "").upper() == "SELL" else "SELL"
        half_size = _safe_float(trade.get("size_usd")) / 2
        shares_a = round(half_size / max(_safe_float(trade.get("entry_price_a")), 0.0001), 4)
        shares_b = round(half_size / max(_safe_float(trade.get("entry_price_b")), 0.0001), 4)
        raw_order_a = client.create_and_post_order({
            "tokenID": token_a,
            "price": current_a,
            "size": shares_a,
            "side": close_side_a,
            "type": "GTC",
        })
        raw_order_b = client.create_and_post_order({
            "tokenID": token_b,
            "price": current_b,
            "size": shares_b,
            "side": close_side_b,
            "type": "GTC",
        })
        order_a = _order_response_dict(raw_order_a)
        order_b = _order_response_dict(raw_order_b)
        exit_execution["orders"] = {
            "a": {
                "order_id": _extract_order_id(order_a) or str(raw_order_a),
                "tx_hash": _extract_tx_hash(order_a),
                "token_id": token_a,
                "side": close_side_a,
                "price": current_a,
                "size_shares": shares_a,
                "response": order_a,
            },
            "b": {
                "order_id": _extract_order_id(order_b) or str(raw_order_b),
                "tx_hash": _extract_tx_hash(order_b),
                "token_id": token_b,
                "side": close_side_b,
                "price": current_b,
                "size_shares": shares_b,
                "response": order_b,
            },
        }
        exit_fee_usd = round(
            _estimate_leg_fee_usd(EXECUTION_MODE, half_size) + _estimate_leg_fee_usd(EXECUTION_MODE, half_size),
            2,
        )
        pnl = db.close_trade(
            trade_id,
            current_a,
            current_b,
            notes=notes or "Operator live close",
            metadata={
                "exit_execution": exit_execution,
                "exit_fee_usd": exit_fee_usd,
            },
        )
        if pnl is not None:
            try:
                import hmrc
                hmrc.log_real_trade({**trade, "pnl": pnl, "exit_execution": exit_execution, "fee_total_usd": trade.get("fee_total_usd", 0) + exit_fee_usd}, action="closed")
            except Exception as exc:
                log.error("HMRC audit log failed during live close: %s", exc)
        return {
            "ok": pnl is not None,
            "mode": "live",
            "trade_id": trade_id,
            "pnl": round(float(pnl or 0.0), 2),
            "exit_execution": exit_execution,
            "fees_usd": exit_fee_usd,
        }
    except Exception as exc:
        log.error("Live close failed for trade %s: %s", trade_id, exc)
        return {"ok": False, "error": str(exc), "mode": "live", "trade_id": trade_id}


def _execute_live(signal, size_usd, price_a, price_b,
                  side_a="BUY", side_b="SELL", exec_mode="maker",
                  confidence_metadata=None, quarter_kelly_capped=False,
                  runtime_scope: str = db.RUNTIME_SCOPE_PENNY):
    """Execute real orders via py-clob-client.

    Maker mode: GTC limit orders posted inside spread — fills when someone
    crosses our price. Pending until filled or expired.
    Taker mode: market-style orders that fill immediately at ask/bid.
    """
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        log.error("py-clob-client not installed. Run: pip install py-clob-client")
        return _failure_result(
            "live",
            "clob_client_missing",
            "py-clob-client not installed",
            runtime_scope=runtime_scope,
            blocker_source=f"{runtime_scope}-execution",
        )

    private_key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        log.error("POLYMARKET_PRIVATE_KEY not set")
        return _failure_result(
            "live",
            "private_key_missing",
            "POLYMARKET_PRIVATE_KEY not set",
            runtime_scope=runtime_scope,
            blocker_source=f"{runtime_scope}-execution",
        )

    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )

        half_size = size_usd / 2
        token_a = signal.get("token_id_a") or signal["market_a"]
        token_b = signal.get("token_id_b") or signal["market_b"]
        order_type = "GTC"

        raw_order_a = client.create_and_post_order({
            "tokenID": token_a,
            "price":   price_a,
            "size":    round(half_size / price_a, 4) if price_a > 0 else 0,
            "side":    side_a,
            "type":    order_type,
        })
        raw_order_b = client.create_and_post_order({
            "tokenID": token_b,
            "price":   price_b,
            "size":    round(half_size / price_b, 4) if price_b > 0 else 0,
            "side":    side_b,
            "type":    order_type,
        })
        order_a = _order_response_dict(raw_order_a)
        order_b = _order_response_dict(raw_order_b)
        order_id_a = _extract_order_id(order_a) or str(raw_order_a)
        order_id_b = _extract_order_id(order_b) or str(raw_order_b)

        signal_id = signal.get("id")
        wallet_address = None
        try:
            import blockchain
            wallet_address = blockchain.get_wallet_address()
        except Exception:
            wallet_address = None
        live_identity = db.build_live_trade_identity(order_id_a, order_id_b, wallet=wallet_address)
        # In maker mode the trade is pending until both legs fill
        trade_status = "pending_fill" if exec_mode == "maker" else "open"
        estimated_fee = round(
            _estimate_leg_fee_usd(exec_mode, half_size) + _estimate_leg_fee_usd(exec_mode, half_size),
            2,
        )
        entry_execution = {
            "mode": "live",
            "exec_mode": exec_mode,
            "order_type": order_type,
            "status": trade_status,
            "wallet_address": wallet_address,
            "requested_prices": {"a": price_a, "b": price_b},
            "orders": {
                "a": {
                    "order_id": order_id_a,
                    "tx_hash": _extract_tx_hash(order_a),
                    "token_id": token_a,
                    "side": side_a,
                    "price": price_a,
                    "size_shares": round(half_size / price_a, 4) if price_a > 0 else 0,
                    "size_usd": round(half_size, 2),
                    "response": order_a,
                },
                "b": {
                    "order_id": order_id_b,
                    "tx_hash": _extract_tx_hash(order_b),
                    "token_id": token_b,
                    "side": side_b,
                    "price": price_b,
                    "size_shares": round(half_size / price_b, 4) if price_b > 0 else 0,
                    "size_usd": round(half_size, 2),
                    "response": order_b,
                },
            },
            "estimated_fee_usd": estimated_fee,
        }
        trade_id = db.open_trade(
            signal_id,
            size_usd=size_usd,
            metadata={
                "strategy_name": "cointegration_live",
                "trade_state_mode": db.TRADE_STATE_LIVE,
                "reconciliation_mode": db.RECONCILIATION_ORDERS,
                "runtime_scope": runtime_scope,
                "entry_execution": entry_execution,
                "entry_fee_usd": estimated_fee,
                **live_identity,
            },
        ) if signal_id else None

        now = time.time()
        expires = now + ORDER_TTL_HOURS * 3600
        for leg, token_id, side, price, order_id, order_payload in [
            ("a", token_a, side_a, price_a, order_id_a, order_a),
            ("b", token_b, side_b, price_b, order_id_b, order_b),
        ]:
            db.save_open_order({
                "order_id":    order_id,
                "trade_id":    trade_id,
                "signal_id":   signal_id,
                "token_id":    token_id,
                "side":        side,
                "leg":         leg,
                "limit_price": price,
                "size_shares": round(half_size / price, 4) if price > 0 else 0,
                "size_usd":    half_size,
                "status":      "pending",
                "mode":        "live",
                "placed_at":   now,
                "expires_at":  expires,
                "purpose":     "open",
                "tx_hash":     _extract_tx_hash(order_payload),
                "response":    order_payload,
            })

        log.info("LIVE %s: trade=%s | orders=%s,%s | size=$%.2f",
                 exec_mode.upper(), trade_id, order_id_a, order_id_b, size_usd)

        return {
            "ok": True,
            "mode": "live",
            "exec_mode": exec_mode,
            "trade_id": trade_id,
            "canonical_ref": live_identity["canonical_ref"],
            "order_a": order_id_a,
            "order_b": order_id_b,
            "tx_hash_a": _extract_tx_hash(order_a),
            "tx_hash_b": _extract_tx_hash(order_b),
            "fill_price_a": price_a,
            "fill_price_b": price_b,
            "size_usd": size_usd,
            "fees_usd": estimated_fee,
            "entry_execution": entry_execution,
            "pending": exec_mode == "maker",
            "runtime_scope": runtime_scope,
            "confidence_score": confidence_metadata.get("confidence_score") if confidence_metadata else None,
            "confidence_policy": confidence_metadata.get("confidence_policy") if confidence_metadata else None,
            "confidence_applied": bool(confidence_metadata),
            "confidence_selected_size_usd": confidence_metadata.get("confidence_selected_size_usd") if confidence_metadata else None,
            "confidence_baseline_size_usd": confidence_metadata.get("confidence_baseline_size_usd") if confidence_metadata else None,
            "confidence_requested_policy": confidence_metadata.get("confidence_requested_policy") if confidence_metadata else None,
            "confidence_gate_blockers": confidence_metadata.get("gate_blockers") if confidence_metadata else None,
            "confidence_gate_blocker_codes": confidence_metadata.get("gate_blocker_codes") if confidence_metadata else None,
            "confidence_gate_can_apply": confidence_metadata.get("gate_can_apply_confidence") if confidence_metadata else None,
            "confidence_compare_only": confidence_metadata.get("compare_only") if confidence_metadata else None,
            "confidence_rollout_state": confidence_metadata.get("rollout_state") if confidence_metadata else None,
            "quarter_kelly_capped": bool(quarter_kelly_capped),
        }

    except Exception as e:
        log.error("Live execution failed: %s", e)
        return _failure_result(
            "live",
            "exchange_order_failed",
            str(e),
            runtime_scope=runtime_scope,
            blocker_source="shared-external",
        )


def place_gtc_order(token_id, side, price, size_shares, mode=None):
    """Place a Good-Till-Cancelled limit order.

    GTC orders sit in the book until filled or cancelled.

    Args:
        token_id: Polymarket token ID.
        side: "BUY" or "SELL".
        price: limit price (0-1 range).
        size_shares: number of shares.
        mode: "paper" or "live".

    Returns:
        dict with order_id or simulated fill info.
    """
    mode = mode or _get_mode()
    log.info("GTC order: %s %s @ %.4f x %.1f shares (mode=%s)",
             side, token_id[:16] + "...", price, size_shares, mode)

    if mode == "paper":
        order_id = f"paper-{int(time.time() * 1000)}"
        log.info("PAPER GTC: order=%s placed", order_id)
        return {
            "ok": True,
            "mode": "paper",
            "order_id": order_id,
            "token_id": token_id,
            "side": side,
            "price": price,
            "size_shares": size_shares,
            "status": "open",
        }

    # Live mode
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        return {"ok": False, "error": "py-clob-client not installed", "mode": "live"}

    private_key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        return {"ok": False, "error": "POLYMARKET_PRIVATE_KEY not set", "mode": "live"}

    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )
        result = client.create_and_post_order({
            "tokenID": token_id,
            "price": price,
            "size": size_shares,
            "side": side,
            "type": "GTC",
        })
        log.info("LIVE GTC: order=%s placed", result)
        return {
            "ok": True,
            "mode": "live",
            "order_id": result,
            "token_id": token_id,
            "side": side,
            "price": price,
            "size_shares": size_shares,
            "status": "open",
        }
    except Exception as e:
        log.error("GTC order failed: %s", e)
        return {"ok": False, "error": str(e), "mode": "live"}


def cancel_order(order_id, mode=None):
    """Cancel an open order.

    Args:
        order_id: order ID string.
        mode: "paper" or "live".

    Returns:
        dict with cancellation result.
    """
    mode = mode or _get_mode()
    log.info("Cancel order: %s (mode=%s)", order_id, mode)

    if mode == "paper":
        log.info("PAPER CANCEL: order=%s", order_id)
        return {"ok": True, "mode": "paper", "order_id": order_id, "status": "cancelled"}

    # Live mode
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        return {"ok": False, "error": "py-clob-client not installed", "mode": "live"}

    private_key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        return {"ok": False, "error": "POLYMARKET_PRIVATE_KEY not set", "mode": "live"}

    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )
        result = client.cancel(order_id)
        log.info("LIVE CANCEL: order=%s result=%s", order_id, result)
        return {"ok": True, "mode": "live", "order_id": order_id, "status": "cancelled",
                "result": result}
    except Exception as e:
        log.error("Cancel failed: %s", e)
        return {"ok": False, "error": str(e), "mode": "live"}


def manage_open_orders():
    """Check pending GTC maker orders: fill paper orders that have crossed,
    cancel any that have expired. Called each autonomy cycle.

    Returns counts of filled and cancelled orders.
    """
    mode = _get_mode()
    pending = db.get_open_orders(status="pending")
    if not pending:
        return {"filled": 0, "cancelled": 0}

    filled = 0
    cancelled = 0
    now = time.time()

    for order in pending:
        # Cancel expired orders
        if now > order["expires_at"]:
            log.info("Cancelling expired maker order %s (trade=%s leg=%s)",
                     order["order_id"], order["trade_id"], order["leg"])
            if mode == "live":
                cancel_order(order["order_id"], mode="live")
            db.cancel_open_order(order["id"], reason="expired")
            cancelled += 1
            continue

        if mode == "paper":
            # In paper mode: check if current mid has crossed our limit price.
            # If yes, consider it filled at our limit (best-case simulation).
            try:
                mid = api.get_midpoint(order["token_id"])
                side = order["side"]
                limit = order["limit_price"]
                crossed = (side == "BUY"  and mid <= limit) or \
                          (side == "SELL" and mid >= limit)
                if crossed:
                    db.fill_open_order(order["id"], fill_price=limit)
                    log.info("PAPER MAKER FILL: order=%s leg=%s price=%.4f",
                             order["order_id"], order["leg"], limit)
                    filled += 1
            except Exception as e:
                log.warning("Paper order fill check failed: %s", e)

        else:  # live mode — query exchange for fill status
            try:
                from py_clob_client.client import ClobClient
                private_key = runtime_config.get("POLYMARKET_PRIVATE_KEY")
                client = ClobClient("https://clob.polymarket.com",
                                    key=private_key, chain_id=137)
                order_data = client.get_order(order["order_id"])
                status = (order_data or {}).get("status", "")
                if status in ("MATCHED", "FILLED"):
                    fill_price = float((order_data or {}).get("price",
                                       order["limit_price"]))
                    db.fill_open_order(order["id"], fill_price=fill_price)
                    log.info("LIVE MAKER FILL: order=%s leg=%s price=%.4f",
                             order["order_id"], order["leg"], fill_price)
                    filled += 1
            except Exception as e:
                log.warning("Live order status check failed for %s: %s",
                            order["order_id"], e)

    if filled or cancelled:
        log.info("manage_open_orders: %d filled, %d cancelled", filled, cancelled)
    return {"filled": filled, "cancelled": cancelled}
