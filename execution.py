"""Order execution engine — paper and live trading for Polymarket pairs.

Paper mode (default): simulates orders against current midpoint prices.
Live mode: uses py-clob-client for real orders on Polymarket (requires POLYMARKET_PRIVATE_KEY).
"""
import logging
import os
import time

import api
import db
import math_engine

log = logging.getLogger("scanner.execution")

MAX_SLIPPAGE_PCT  = 2.5
PAPER_BALANCE_USD = 2_000.0

# Execution mode: "maker" (GTC limit orders, 0% fee) or "taker" (market orders, 2% fee).
# Default is maker — post inside the spread, pay no fees, capture better prices.
EXECUTION_MODE = os.environ.get("EXECUTION_MODE", "maker")

# How far inside the spread we post our limit (fraction of half-spread).
# 0.5 = halfway between mid and best bid/ask.
MAKER_AGGRESSION = 0.5

# GTC orders expire and are cancelled after this many hours if unfilled.
ORDER_TTL_HOURS = 4

def _get_mode():
    """Determine trading mode from environment."""
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
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
    """Override size_usd with confidence-recommended sizing when active."""
    decision = _extract_confidence_decision(signal)
    if not decision or not decision.get("applied"):
        return size_usd, None
    try:
        recommended = round(float(decision.get("selected_size_usd") or size_usd), 2)
    except (TypeError, ValueError):
        recommended = size_usd
    if recommended <= 0:
        return size_usd, None
    meta = {
        "confidence_score": round(float(decision.get("confidence_score") or 0.0), 4),
        "confidence_policy": decision.get("selected_policy"),
        "confidence_baseline_size_usd": round(float(decision.get("baseline_size_usd") or 0.0), 2),
        "confidence_selected_size_usd": recommended,
    }
    return recommended, meta


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


def check_balance(mode=None):
    """Check available USDC balance.

    Paper mode: returns simulated balance.
    Live mode: queries on-chain USDC.e balance via blockchain module.

    Returns:
        dict with balance_usd, mode, and any error info.
    """
    mode = mode or _get_mode()

    if mode == "paper":
        account = db.get_paper_account_state(refresh_unrealized=False)
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
            "paper_account": account,
        }

    # Live mode — use blockchain module
    try:
        import blockchain
        wallet = blockchain.get_wallet_address()
        if not wallet:
            return {"ok": False, "balance_usd": 0, "mode": "live",
                    "error": "No wallet address available"}
        balance = blockchain.get_usdc_balance(wallet)
        log.info("Live balance for %s: $%.2f", wallet[:10] + "...", balance)
        return {"ok": True, "balance_usd": balance, "mode": "live"}
    except Exception as e:
        log.error("Failed to check live balance: %s", e)
        return {"ok": False, "balance_usd": 0, "mode": "live",
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


def execute_trade(signal, size_usd, mode=None):
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
    log.info("Executing trade: %s | size=$%.2f mode=%s z=%.2f",
             signal.get("event", "?")[:50], size_usd, mode, signal.get("z_score", 0))

    # 1. Balance pre-check
    bal = check_balance(mode)
    if not bal["ok"]:
        log.warning("Balance check failed: %s", bal.get("error"))
        return {"ok": False, "error": f"Balance check failed: {bal.get('error')}",
                "mode": mode}

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
        return {"ok": False, "error": f"Insufficient balance: ${bal['balance_usd']:.2f} < ${size_usd:.2f}",
                "mode": mode}

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
            return {"ok": False, "error": f"Maker price fetch failed: {e}", "mode": mode}
    else:
        try:
            price_a = api.get_midpoint(token_a)
            price_b = api.get_midpoint(token_b)
        except Exception as e:
            log.error("Failed to fetch current prices: %s", e)
            return {"ok": False, "error": f"Price fetch failed: {e}", "mode": mode}

    if price_a <= 0 or price_b <= 0:
        log.warning("Invalid prices: a=%.4f b=%.4f", price_a, price_b)
        return {"ok": False, "error": f"Invalid prices: a={price_a} b={price_b}",
                "mode": mode}

    # 3. Slippage check — taker mode only (maker orders set their own price)
    if mode == "live" and exec_mode == "taker":
        slippage = math_engine.check_slippage(
            token_a, trade_size_usd=size_usd / 2, max_slippage_pct=MAX_SLIPPAGE_PCT,
        )
        if not slippage["ok"]:
            log.warning("Slippage check failed for leg A: %s", slippage.get("reason"))
            return {"ok": False, "error": f"Slippage too high: {slippage.get('reason')}",
                    "slippage": slippage, "mode": mode}
        slippage_b = math_engine.check_slippage(
            token_b, trade_size_usd=size_usd / 2, max_slippage_pct=MAX_SLIPPAGE_PCT,
        )
        if not slippage_b["ok"]:
            log.warning("Slippage check failed for leg B: %s", slippage_b.get("reason"))
            return {"ok": False, "error": f"Slippage too high on leg B: {slippage_b.get('reason')}",
                    "slippage": slippage_b, "mode": mode}
    elif mode == "paper":
        slippage = math_engine.check_slippage(token_a, trade_size_usd=size_usd / 2)
        log.info("Paper slippage (informational, %s): leg A=%.2f%%",
                 exec_mode, slippage.get("slippage_pct") or 0)

    # 4. HMRC gate — block live trades if GBP audit logging is unavailable
    if mode == "live":
        try:
            import hmrc
            gbp_rate = hmrc.require_gbp_rate()
        except RuntimeError as e:
            log.error("LIVE TRADE BLOCKED — HMRC compliance failure: %s", e)
            return {"ok": False, "error": str(e), "mode": mode}

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

    return result


def execute_weather_trade(signal, size_usd, mode=None):
    """Execute a paper-first weather trade from a saved weather signal."""
    mode = mode or _get_mode()
    signal = dict(signal or {})
    weather_signal_id = signal.get("id") or signal.get("weather_signal_id")
    if not weather_signal_id:
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "signal_not_found",
            "error": "Weather signal missing id.",
        }

    if not signal.get("id"):
        signal = db.get_weather_signal_by_id(weather_signal_id) or {}
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
        "Executing weather trade: signal=%s strategy=%s size=$%.2f mode=%s",
        weather_signal_id,
        strategy_name,
        size_usd,
        mode,
    )

    if mode != "paper":
        log.warning(
            "Weather live execution blocked for signal %s strategy=%s",
            weather_signal_id,
            strategy_name,
        )
        return {
            "ok": False,
            "mode": mode,
            "reason_code": "paper_only_mode",
            "error": "Weather execution is paper-only unless live rollout is explicitly approved.",
            "strategy_name": strategy_name,
            "weather_signal_id": weather_signal_id,
        }

    bal = check_balance("paper")
    if not bal["ok"]:
        return {
            "ok": False,
            "mode": "paper",
            "reason_code": "balance_check_failed",
            "error": f"Balance check failed: {bal.get('error')}",
            "weather_signal_id": weather_signal_id,
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
            "mode": "paper",
            "reason_code": "insufficient_cash",
            "error": f"Insufficient balance: ${bal['balance_usd']:.2f} < ${size_usd:.2f}",
            "paper_account": bal.get("paper_account"),
            "weather_signal_id": weather_signal_id,
        }

    decision = db.inspect_weather_trade_open(weather_signal_id, size_usd=size_usd)
    if not decision["ok"]:
        return {
            "ok": False,
            "mode": "paper",
            "reason_code": decision.get("reason_code"),
            "error": decision.get("reason"),
            "decision": decision,
            "weather_signal_id": weather_signal_id,
            "strategy_name": strategy_name,
        }

    trade_id = db.open_weather_trade(weather_signal_id, size_usd=size_usd)
    if not trade_id:
        return {
            "ok": False,
            "mode": "paper",
            "reason_code": "open_failed",
            "error": "Weather trade could not be opened after preflight passed.",
            "weather_signal_id": weather_signal_id,
            "strategy_name": strategy_name,
        }

    account = db.get_paper_account_state(refresh_unrealized=False)
    return {
        "ok": True,
        "mode": "paper",
        "trade_id": trade_id,
        "weather_signal_id": weather_signal_id,
        "signal_id": weather_signal_id,
        "strategy_name": strategy_name,
        "entry_price": decision.get("entry_price"),
        "action": decision.get("action"),
        "trade_state_mode": db.TRADE_STATE_PAPER,
        "reconciliation_mode": db.RECONCILIATION_INTERNAL,
        "paper_account": account,
        "confidence_score": confidence_meta.get("confidence_score") if confidence_meta else None,
        "confidence_policy": confidence_meta.get("confidence_policy") if confidence_meta else None,
        "confidence_applied": bool(confidence_meta),
        "quarter_kelly_capped": bool(quarter_kelly_capped),
    }


def _execute_paper(signal, size_usd, price_a, price_b,
                   side_a="BUY", side_b="SELL", exec_mode="maker",
                   confidence_metadata=None, quarter_kelly_capped=False):
    """Simulate order fill.

    Maker mode: records limit prices (better than mid) with 0% fee — optimistic
    but correct for benchmarking maker strategy vs taker.
    Taker mode: fills at midpoint with fee already baked into EV model.
    """
    signal_id = signal.get("id")
    if not signal_id:
        log.error("Signal missing 'id' field, cannot record trade")
        return {"ok": False, "error": "Signal missing id", "mode": "paper"}

    trade_id = db.open_trade(
        signal_id,
        size_usd=size_usd,
        metadata={
            "strategy_name": "cointegration",
            "trade_state_mode": db.TRADE_STATE_PAPER,
            "reconciliation_mode": db.RECONCILIATION_INTERNAL,
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
        return {"ok": False, "error": "DB open_trade failed", "mode": "paper"}
    account = db.get_paper_account_state(refresh_unrealized=False)

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
        "paper_account": account,
        "confidence_score": confidence_metadata.get("confidence_score") if confidence_metadata else None,
        "confidence_policy": confidence_metadata.get("confidence_policy") if confidence_metadata else None,
        "confidence_applied": bool(confidence_metadata),
        "quarter_kelly_capped": bool(quarter_kelly_capped),
    }


def settle_paper_trade(trade_id, pnl_usd):
    """Retained for compatibility. Paper accounting is now derived from SQLite."""
    account = db.get_paper_account_state(refresh_unrealized=False)
    log.info(
        "PAPER CLOSE: trade=%d | pnl=$%.2f | available=$%.2f equity=$%.2f",
        trade_id,
        pnl_usd or 0,
        account["available_cash"],
        account["total_equity"],
    )
    return True


def _execute_live(signal, size_usd, price_a, price_b,
                  side_a="BUY", side_b="SELL", exec_mode="maker",
                  confidence_metadata=None, quarter_kelly_capped=False):
    """Execute real orders via py-clob-client.

    Maker mode: GTC limit orders posted inside spread — fills when someone
    crosses our price. Pending until filled or expired.
    Taker mode: market-style orders that fill immediately at ask/bid.
    """
    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        log.error("py-clob-client not installed. Run: pip install py-clob-client")
        return {"ok": False, "error": "py-clob-client not installed", "mode": "live"}

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        log.error("POLYMARKET_PRIVATE_KEY not set")
        return {"ok": False, "error": "POLYMARKET_PRIVATE_KEY not set", "mode": "live"}

    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=137,
        )

        half_size = size_usd / 2
        token_a = signal.get("token_id_a") or signal["market_a"]
        token_b = signal.get("token_id_b") or signal["market_b"]
        order_type = "GTC" if exec_mode == "maker" else "FOK"

        order_a = client.create_and_post_order({
            "tokenID": token_a,
            "price":   price_a,
            "size":    round(half_size / price_a, 4) if price_a > 0 else 0,
            "side":    side_a,
            "type":    order_type,
        })
        order_b = client.create_and_post_order({
            "tokenID": token_b,
            "price":   price_b,
            "size":    round(half_size / price_b, 4) if price_b > 0 else 0,
            "side":    side_b,
            "type":    order_type,
        })

        signal_id = signal.get("id")
        wallet_address = None
        try:
            import blockchain
            wallet_address = blockchain.get_wallet_address()
        except Exception:
            wallet_address = None
        live_identity = db.build_live_trade_identity(str(order_a), str(order_b), wallet=wallet_address)
        # In maker mode the trade is pending until both legs fill
        trade_status = "pending_fill" if exec_mode == "maker" else "open"
        trade_id = db.open_trade(
            signal_id,
            size_usd=size_usd,
            metadata={
                "strategy_name": "cointegration_live",
                "trade_state_mode": db.TRADE_STATE_LIVE,
                "reconciliation_mode": db.RECONCILIATION_ORDERS,
                **live_identity,
            },
        ) if signal_id else None

        now = time.time()
        expires = now + ORDER_TTL_HOURS * 3600
        for leg, token_id, side, price, order_id in [
            ("a", token_a, side_a, price_a, str(order_a)),
            ("b", token_b, side_b, price_b, str(order_b)),
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
            })

        log.info("LIVE %s: trade=%s | orders=%s,%s | size=$%.2f",
                 exec_mode.upper(), trade_id, order_a, order_b, size_usd)

        return {
            "ok": True,
            "mode": "live",
            "exec_mode": exec_mode,
            "trade_id": trade_id,
            "canonical_ref": live_identity["canonical_ref"],
            "order_a": str(order_a),
            "order_b": str(order_b),
            "fill_price_a": price_a,
            "fill_price_b": price_b,
            "size_usd": size_usd,
            "pending": exec_mode == "maker",
            "confidence_score": confidence_metadata.get("confidence_score") if confidence_metadata else None,
            "confidence_policy": confidence_metadata.get("confidence_policy") if confidence_metadata else None,
            "confidence_applied": bool(confidence_metadata),
            "quarter_kelly_capped": bool(quarter_kelly_capped),
        }

    except Exception as e:
        log.error("Live execution failed: %s", e)
        return {"ok": False, "error": str(e), "mode": "live"}


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

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
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

    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
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
                private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
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
