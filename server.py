"""FastAPI backend — REST API + serves dashboard."""
from dotenv import load_dotenv
load_dotenv()

import logging
import threading
import time
from pathlib import Path
import asyncio

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from auth import require_admin, require_operator
import db
import scanner
import brain
from log_setup import init_logging

init_logging()
log = logging.getLogger("scanner.server")

app = FastAPI(title="Polymarket Scanner")


@app.on_event("startup")
async def startup_event():
    db.init_db()
    try:
        import wallet_monitor
        wallet_monitor.start()
        log.info("Wallet monitor started on server startup")
    except Exception as e:
        log.warning("Wallet monitor failed to start: %s", e)


@app.on_event("shutdown")
async def shutdown_event():
    try:
        import wallet_monitor
        wallet_monitor.stop()
    except Exception as e:
        log.warning("Wallet monitor failed to stop cleanly: %s", e)

    try:
        import async_api
        await async_api.close()
    except Exception as e:
        log.warning("Async API client failed to close cleanly: %s", e)


@app.middleware("http")
async def authorize_mutating_routes(request: Request, call_next):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return await call_next(request)

    path = request.url.path
    required = None

    if (
        path.startswith("/api/trades")
        or path.startswith("/api/weather/")
        or path == "/api/copy/mirror"
        or path == "/api/copy/watch"
        or path.startswith("/api/copy/watch/")
        or path.startswith("/api/copy/candidates/")
    ):
        required = "admin"
    elif (
        path.startswith("/api/scan")
        or path == "/api/autonomy"
        or path.startswith("/api/brain/validate/")
        or path == "/api/copy/score"
        or path == "/api/copy/discover"
    ):
        required = "operator"

    if required == "admin":
        await require_admin(request=request, x_api_key=request.headers.get("X-API-Key"))
    elif required == "operator":
        await require_operator(request=request, x_api_key=request.headers.get("X-API-Key"))

    return await call_next(request)

DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"


def _save_pairs_scan_run(scan_result, duration):
    opportunities = scan_result["opportunities"]
    db.save_scan_run(
        pairs_tested=scan_result["pairs_tested"],
        cointegrated=scan_result["pairs_cointegrated"],
        opportunities=len(opportunities),
        duration=duration,
    )

    signal_ids = []
    for opp in opportunities:
        try:
            signal_ids.append(db.save_signal(opp))
        except Exception as e:
            log.warning("Failed to save signal: %s", e)

    return {
        "opportunities": len(opportunities),
        "signal_ids": signal_ids,
        "duration_secs": round(duration, 1),
        "signals": opportunities,
        "pairs_tested": scan_result["pairs_tested"],
        "cointegrated": scan_result["pairs_cointegrated"],
    }


def _run_job(job_id, job_kind, work_fn):
    db.start_scan_job(job_id)
    try:
        result = work_fn()
        db.finish_scan_job(job_id, result)
        log.info("%s job %d completed", job_kind, job_id)
    except Exception as e:
        log.error("%s job %d failed: %s", job_kind, job_id, e)
        db.fail_scan_job(job_id, str(e))


def _start_job(job_kind, params, work_fn):
    job_id = db.create_scan_job(job_kind, params)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, job_kind, work_fn),
        name=f"{job_kind}-job-{job_id}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as e:
        db.fail_scan_job(job_id, f"Failed to start background worker: {e}")
        log.error("Failed to start %s job %d: %s", job_kind, job_id, e)
        raise HTTPException(500, f"Failed to start {job_kind} job")
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "queued", "kind": job_kind})


# --- Dashboard ---

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_PATH.read_text()


# --- Stats ---

@app.get("/api/stats")
async def stats():
    return db.get_stats()


# --- Signals ---

@app.get("/api/signals")
async def list_signals(limit: int = 50, status: str = None):
    return db.get_signals(limit=limit, status=status)


@app.get("/api/signals/{signal_id}")
async def get_signal(signal_id: int):
    s = db.get_signal_by_id(signal_id)
    if not s:
        raise HTTPException(404, "Signal not found")
    return s


# --- Scan ---

@app.post("/api/scan")
async def run_scan(
    z_threshold: float = 1.5,
    p_threshold: float = 0.10,
    min_liquidity: float = 5000,
    interval: str = "1w",
):
    """Queue a scan and return a persisted job id."""
    params = {
        "z_threshold": z_threshold,
        "p_threshold": p_threshold,
        "min_liquidity": min_liquidity,
        "interval": interval,
    }
    log.info("Queued scan job: %s", params)

    def work():
        t0 = time.time()
        result = scanner.scan(verbose=False, include_stats=True, **params)
        return _save_pairs_scan_run(result, time.time() - t0)

    return _start_job("scan", params, work)


# --- Fast Scan (async) ---

@app.post("/api/scan/fast")
async def run_fast_scan(
    z_threshold: float = 1.5,
    p_threshold: float = 0.10,
    min_liquidity: float = 5000,
    interval: str = "1w",
):
    """Queue the async scanner and return a persisted job id."""
    import async_scanner

    params = {
        "z_threshold": z_threshold,
        "p_threshold": p_threshold,
        "min_liquidity": min_liquidity,
        "interval": interval,
    }
    log.info("Queued fast scan job: %s", params)

    def work():
        t0 = time.time()
        result = asyncio.run(async_scanner.scan(verbose=False, include_stats=True, **params))
        return _save_pairs_scan_run(result, time.time() - t0)

    return _start_job("fast_scan", params, work)


# --- Weather Edge ---

@app.post("/api/scan/weather")
async def run_weather_scan(
    min_edge: float = 0.06,
    min_liquidity: float = 200,
):
    """Queue the weather scan and return a persisted job id."""
    import weather_scanner

    params = {
        "min_edge": min_edge,
        "min_liquidity": min_liquidity,
    }
    log.info("Queued weather scan job: %s", params)

    def work():
        t0 = time.time()
        opportunities, meta = weather_scanner.scan(verbose=False, **params)
        saved_ids = []
        for opp in opportunities:
            try:
                saved_ids.append(db.save_weather_signal(opp))
            except Exception as e:
                log.warning("Failed to save weather signal: %s", e)

        tradeable = sum(1 for o in opportunities if o.get("tradeable"))
        return {
            "opportunities": len(opportunities),
            "tradeable": tradeable,
            "saved_ids": saved_ids,
            "duration_secs": round(time.time() - t0, 1),
            "markets_checked": meta.get("markets_checked", 0),
            "weather_found": meta.get("weather_found", 0),
            "results": opportunities,
        }

    return _start_job("weather_scan", params, work)


@app.get("/api/weather")
async def list_weather_signals(limit: int = 50, tradeable_only: bool = False):
    """Return recent weather-edge opportunities from the database."""
    return db.get_weather_signals(limit=limit, tradeable_only=tradeable_only)


# --- Locked Market Arb ---

@app.post("/api/scan/locked")
async def run_locked_scan(
    min_net_gap: float = 0.005,
    min_liquidity: float = 500,
    check_slippage: bool = True,
    trade_size_usd: float = 100,
):
    """Queue the locked-market scan and return a persisted job id."""
    import locked_scanner

    params = {
        "min_net_gap": min_net_gap,
        "min_liquidity": min_liquidity,
        "check_slippage": check_slippage,
        "trade_size_usd": trade_size_usd,
    }
    log.info("Queued locked scan job: %s", params)

    def work():
        t0 = time.time()
        opportunities = locked_scanner.scan(verbose=False, **params)
        saved_ids = []
        for opp in opportunities:
            try:
                saved_ids.append(db.save_locked_arb(opp))
            except Exception as e:
                log.warning("Failed to save locked arb: %s", e)

        tradeable = sum(1 for o in opportunities if o.get("tradeable"))
        return {
            "opportunities": len(opportunities),
            "tradeable": tradeable,
            "saved_ids": saved_ids,
            "duration_secs": round(time.time() - t0, 1),
            "results": opportunities,
        }

    return _start_job("locked_scan", params, work)


@app.get("/api/locked")
async def list_locked_arb(limit: int = 50, tradeable_only: bool = False):
    """Return recent locked-arb opportunities from the database."""
    return db.get_locked_arb(limit=limit, tradeable_only=tradeable_only)


# --- Brain (Claude AI) ---

@app.post("/api/brain/validate/{signal_id}")
async def brain_validate(signal_id: int):
    """Ask Claude to validate a signal before trading."""
    for s in db.get_signals(limit=500):
        if s["id"] == signal_id:
            should_trade, reasoning = brain.validate_signal(s)
            return {
                "signal_id": signal_id,
                "should_trade": should_trade,
                "reasoning": reasoning,
            }
    raise HTTPException(404, "Signal not found")


# --- Trades ---

@app.get("/api/trades")
async def list_trades(status: str = None, limit: int = 50):
    return db.get_trades(status=status, limit=limit)


@app.get("/api/trades/{trade_id}")
async def get_trade(trade_id: int):
    trade = db.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, "Trade not found")
    return trade


@app.post("/api/trades")
async def create_trade(signal_id: int, size_usd: float = 100):
    """Open a paper trade from a signal."""
    trade_id = db.open_trade(signal_id, size_usd=size_usd)
    if not trade_id:
        raise HTTPException(404, "Signal not found")
    return {"trade_id": trade_id, "status": "open"}


@app.post("/api/trades/{trade_id}/close")
async def close_trade(trade_id: int, exit_price_a: float, exit_price_b: float = None, notes: str = ""):
    """Close a paper trade. exit_price_b is optional for single-leg (weather) trades."""
    pnl = db.close_trade(trade_id, exit_price_a, exit_price_b, notes)
    if pnl is None:
        raise HTTPException(404, "Trade not found")
    return {"trade_id": trade_id, "pnl": round(pnl, 2), "status": "closed"}


@app.post("/api/weather/{signal_id}/trade")
async def open_weather_trade(signal_id: int, size_usd: float = 20):
    """Open a paper trade from a weather signal."""
    trade_id = db.open_weather_trade(signal_id, size_usd=size_usd)
    if not trade_id:
        raise HTTPException(404, "Weather signal not found or already traded")
    return {"trade_id": trade_id, "signal_id": signal_id, "status": "open"}


# --- Snapshots ---

@app.get("/api/trades/{trade_id}/snapshots")
async def trade_snapshots(trade_id: int):
    return db.get_snapshots(trade_id)


# --- Scan History ---

@app.get("/api/scan-runs")
async def scan_runs(limit: int = 20):
    return db.get_scan_runs(limit=limit)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    job = db.get_scan_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# --- Logs ---

@app.get("/api/logs")
async def get_logs(lines: int = 100):
    """Return recent log lines from scanner.log."""
    log_path = Path(__file__).parent / "logs" / "scanner.log"
    if not log_path.exists():
        return {"lines": []}
    try:
        with open(log_path, "r") as f:
            all_lines = f.readlines()
        recent = [l.rstrip() for l in all_lines[-lines:]]
        return {"lines": recent}
    except Exception as e:
        return {"lines": [f"Error reading log: {e}"]}


# --- Autonomy ---

_autonomy_status = {"running": False, "last_result": None}

def _run_autonomy_background():
    """Run autonomy cycle in background thread."""
    import autonomy
    _autonomy_status["running"] = True
    t0 = time.time()
    try:
        stats_before = db.get_stats()
        state = autonomy.load_state()
        autonomy.run_cycle(state)
        stats_after = db.get_stats()
        duration = round(time.time() - t0, 1)
        _autonomy_status["last_result"] = {
            "ok": True,
            "duration_secs": duration,
            "signals_found": stats_after.get("total_signals", 0) - stats_before.get("total_signals", 0),
            "trades_opened": stats_after.get("open_trades", 0) - stats_before.get("open_trades", 0),
            "trades_closed": stats_after.get("closed_trades", 0) - stats_before.get("closed_trades", 0),
        }
        log.info("Autonomy cycle complete in %.1fs", duration)
    except Exception as e:
        log.error("Autonomy cycle failed: %s", e)
        _autonomy_status["last_result"] = {"ok": False, "error": str(e)}
    finally:
        _autonomy_status["running"] = False


@app.post("/api/autonomy")
async def run_autonomy():
    """Kick off autonomy cycle in background — returns immediately."""
    import threading
    if _autonomy_status["running"]:
        return {"ok": False, "error": "Cycle already running — check Console tab for progress"}
    thread = threading.Thread(target=_run_autonomy_background, daemon=True)
    thread.start()
    log.info("Autonomy cycle triggered from dashboard (background)")
    return {"ok": True, "message": "Autonomy cycle started — watch Console tab for progress"}


@app.get("/api/autonomy/status")
async def autonomy_status():
    """Check if an autonomy cycle is running and get last result."""
    return {
        "running": _autonomy_status["running"],
        "last_result": _autonomy_status["last_result"],
    }


# ── Copy Trader ───────────────────────────────────────────────────────────────

@app.get("/api/copy/monitor")
async def copy_monitor_status():
    """Wallet monitor status — scores, last poll, new trades detected."""
    import wallet_monitor
    return wallet_monitor.get_status()


@app.get("/api/copy/wallets")
async def copy_wallets():
    """Return analysis for all active watched wallets."""
    import copy_scanner
    results = []
    for row in db.get_watched_wallets(active_only=True):
        r = copy_scanner.analyse_wallet(row["address"], label=row["label"], limit=100)
        results.append(r)
    return results


@app.get("/api/copy/positions")
async def copy_positions():
    """Return current open positions for all active watched wallets, annotated with mirror status."""
    import copy_scanner
    mirrored = {
        t["copy_condition_id"]
        for t in db.get_trades(status="open", limit=500)
        if t.get("trade_type") == "copy" and t.get("copy_condition_id")
    }
    out = []
    for row in db.get_watched_wallets(active_only=True):
        address, label = row["address"], row["label"]
        positions = copy_scanner.get_positions(address)
        for p in positions:
            p["mirrored"] = p.get("conditionId", "") in mirrored
        value = copy_scanner.get_portfolio_value(address)
        out.append({
            "address": address,
            "label": label,
            "portfolio_usd": value,
            "positions": positions,
        })
    return out


@app.post("/api/copy/mirror")
async def mirror_position(wallet: str, condition_id: str, size_usd: float = 20.0):
    """Open a paper copy trade mirroring a watched wallet's position."""
    import copy_scanner
    wallets = {r["address"]: r["label"] for r in db.get_watched_wallets(active_only=True)}
    label = wallets.get(wallet, wallet[:10] + "...")
    positions = copy_scanner.get_positions(wallet)
    pos = next((p for p in positions if p.get("conditionId") == condition_id), None)
    if not pos:
        raise HTTPException(404, f"Position {condition_id} not found for wallet {wallet}")
    trade_id = db.open_copy_trade(wallet, label, pos, size_usd=size_usd)
    if trade_id is None:
        return {"ok": False, "error": "Already have an open copy trade for this position"}
    return {"ok": True, "trade_id": trade_id, "label": label,
            "market": pos.get("title"), "outcome": pos.get("outcome"),
            "price": pos.get("curPrice"), "size_usd": size_usd}


# --- Wallet Discovery ---

@app.get("/api/copy/watchlist")
async def get_watchlist():
    """Return all watched wallets (including dropped) with open trade counts."""
    conn = db.get_conn()
    rows = conn.execute("""
        SELECT ww.*,
               COUNT(CASE WHEN t.status='open' AND t.trade_type='copy' THEN 1 END) AS open_copy_trades
        FROM watched_wallets ww
        LEFT JOIN trades t ON t.copy_wallet = ww.address
        GROUP BY ww.id
        ORDER BY ww.active DESC, ww.score DESC
    """).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for field in ("score_breakdown", "ai_risk_flags"):
            if d.get(field):
                try:
                    import json as _json
                    d[field] = _json.loads(d[field])
                except Exception:
                    pass
        out.append(d)
    return out


@app.post("/api/copy/score")
async def score_wallet_endpoint(address: str, label: str = ""):
    """Score a wallet address and get Claude's recommendation."""
    import wallet_monitor
    import brain
    label = label or address[:16] + "..."
    address = address.lower()

    try:
        score_result = wallet_monitor.score_wallet(address, label)
    except Exception as e:
        log.error("Score wallet failed: %s", e)
        raise HTTPException(500, f"Scoring failed: {e}")

    ai = None
    try:
        ai = brain.recommend_wallet(address, label, score_result)
    except Exception as e:
        log.warning("Brain wallet rec failed: %s", e)

    return {
        "address": address,
        "label": label,
        "score": score_result.get("score"),
        "classification": score_result.get("classification"),
        "will_copy": score_result.get("will_copy"),
        "breakdown": score_result.get("breakdown"),
        "ai": {
            "verdict": ai.get("verdict") if ai else None,
            "reasoning": ai.get("reasoning") if ai else None,
            "risk_flags": ai.get("risk_flags") if ai else [],
            "confidence": ai.get("confidence") if ai else None,
            "available": ai is not None,
        },
    }


@app.post("/api/copy/watch")
async def add_to_watchlist(
    address: str,
    label: str = "",
    ai_verdict: str = None,
    ai_reasoning: str = None,
):
    """Add a wallet to the watch list."""
    address = address.lower()
    label = label or address[:16] + "..."
    row_id = db.add_watched_wallet(address, label)
    if ai_verdict:
        db.update_wallet_ai(address, ai_verdict, ai_reasoning or "", [])
    # Kick off a background score if not yet scored
    import threading, wallet_monitor
    def _bg_score():
        try:
            result = wallet_monitor.score_wallet(address, label)
            db.update_wallet_score(address, result)
            wallet_monitor._status["wallets"][address] = result
        except Exception as e:
            log.warning("Background score failed for %s: %s", address, e)
    threading.Thread(target=_bg_score, daemon=True).start()
    return {"ok": True, "id": row_id, "address": address, "label": label}


@app.get("/api/copy/candidates")
async def list_candidates(status: str = "pending"):
    """Return wallet candidates from automated discovery."""
    return db.get_wallet_candidates(status=status)


_discovery_status = {"running": False, "last_result": None}


@app.post("/api/copy/discover")
async def run_discovery(auto_add: bool = True):
    """Trigger automated wallet discovery in the background."""
    import threading
    if _discovery_status["running"]:
        return {"ok": False, "error": "Discovery already running"}

    def _bg():
        import wallet_discovery
        _discovery_status["running"] = True
        try:
            result = wallet_discovery.run_discovery(auto_add=auto_add)
            _discovery_status["last_result"] = result
        except Exception as e:
            log.error("Discovery failed: %s", e)
            _discovery_status["last_result"] = {"ok": False, "error": str(e)}
        finally:
            _discovery_status["running"] = False

    threading.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "message": "Discovery started — check candidates tab when done"}


@app.get("/api/copy/discover/status")
async def discovery_status():
    return {"running": _discovery_status["running"], "last_result": _discovery_status["last_result"]}


@app.post("/api/copy/candidates/{candidate_id}/add")
async def add_candidate(candidate_id: int):
    """Add a pending candidate to the watch list."""
    candidates = db.get_wallet_candidates(status="pending")
    c = next((x for x in candidates if x["id"] == candidate_id), None)
    if not c:
        raise HTTPException(404, "Candidate not found")
    db.add_watched_wallet(c["address"], c["label"], added_by="manual_from_candidate")
    if c.get("ai_verdict"):
        db.update_wallet_ai(c["address"], c["ai_verdict"],
                            c.get("ai_reasoning", ""), c.get("ai_risk_flags") or [])
    db.update_candidate_status(candidate_id, "added")
    return {"ok": True, "address": c["address"], "label": c["label"]}


@app.post("/api/copy/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(candidate_id: int):
    """Dismiss a pending candidate."""
    db.update_candidate_status(candidate_id, "dismissed")
    return {"ok": True}


@app.delete("/api/copy/watch/{address}")
async def remove_from_watchlist(address: str):
    """Remove a wallet and close its open copy trades."""
    address = address.lower()
    # Close open copy trades
    open_copy = [
        t for t in db.get_trades(status="open", limit=500)
        if t.get("trade_type") == "copy" and t.get("copy_wallet") == address
    ]
    closed = 0
    for trade in open_copy:
        db.close_trade(trade["id"], exit_price_a=trade.get("entry_price_a", 0.5),
                       notes="removed from watchlist")
        closed += 1
    removed = db.remove_watched_wallet(address)
    return {"ok": removed, "address": address, "trades_closed": closed}


if __name__ == "__main__":
    import uvicorn
    print("Starting Polymarket Scanner on http://localhost:8899")
    uvicorn.run(app, host="0.0.0.0", port=8899)
