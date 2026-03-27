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

MAX_SLIPPAGE_PCT = 2.5
PAPER_BALANCE_USD = 10_000.0  # simulated starting balance

# In-memory paper balance tracker (resets on restart)
_paper_state = {
    "balance": PAPER_BALANCE_USD,
    "fills": [],
}


def _get_mode():
    """Determine trading mode from environment."""
    key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if key:
        return "live"
    return "paper"


def check_balance(mode=None):
    """Check available USDC balance.

    Paper mode: returns simulated balance.
    Live mode: queries on-chain USDC.e balance via blockchain module.

    Returns:
        dict with balance_usd, mode, and any error info.
    """
    mode = mode or _get_mode()

    if mode == "paper":
        log.debug("Paper balance check: $%.2f", _paper_state["balance"])
        return {
            "ok": True,
            "balance_usd": _paper_state["balance"],
            "mode": "paper",
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
    log.info("Executing trade: %s | size=$%.2f mode=%s z=%.2f",
             signal.get("event", "?")[:50], size_usd, mode, signal.get("z_score", 0))

    # 1. Balance pre-check
    bal = check_balance(mode)
    if not bal["ok"]:
        log.warning("Balance check failed: %s", bal.get("error"))
        return {"ok": False, "error": f"Balance check failed: {bal.get('error')}",
                "mode": mode}

    if bal["balance_usd"] < size_usd:
        log.warning("Insufficient balance: $%.2f < $%.2f", bal["balance_usd"], size_usd)
        return {"ok": False, "error": f"Insufficient balance: ${bal['balance_usd']:.2f} < ${size_usd:.2f}",
                "mode": mode}

    # 2. Fetch current prices — use token IDs (numeric), fall back to market name
    token_a = signal.get("token_id_a") or signal["market_a"]
    token_b = signal.get("token_id_b") or signal["market_b"]
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

    # 3. Slippage check — live only (paper trades don't execute real orders)
    if mode == "live":
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
    else:
        # Paper mode: log slippage as info only, don't block
        slippage = math_engine.check_slippage(token_a, trade_size_usd=size_usd / 2)
        log.info("Paper slippage (informational): leg A=%.2f%%", slippage.get("slippage_pct") or 0)

    # 4. HMRC gate — block live trades if GBP audit logging is unavailable
    if mode == "live":
        try:
            import hmrc
            gbp_rate = hmrc.require_gbp_rate()
        except RuntimeError as e:
            log.error("LIVE TRADE BLOCKED — HMRC compliance failure: %s", e)
            return {"ok": False, "error": str(e), "mode": mode}

    # 5. Execute based on mode
    if mode == "paper":
        result = _execute_paper(signal, size_usd, price_a, price_b)
    else:
        result = _execute_live(signal, size_usd, price_a, price_b)

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


def _execute_paper(signal, size_usd, price_a, price_b):
    """Simulate order fill at current midpoint prices."""
    # Record the trade in DB
    signal_id = signal.get("id")
    if not signal_id:
        log.error("Signal missing 'id' field, cannot record trade")
        return {"ok": False, "error": "Signal missing id", "mode": "paper"}

    trade_id = db.open_trade(signal_id, size_usd=size_usd)
    if not trade_id:
        log.error("Failed to open trade in DB for signal %s", signal_id)
        return {"ok": False, "error": "DB open_trade failed", "mode": "paper"}

    # Deduct from paper balance
    _paper_state["balance"] -= size_usd
    fill = {
        "trade_id": trade_id,
        "signal_id": signal_id,
        "fill_price_a": price_a,
        "fill_price_b": price_b,
        "size_usd": size_usd,
        "timestamp": time.time(),
    }
    _paper_state["fills"].append(fill)

    log.info("PAPER FILL: trade=%d | A=%.4f B=%.4f | size=$%.2f | balance=$%.2f",
             trade_id, price_a, price_b, size_usd, _paper_state["balance"])

    return {
        "ok": True,
        "mode": "paper",
        "trade_id": trade_id,
        "signal_id": signal_id,
        "fill_price_a": price_a,
        "fill_price_b": price_b,
        "size_usd": size_usd,
        "remaining_balance": _paper_state["balance"],
    }


def _execute_live(signal, size_usd, price_a, price_b):
    """Execute real orders via py-clob-client."""
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
            chain_id=137,  # Polygon mainnet
        )

        # Determine order sides from z-score
        z = signal.get("z_score", 0)
        if z < 0:
            side_a, side_b = "BUY", "SELL"
        else:
            side_a, side_b = "SELL", "BUY"

        half_size = size_usd / 2

        # Place leg A
        order_a = client.create_and_post_order({
            "tokenID": signal.get("token_id_a") or signal["market_a"],
            "price": price_a,
            "size": half_size / price_a if price_a > 0 else 0,
            "side": side_a,
        })

        # Place leg B
        order_b = client.create_and_post_order({
            "tokenID": signal.get("token_id_b") or signal["market_b"],
            "price": price_b,
            "size": half_size / price_b if price_b > 0 else 0,
            "side": side_b,
        })

        # Record in DB
        signal_id = signal.get("id")
        trade_id = db.open_trade(signal_id, size_usd=size_usd) if signal_id else None

        log.info("LIVE FILL: trade=%s | orders=%s,%s | size=$%.2f",
                 trade_id, order_a, order_b, size_usd)

        return {
            "ok": True,
            "mode": "live",
            "trade_id": trade_id,
            "order_a": order_a,
            "order_b": order_b,
            "fill_price_a": price_a,
            "fill_price_b": price_b,
            "size_usd": size_usd,
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
