"""FastAPI backend — REST API + serves dashboard."""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from auth import require_admin, require_operator
import db
import scanner
import brain
import cointegration_trial
import trade_monitor
from log_setup import init_logging

init_logging()
log = logging.getLogger("scanner.server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        import wallet_monitor
        wallet_monitor.start()
        log.info("Wallet monitor started on server startup")
    except Exception as e:
        log.warning("Wallet monitor failed to start: %s", e)

    try:
        yield
    finally:
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


app = FastAPI(title="Polymarket Scanner", lifespan=lifespan)


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
        or path == "/api/copy/settings"
        or path == "/api/copy/watch"
        or path.startswith("/api/copy/watch/")
        or path.startswith("/api/copy/candidates/")
    ):
        required = "admin"
    elif (
        path.startswith("/api/scan")
        or path == "/api/autonomy"
        or path.startswith("/api/brain/validate/")
        or path.startswith("/api/reports/")
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
DAILY_REPORTS_DIR = Path(__file__).parent / "reports"
DIAGNOSTICS_DIR = Path(__file__).parent / "reports" / "diagnostics"
IMPLEMENTATION_PLAN_PATH = Path(__file__).parent / "implementation-plan.md"
TESTING_IDEAS_PATH = Path(__file__).parent / "testing-ideas.md"
FIX_LOGS_DIR = Path(__file__).parent / "fix_logs"


def _safe_record_paper_trade_attempt(**kwargs) -> bool:
    recorder = getattr(db, "record_paper_trade_attempt", None)
    if not callable(recorder):
        log.warning("Paper-trade attempt logging unavailable in db module; skipping event")
        return False
    try:
        recorder(**kwargs)
        return True
    except Exception as exc:
        log.warning("Paper-trade attempt logging failed: %s", exc)
        return False


def _paper_trade_attempt_feed(limit: int) -> dict:
    attempts_getter = getattr(db, "get_paper_trade_attempts", None)
    summary_getter = getattr(db, "get_paper_trade_attempt_summary", None)
    if not callable(attempts_getter) or not callable(summary_getter):
        return {
            "available": False,
            "degraded_reason": "paper_trade_attempt_api_missing",
            "attempts": [],
            "summary": {
                "available": False,
                "recent_count": 0,
                "allowed": 0,
                "blocked": 0,
                "errors": 0,
                "top_blockers": [],
            },
        }

    try:
        attempts = attempts_getter(limit=limit)
        summary = summary_getter(limit=limit)
        if not isinstance(summary, dict):
            summary = {}
        summary.setdefault("available", True)
        return {
            "available": bool(summary.get("available", True)),
            "degraded_reason": None,
            "attempts": attempts or [],
            "summary": summary,
        }
    except Exception as exc:
        log.warning("Paper-trade attempt feed unavailable: %s", exc)
        return {
            "available": False,
            "degraded_reason": str(exc),
            "attempts": [],
            "summary": {
                "available": False,
                "recent_count": 0,
                "allowed": 0,
                "blocked": 0,
                "errors": 0,
                "top_blockers": [],
            },
        }


def _tail_lines(path: Path, limit: int = 80) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return [line.rstrip() for line in f.readlines()[-limit:]]
    except Exception as e:
        log.warning("Failed to read %s: %s", path.name, e)
        return []


def _fallback_daily_report(context: dict) -> dict:
    stats = context["stats"]
    working = [
        f"Dashboard API is serving with {stats['open_trades']} open trades and {stats['closed_trades']} closed trades recorded.",
        f"Historical win rate is {stats['win_rate']}% with cumulative P&L at ${stats['total_pnl']:.2f}.",
        f"{len(context['recent_scans'])} recent scan runs were available for review.",
    ]
    not_working = []
    if context["recent_errors"]:
        not_working.append(f"{len(context['recent_errors'])} recent error log lines need review.")
    if context["recent_warnings"]:
        not_working.append(f"{len(context['recent_warnings'])} recent warning log lines indicate degraded dependencies.")
    if not not_working:
        not_working.append("No critical failures were detected in the sampled logs, but AI daily reporting was unavailable.")

    improvements = [
        "Reduce recurring warning and error noise by grouping root causes and adding remediation hints in logs.",
        "Add trend metrics for scan hit rate, trade open/close velocity, and strategy-level win rates.",
        "Keep a visible history of generated daily reports in the dashboard.",
        "Add explicit health summaries for cointegration, whale, weather, and copy-trading subsystems.",
        "Turn daily report improvement items into tracked implementation tasks automatically.",
    ]
    return {
        "summary": "AI daily report generation was unavailable, so this fallback summary was built from local metrics and logs.",
        "working": working,
        "not_working": not_working,
        "improvements": improvements,
        "confidence": "low",
        "model": "fallback",
    }


def _daily_report_context() -> dict:
    stats = db.get_stats()
    recent_scans = db.get_scan_runs(limit=10)
    open_trades = db.get_trades(status="open", limit=10)
    closed_trades = db.get_trades(status="closed", limit=10)
    whale_alerts = db.get_whale_alerts(limit=10, min_score=60)
    scanner_lines = _tail_lines(Path(__file__).parent / "logs" / "scanner.log", limit=120)
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "stats": stats,
        "recent_scans": recent_scans,
        "open_trades": open_trades,
        "closed_trades": closed_trades,
        "recent_whales": whale_alerts,
        "recent_errors": [line for line in scanner_lines if " ERROR " in line][-12:],
        "recent_warnings": [line for line in scanner_lines if " WARNING " in line][-12:],
        "recent_brain": [line for line in scanner_lines if "scanner.brain" in line][-12:],
    }


def _render_daily_report_markdown(report_date: str, context: dict, report: dict) -> str:
    working = "\n".join(f"- {item}" for item in report.get("working", []))
    def checkbox_list(items, checked=False):
        mark = "x" if checked else " "
        return "\n".join(f"- [{mark}] {item}" for item in items)
    not_working = checkbox_list(report.get("not_working", []))
    improvements = checkbox_list(report.get("improvements", []))
    return f"""# Daily Report - {report_date}

Generated at: {context['generated_at']}
Model: {report.get('model', 'unknown')}
Confidence: {report.get('confidence', 'unknown')}

## Summary
{report.get('summary', '')}

## Working
{working or '- None recorded'}

## Not Working
{not_working or '- None recorded'}

## Top 5 Improvements
{improvements or '1. None recorded'}
"""


def _latest_daily_report_path() -> Path | None:
    DAILY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    reports = sorted(DAILY_REPORTS_DIR.glob("*-daily-report.md"))
    return reports[-1] if reports else None


def _extract_report_date(path: Path) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})-daily-report\.md$", path.name)
    return match.group(1) if match else str(datetime.utcnow().date())


def _parse_daily_report_markdown(content: str) -> dict:
    report = {
        "summary": "",
        "working": [],
        "not_working": [],
        "improvements": [],
        "model": "unknown",
        "confidence": "unknown",
    }
    current = None
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if line.startswith("Model:"):
            report["model"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Confidence:"):
            report["confidence"] = line.split(":", 1)[1].strip()
            continue
        if line == "## Summary":
            current = "summary"
            continue
        if line == "## Working":
            current = "working"
            continue
        if line == "## Not Working":
            current = "not_working"
            continue
        if line == "## Top 5 Improvements":
            current = "improvements"
            continue
        if line.startswith("## "):
            current = None
            continue
        if not line:
            continue
        if current == "summary":
            report["summary"] = (report["summary"] + "\n" + line).strip()
        elif current in {"working", "not_working"}:
            match = re.match(r"-\s*\[(?: |x|X)\]\s*(.+)", line)
            if match:
                report[current].append(match.group(1).strip())
                continue
            if line.startswith("- "):
                report[current].append(line[2:].strip())
        elif current == "improvements":
            cleaned = re.sub(r"^\d+\.\s*", "", line).strip()
            match = re.match(r"-\s*\[(?: |x|X)\]\s*(.+)", cleaned)
            if match:
                candidate = match.group(1).strip()
            elif cleaned.startswith("- "):
                candidate = cleaned[2:].strip()
            else:
                candidate = cleaned
            if candidate:
                report["improvements"].append(candidate)
    return report


def _persist_report_items(report_date: str, report: dict) -> list[dict]:
    not_working = [item.strip() for item in report.get("not_working", []) if (item or "").strip()]
    improvements = [item.strip() for item in report.get("improvements", []) if (item or "").strip()]
    db.save_report_items(report_date, "not_working", not_working)
    db.save_report_items(report_date, "improvement", improvements)
    allowed = {("not_working", item) for item in not_working}
    allowed.update({("improvement", item) for item in improvements})
    return [
        item for item in db.get_report_items(report_date)
        if (item["section"], item["item_text"]) in allowed
    ]


def _visible_report_items(items: list[dict]) -> list[dict]:
    return [item for item in items if item.get("status") != "completed"]


def _write_action_item(
    path: Path,
    title: str,
    report_date: str,
    item_text: str,
    section_label: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else f"# {title}\n\n"
    section_header = f"## {section_label or report_date}"
    bullet = f"- {item_text}"
    if bullet in existing:
        return
    if section_header in existing:
        head, _, tail = existing.partition(section_header)
        lines = tail.splitlines()
        insert_at = len(lines)
        for idx in range(1, len(lines)):
            if lines[idx].startswith("## "):
                insert_at = idx
                break
        section_lines = lines[:insert_at]
        if section_lines and section_lines[-1].strip():
            section_lines.append("")
        section_lines.append(bullet)
        new_tail = "\n".join(section_lines + lines[insert_at:])
        content = head + section_header + new_tail
    else:
        content = existing.rstrip() + f"\n\n{section_header}\n{bullet}\n"
    path.write_text(content.rstrip() + "\n")


def _resolve_report_item_log_path(item: dict) -> Path | None:
    for attr in ("diagnosis_path", "action_path"):
        candidate = item.get(attr)
        if candidate:
            candidate_path = Path(candidate)
            if candidate_path.is_file():
                return candidate_path
    fallback = FIX_LOGS_DIR / f"{item['report_date']}-report-followups.md"
    if fallback.is_file():
        return fallback
    return None


def _log_snippet(path: Path, needle: str | None, window: int = 8) -> str:
    try:
        lines = path.read_text().splitlines()
    except Exception as exc:
        log.warning("Failed to read log snippet from %s: %s", path, exc)
        return ""
    if not lines:
        return ""
    focus = 0
    if needle:
        lower = needle.lower()
        for idx, line in enumerate(lines):
            if lower in line.lower():
                focus = idx
                break
    start = max(0, focus - window // 2)
    end = min(len(lines), start + window)
    snippet = lines[start:end]
    if start > 0:
        snippet.insert(0, "... (truncated) ...")
    if end < len(lines):
        snippet.append("... (truncated) ...")
    return "\n".join(snippet)


def _append_fix_log(item: dict) -> Path:
    report_date = item["report_date"]
    path = FIX_LOGS_DIR / f"{report_date}-report-followups.md"
    _write_action_item(path, "Report Follow-Ups", report_date, item["item_text"], section_label=report_date)
    return path


def _diagnosis_context(item: dict) -> dict:
    context = _daily_report_context()
    needle_words = [word.lower() for word in re.findall(r"[A-Za-z0-9_]{4,}", item["item_text"])][:8]
    relevant_logs = []
    for line in context["recent_errors"] + context["recent_warnings"] + context["recent_brain"]:
        lower = line.lower()
        if not needle_words or any(word in lower for word in needle_words):
            relevant_logs.append(line)
    context["relevant_logs"] = relevant_logs[:12]
    context["report_item"] = item["item_text"]
    context["report_section"] = item["section"]
    return context


def _write_diagnosis_log(item: dict) -> tuple[Path, str]:
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    path = DIAGNOSTICS_DIR / f"{datetime.utcnow().date()}-report-item-{item['id']}.md"
    context = _diagnosis_context(item)
    prompt = f"""You are diagnosing a Polymarket scanner issue from the daily report queue.

Issue:
{item['item_text']}

Context JSON:
{json.dumps(context, indent=2)}

Write a concise markdown note with these sections:
## Item
## Signals
## Likely Causes
## Next Checks
## Recommendation
Keep it concrete and operational."""
    analysis = brain.ask(prompt, model=brain.OPUS_MODEL)
    if analysis.startswith("Brain unavailable") or analysis.startswith("Brain error:"):
        analysis = (
            "## Item\n"
            f"{item['item_text']}\n\n"
            "## Signals\n"
            + ("\n".join(f"- {line}" for line in context["relevant_logs"]) or "- No matching log lines found in the latest sample.")
            + "\n\n## Likely Causes\n- Further review needed.\n\n## Next Checks\n- Inspect the latest logs and affected endpoint.\n\n## Recommendation\n- Keep this in the diagnostic queue until reproduced."
        )
    path.write_text(analysis.rstrip() + "\n")
    return path, analysis


def _save_pairs_scan_run(scan_result, duration):
    opportunities = scan_result["opportunities"]
    trial_settings = cointegration_trial.get_trial_settings()
    for opp in opportunities:
        cointegration_trial.annotate_opportunity(opp, mode="paper", settings=trial_settings)
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
        "raw_diverged_pairs": scan_result.get("raw_diverged_pairs", len(opportunities)),
        "admission_counts": scan_result.get("admission_counts", {}),
        "skip_counts": scan_result.get("skip_counts", {}),
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
    content = DASHBOARD_PATH.read_text()
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
    )


# --- Stats ---

@app.get("/api/stats")
async def stats():
    return db.get_stats()


@app.get("/api/paper-account")
async def paper_account():
    return db.get_paper_account_overview(refresh_unrealized=True)


@app.get("/api/paper-sizing")
async def paper_sizing(limit: int = 50):
    import paper_sizing as paper_sizing_module

    return {
        "settings": paper_sizing_module.get_sizing_settings(),
        "decisions": db.get_paper_sizing_decisions(limit=limit),
        "summary": db.get_paper_sizing_summary(limit=max(limit, 200)),
    }


@app.get("/api/paper-trade-attempts")
async def paper_trade_attempts(limit: int = 50):
    return _paper_trade_attempt_feed(limit=limit)


@app.get("/api/trades/monitor")
async def trade_monitor_status():
    return trade_monitor.get_flagged_open_trades()


@app.post("/api/trades/reconcile")
async def reconcile_trades(auto_remediate: bool = True):
    return trade_monitor.reconcile_open_trades(auto_remediate=auto_remediate)


@app.get("/api/cointegration/trial")
async def cointegration_trial_status():
    settings = cointegration_trial.get_trial_settings()
    summary = db.get_cointegration_trial_summary()
    recommendation = "keep_experimental"
    a_trial = summary["cohorts"]["a_trial"]
    a_plus = summary["cohorts"]["a_plus"]
    if a_trial["closed_trades"] >= 20:
        if (
            a_trial["realized_pnl"] > 0
            and a_trial["worst_mae_usd"] >= a_plus["worst_mae_usd"]
            and a_trial["regime_break_rate"] <= max(a_plus["regime_break_rate"], 20.0)
        ):
            recommendation = "consider_promoting"
        elif a_trial["realized_pnl"] <= 0 or a_trial["regime_break_rate"] > max(a_plus["regime_break_rate"] + 15.0, 35.0):
            recommendation = "reject"
    return {
        "trial_name": settings["trial_name"],
        "status": {
            "enabled": settings["enabled"],
            "paper_only": settings["paper_only"],
            "recommendation": recommendation,
        },
        "settings": settings,
        "summary": summary,
    }


@app.get("/api/reports/daily")
async def get_daily_report():
    latest = _latest_daily_report_path()
    if not latest:
        return {
            "exists": False,
            "content": "",
            "path": str(DAILY_REPORTS_DIR / f"{datetime.utcnow().date()}-daily-report.md"),
            "implementation_plan_path": str(IMPLEMENTATION_PLAN_PATH),
            "testing_ideas_path": str(TESTING_IDEAS_PATH),
        }
    report_date = _extract_report_date(latest)
    content = latest.read_text()
    report = _parse_daily_report_markdown(content)
    items = _visible_report_items(_persist_report_items(report_date, report))
    return {
        "exists": True,
        "content": content,
        "path": str(latest),
        "implementation_plan_path": str(IMPLEMENTATION_PLAN_PATH),
        "testing_ideas_path": str(TESTING_IDEAS_PATH),
        "report_date": report_date,
        "report": report,
        "items": items,
    }


@app.get("/reports/{filename}")
async def serve_daily_report_file(filename: str):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-daily-report\.md", filename):
        raise HTTPException(404, "Invalid report name")
    path = DAILY_REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Daily report not found")
    content = path.read_text()
    return PlainTextResponse(content, media_type="text/markdown")


@app.post("/api/reports/daily")
async def generate_daily_report():
    report_date = str(datetime.utcnow().date())
    DAILY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    context = _daily_report_context()
    report = brain.generate_daily_report(context) or _fallback_daily_report(context)
    report_path = DAILY_REPORTS_DIR / f"{report_date}-daily-report.md"
    report_content = _render_daily_report_markdown(report_date, context, report)
    report_path.write_text(report_content)
    items = _visible_report_items(_persist_report_items(report_date, report))

    return {
        "ok": True,
        "content": report_content,
        "path": str(report_path),
        "implementation_plan_path": str(IMPLEMENTATION_PLAN_PATH),
        "testing_ideas_path": str(TESTING_IDEAS_PATH),
        "report": report,
        "items": items,
    }


@app.post("/api/reports/items/{item_id}/fix-log")
async def add_report_item_to_fix_log(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    path = _append_fix_log(item)
    updated = db.update_report_item(
        item_id,
        status="queued",
        disposition="implement",
        action_path=str(path),
        notes="Logged to dated fix log for implementation follow-up.",
    )
    return {"ok": True, "item": updated, "path": str(path)}


@app.post("/api/reports/items/{item_id}/diagnose")
async def diagnose_report_item(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    path, content = _write_diagnosis_log(item)
    updated = db.update_report_item(
        item_id,
        status="needs_review",
        disposition="diagnose",
        diagnosis_path=str(path),
        notes="Diagnosis note generated from latest report context and logs.",
    )
    return {"ok": True, "item": updated, "path": str(path), "content": content}


@app.post("/api/reports/items/{item_id}/plan")
async def add_report_item_to_plan(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    _write_action_item(
        IMPLEMENTATION_PLAN_PATH,
        "Implementation Plan",
        item["report_date"],
        item["item_text"],
        section_label=f"{item['report_date']} Report Queue",
    )
    updated = db.update_report_item(
        item_id,
        status="planned",
        disposition="implement",
        action_path=str(IMPLEMENTATION_PLAN_PATH),
        notes="Promoted to implementation plan.",
    )
    return {"ok": True, "item": updated, "path": str(IMPLEMENTATION_PLAN_PATH)}


@app.post("/api/reports/items/{item_id}/testing")
async def add_report_item_to_testing(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    _write_action_item(
        TESTING_IDEAS_PATH,
        "Testing Ideas",
        item["report_date"],
        item["item_text"],
        section_label=f"{item['report_date']} Testing Queue",
    )
    updated = db.update_report_item(
        item_id,
        status="test_only",
        disposition="test_only",
        action_path=str(TESTING_IDEAS_PATH),
        notes="Saved as a testing-only candidate.",
    )
    return {"ok": True, "item": updated, "path": str(TESTING_IDEAS_PATH)}


@app.post("/api/reports/items/{item_id}/live-candidate")
async def mark_report_item_live_candidate(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    _write_action_item(
        IMPLEMENTATION_PLAN_PATH,
        "Implementation Plan",
        item["report_date"],
        item["item_text"],
        section_label=f"{item['report_date']} Report Queue",
    )
    updated = db.update_report_item(
        item_id,
        status="planned",
        disposition="live_candidate",
        action_path=str(IMPLEMENTATION_PLAN_PATH),
        notes="Marked as a live-testing candidate and added to the implementation plan.",
    )
    return {"ok": True, "item": updated, "path": str(IMPLEMENTATION_PLAN_PATH)}


@app.post("/api/reports/items/{item_id}/complete")
async def complete_report_item(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    updated = db.update_report_item(
        item_id,
        status="completed",
        notes="Completed and removed from the active review queue.",
    )
    return {"ok": True, "item": updated}


@app.get("/api/reports/items/{item_id}/log")
async def get_report_item_log(item_id: int):
    item = db.get_report_item(item_id)
    if not item:
        raise HTTPException(404, "Report item not found")
    path = _resolve_report_item_log_path(item)
    if not path:
        raise HTTPException(404, "Log file not found for this report item")
    snippet = _log_snippet(path, item.get("item_text"))
    return {
        "ok": True,
        "path": str(path),
        "snippet": snippet,
    }


# --- Signals ---

@app.get("/api/signals")
async def list_signals(limit: int = 50, status: str = None, include_rejected: bool = False):
    return db.get_signals(limit=limit, status=status, include_rejected=include_rejected)


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
    correction_mode: str = "shadow",
    intraday_observations_json: str | None = None,
    include_exact_temp: bool = False,
):
    """Queue the weather scan and return a persisted job id."""
    import weather_strategy

    if correction_mode not in {"shadow", "blend", "corrected"}:
        raise HTTPException(400, "correction_mode must be one of: shadow, blend, corrected")

    intraday_observations = None
    if intraday_observations_json:
        try:
            intraday_observations = json.loads(intraday_observations_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"Invalid intraday_observations_json: {exc}") from exc

    params = {
        "min_edge": min_edge,
        "min_liquidity": min_liquidity,
        "correction_mode": correction_mode,
        "include_exact_temp": include_exact_temp,
    }
    log.info(
        "Queued weather scan job: %s observations=%d",
        params,
        len(intraday_observations) if isinstance(intraday_observations, list) else len(intraday_observations or {}),
    )

    def work():
        t0 = time.time()
        opportunities, meta = weather_strategy.scan_weather_opportunities(
            verbose=False,
            intraday_observations=intraday_observations,
            **params,
        )
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
            "exact_temp_enabled": meta.get("exact_temp_enabled", False),
            "exact_temp_opportunities": meta.get("exact_temp_opportunities", 0),
            "results": opportunities,
        }

    return _start_job("weather_scan", params, work)


@app.get("/api/weather")
async def list_weather_signals(limit: int = 50, tradeable_only: bool = False):
    """Return recent weather-edge opportunities from the database."""
    return db.get_weather_signals(limit=limit, tradeable_only=tradeable_only)


# --- Locked Market Arb ---

@app.post("/api/scan/longshot")
async def run_longshot_scan(
    min_liquidity: float = 2000,
    min_ev_pct: float = 0.5,
):
    """Scan all active binary markets for longshot NO bias opportunities.

    Finds YES markets priced 3–15¢ where calibrated NO win rate exceeds the
    implied price, then scores maker BUY_NO limit orders by EV and Kelly.
    """
    import longshot_scanner
    t0 = time.time()
    log.info("Longshot scan started: min_liq=%.0f min_ev=%.2f%%", min_liquidity, min_ev_pct)

    try:
        opportunities, stats = longshot_scanner.scan(
            min_liquidity=min_liquidity,
            min_ev_pct=min_ev_pct,
            verbose=False,
        )
    except Exception as e:
        log.error("Longshot scan failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e), "opportunities": 0})

    duration = time.time() - t0

    saved_ids = []
    for opp in opportunities:
        try:
            row_id = db.save_longshot_signal(opp)
            saved_ids.append(row_id)
        except Exception as e:
            log.warning("Failed to save longshot signal: %s", e)

    tradeable = sum(1 for o in opportunities if o.get("tradeable"))
    log.info("Longshot scan complete: %d opps (%d tradeable) in %.1fs",
             len(opportunities), tradeable, duration)

    return {
        "opportunities": len(opportunities),
        "tradeable": tradeable,
        "saved_ids": saved_ids,
        "duration_secs": round(duration, 1),
        "stats": stats,
        "results": opportunities,
    }


@app.get("/api/longshot")
async def get_longshot_signals(limit: int = 50, tradeable_only: bool = False):
    """Fetch recent longshot scanner results."""
    signals = db.get_longshot_signals(limit=limit, tradeable_only=tradeable_only)
    return {"signals": signals, "count": len(signals)}


@app.post("/api/scan/near-certainty")
async def run_near_certainty_scan(
    min_liquidity: float = 5000,
    min_ev_pct: float = 0.10,
    use_brain: bool = True,
):
    """Scan for near-certain YES markets (85–99¢) with calibration edge.

    Calibrated data shows YES at 90¢ wins 91.5% (not 90%), giving a structural
    1.5pp edge. Brain validation via the configured AI provider filters out cases where doubt is
    legitimate. Net EV is positive even after 2% taker fee.
    """
    import near_certainty_scanner
    t0 = time.time()
    log.info("Near-certainty scan started: min_liq=%.0f min_ev=%.2f%% brain=%s",
             min_liquidity, min_ev_pct, use_brain)

    try:
        opportunities, stats = near_certainty_scanner.scan(
            min_liquidity=min_liquidity,
            min_ev_pct=min_ev_pct,
            use_brain=use_brain,
            verbose=False,
        )
    except Exception as e:
        log.error("Near-certainty scan failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e), "opportunities": 0})

    duration = time.time() - t0

    saved_ids = []
    for opp in opportunities:
        try:
            row_id = db.save_near_certainty_signal(opp)
            saved_ids.append(row_id)
        except Exception as e:
            log.warning("Failed to save near-certainty signal: %s", e)

    tradeable = sum(1 for o in opportunities if o.get("tradeable"))
    log.info("Near-certainty scan complete: %d opps (%d tradeable) in %.1fs",
             len(opportunities), tradeable, duration)

    return {
        "opportunities": len(opportunities),
        "tradeable": tradeable,
        "saved_ids": saved_ids,
        "duration_secs": round(duration, 1),
        "stats": stats,
        "results": opportunities,
    }


@app.get("/api/near-certainty")
async def get_near_certainty_signals(limit: int = 50, tradeable_only: bool = False):
    """Fetch recent near-certainty scanner results."""
    signals = db.get_near_certainty_signals(limit=limit, tradeable_only=tradeable_only)
    return {"signals": signals, "count": len(signals)}


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


# --- Whale / Insider Detection ---

_whale_status = {"running": False, "last_result": None}


def _run_whale_background(min_score, auto_trade=False):
    """Run whale scan in background thread — avoids blocking the event loop."""
    import whale_detector
    _whale_status["running"] = True
    t0 = time.time()
    try:
        alerts, stats = whale_detector.scan(min_score=min_score, verbose=True, auto_trade=auto_trade)
        saved_ids = []
        for alert in alerts:
            try:
                row_id = db.save_whale_alert(alert)
                if row_id:
                    saved_ids.append(row_id)
            except Exception as e:
                log.warning("Failed to save whale alert: %s", e)

        duration = round(time.time() - t0, 1)
        _whale_status["last_result"] = {
            "ok": True,
            "alerts": len(alerts),
            "new_saved": len(saved_ids),
            "trades_created": stats.get("trades_created", 0),
            "duration_secs": duration,
            "stats": stats,
        }
        log.info("Whale scan complete: %d alerts (%d new, %d trades) in %.1fs",
                 len(alerts), len(saved_ids), stats.get("trades_created", 0), duration)
    except Exception as e:
        log.error("Whale scan failed: %s", e)
        _whale_status["last_result"] = {"ok": False, "error": str(e)}
    finally:
        _whale_status["running"] = False


@app.post("/api/scan/whales")
async def run_whale_scan(min_score: int = 50, auto_trade: bool = False):
    """Kick off whale scan in background — returns immediately."""
    import threading
    if _whale_status["running"]:
        return {"ok": False, "error": "Whale scan already running — check Console tab for progress"}
    thread = threading.Thread(target=_run_whale_background, args=(min_score, auto_trade), daemon=True)
    thread.start()
    log.info("Whale scan triggered from dashboard (background, min_score=%d, auto_trade=%s)", min_score, auto_trade)
    return {"ok": True, "message": "Whale scan started — watch Console tab for progress"}


@app.get("/api/scan/whales/status")
async def whale_scan_status():
    """Check if whale scan is running and get last result."""
    return {"running": _whale_status["running"], "last_result": _whale_status["last_result"]}


@app.get("/api/whales")
async def list_whale_alerts(limit: int = 50, min_score: int = 0, undismissed_only: bool = False):
    """Return recent whale/insider alerts."""
    return db.get_whale_alerts(limit=limit, min_score=min_score, undismissed_only=undismissed_only)


@app.get("/api/whales/count")
async def whale_alert_count():
    """Count of new (undismissed) whale alerts in last 24h — used for popup badge."""
    return {"count": db.get_new_whale_count()}


@app.post("/api/whales/{alert_id}/dismiss")
async def dismiss_whale(alert_id: int):
    """Dismiss a whale alert."""
    db.dismiss_whale_alert(alert_id)
    return {"ok": True, "id": alert_id}


# --- Brain (AI provider) ---

@app.post("/api/brain/validate/{signal_id}")
async def brain_validate(signal_id: int):
    """Ask the configured AI provider to validate a signal before trading."""
    for s in db.get_signals(limit=500):
        if s["id"] == signal_id:
            should_trade, reasoning = brain.validate_signal(s)
            return {
                "signal_id": signal_id,
                "should_trade": should_trade,
                "reasoning": reasoning,
            }
    raise HTTPException(404, "Signal not found")


@app.get("/api/brain/runtime")
async def brain_runtime():
    """Return current brain-provider runtime status for safe migration checks."""
    try:
        return brain.get_runtime_status()
    except Exception as e:
        log.error("Failed to fetch brain runtime status: %s", e)
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/api/brain/whale/{alert_id}")
async def brain_validate_whale(alert_id: int):
    """Ask the configured AI provider to analyze a whale alert for trading opportunities."""
    alert = db.get_whale_alert_by_id(alert_id)
    if not alert:
        raise HTTPException(404, "Whale alert not found")
    result = brain.validate_whale(alert) or {}
    verdict = (result.get("verdict") or "").lower()
    reasoning = result.get("reasoning") or "Brain unavailable"
    risk_flags = result.get("risk_flags") or []
    if risk_flags:
        reasoning = f"{reasoning} Risk flags: {', '.join(risk_flags[:3])}"
    should_trade = verdict == "suspicious"
    return {
        "alert_id": alert_id,
        "should_trade": should_trade,
        "reasoning": reasoning,
        "verdict": verdict or "unavailable",
    }


@app.get("/api/copy/latest")
async def copy_latest_trades(limit: int = 5):
    return {"trades": db.get_latest_copy_trades(limit)}


@app.get("/api/copy/events")
async def copy_wallet_events(limit: int = 40, wallet: str | None = None):
    getter = getattr(db, "get_wallet_monitor_events", None)
    summary_getter = getattr(db, "get_wallet_monitor_event_summary", None)
    if not callable(getter) or not callable(summary_getter):
        return {
            "available": False,
            "degraded_reason": "wallet_monitor_event_api_missing",
            "events": [],
            "summary": {"available": False, "recent_count": 0, "status_counts": {}},
        }
    return {
        "available": True,
        "degraded_reason": None,
        "events": getter(limit=limit, wallet=wallet),
        "summary": summary_getter(limit=limit, wallet=wallet),
    }


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
    decision = db.inspect_pairs_trade_open(signal_id, size_usd=size_usd)
    if not decision["ok"]:
        _safe_record_paper_trade_attempt(
            source="manual_api",
            strategy="pairs",
            outcome="blocked",
            reason_code=decision["reason_code"],
            reason=decision["reason"],
            event=((decision.get("signal") or {}).get("event")),
            signal_id=signal_id,
            size_usd=size_usd,
            details={"path": "/api/trades"},
        )
        status_code = 404 if decision["reason_code"] == "signal_not_found" else 409
        if decision["reason_code"] == "insufficient_cash":
            status_code = 400
        return JSONResponse(
            status_code=status_code,
            content={
                "ok": False,
                "error": decision["reason"],
                "reason": decision["reason"],
                "reason_code": decision["reason_code"],
                "paper_account": decision.get("account"),
                "policy": {
                    "position_policy": decision.get("position_policy"),
                    "label": decision.get("position_policy_label"),
                    "detail": decision.get("position_policy_detail"),
                },
            },
        )
    trade_id = db.open_trade(signal_id, size_usd=size_usd)
    if not trade_id:
        _safe_record_paper_trade_attempt(
            source="manual_api",
            strategy="pairs",
            outcome="error",
            reason_code="open_failed",
            reason="Pairs trade could not be opened after preflight passed.",
            event=((decision.get("signal") or {}).get("event")),
            signal_id=signal_id,
            size_usd=size_usd,
            details={"path": "/api/trades"},
        )
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": "Pairs trade could not be opened.", "reason_code": "open_failed"},
        )
    _safe_record_paper_trade_attempt(
        source="manual_api",
        strategy="pairs",
        outcome="allowed",
        reason_code="opened",
        reason="Paper pairs trade opened.",
        event=((decision.get("signal") or {}).get("event")),
        signal_id=signal_id,
        trade_id=trade_id,
        size_usd=size_usd,
        details={"path": "/api/trades"},
    )
    return {
        "ok": True,
        "trade_id": trade_id,
        "status": "open",
        "trade_state_mode": db.TRADE_STATE_PAPER,
        "reconciliation_mode": db.RECONCILIATION_INTERNAL,
        "paper_account": db.get_paper_account_state(refresh_unrealized=True),
    }


@app.post("/api/trades/{trade_id}/close")
async def close_trade(trade_id: int, exit_price_a: float, exit_price_b: float = None, notes: str = ""):
    """Close a paper trade. exit_price_b is optional for single-leg (weather) trades."""
    pnl = db.close_trade(trade_id, exit_price_a, exit_price_b, notes)
    if pnl is None:
        raise HTTPException(404, "Trade not found")
    return {
        "trade_id": trade_id,
        "pnl": round(pnl, 2),
        "status": "closed",
        "paper_account": db.get_paper_account_state(refresh_unrealized=True),
    }


@app.post("/api/weather/{signal_id}/trade")
async def open_weather_trade(signal_id: int, size_usd: float = 20):
    """Open a paper trade from a weather signal."""
    signal = db.get_weather_signal_by_id(signal_id)
    result = execution.execute_weather_trade(signal or {"id": signal_id}, size_usd=size_usd, mode="paper")
    if not result["ok"]:
        decision = result.get("decision") or db.inspect_weather_trade_open(signal_id, size_usd=size_usd)
        _safe_record_paper_trade_attempt(
            source="manual_api",
            strategy="weather",
            outcome="blocked",
            reason_code=result.get("reason_code") or decision["reason_code"],
            reason=result.get("error") or decision["reason"],
            event=((decision.get("signal") or {}).get("event")),
            weather_signal_id=signal_id,
            token_id=decision.get("entry_token"),
            size_usd=size_usd,
            details={"path": "/api/weather/{signal_id}/trade"},
        )
        status_code = 404 if decision["reason_code"] == "signal_not_found" else 409
        if decision["reason_code"] == "insufficient_cash":
            status_code = 400
        return JSONResponse(
            status_code=status_code,
            content={
                "ok": False,
                "error": result.get("error") or decision["reason"],
                "reason": result.get("error") or decision["reason"],
                "reason_code": result.get("reason_code") or decision["reason_code"],
                "paper_account": decision.get("account"),
                "policy": {
                    "position_policy": decision.get("position_policy"),
                    "label": decision.get("position_policy_label"),
                    "detail": decision.get("position_policy_detail"),
                },
            },
        )
    _safe_record_paper_trade_attempt(
        source="manual_api",
        strategy="weather",
        outcome="allowed",
        reason_code="opened",
        reason="Paper weather trade opened.",
        event=((signal or {}).get("event")),
        weather_signal_id=signal_id,
        trade_id=result["trade_id"],
        token_id=((signal or {}).get("yes_token")),
        size_usd=size_usd,
        details={"path": "/api/weather/{signal_id}/trade"},
    )
    return {
        "ok": True,
        "trade_id": result["trade_id"],
        "signal_id": signal_id,
        "status": "open",
        "trade_state_mode": result["trade_state_mode"],
        "reconciliation_mode": result["reconciliation_mode"],
        "paper_account": db.get_paper_account_state(refresh_unrealized=True),
    }


@app.post("/api/whales/{alert_id}/trade")
async def open_whale_trade_endpoint(alert_id: int, size_usd: float = 20):
    """Open a paper trade from a whale alert."""
    log.info("Whale trade request for alert ID: %d", alert_id)
    try:
        balance_check = db.can_open_paper_trade(size_usd)
        if not balance_check["ok"]:
            raise HTTPException(
                400,
                f"Insufficient paper cash: ${balance_check['available_cash']:.2f} available, "
                f"${balance_check['requested_size_usd']:.2f} requested",
            )
        import whale_detector
        # Get alert from DB
        alerts = db.get_whale_alerts(limit=1000)
        alert = next((a for a in alerts if a["id"] == alert_id), None)
        if not alert:
            log.warning("Whale alert %d not found in last 1000 alerts", alert_id)
            raise HTTPException(404, f"Whale alert {alert_id} not found")

        trade_id = whale_detector.create_whale_trade(alert, size_usd=size_usd)
        if not trade_id:
            log.error("whale_detector.create_whale_trade returned None for alert %d", alert_id)
            raise HTTPException(500, "Failed to create whale trade")

        log.info("Whale trade opened: ID %d from alert %d", trade_id, alert_id)
        return {
            "ok": True,
            "trade_id": trade_id,
            "status": "open",
            "paper_account": db.get_paper_account_state(refresh_unrealized=True),
        }
    except Exception as e:
        log.error("Whale trade endpoint error: %s", e)
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(500, str(e))


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
        log.exception("Autonomy cycle failed in background thread: %s", e)
        _safe_record_paper_trade_attempt(
            source="autonomy_runner",
            strategy="system",
            outcome="error",
            reason_code="autonomy_cycle_failed",
            reason=f"Autonomy cycle failed: {e}",
            event="Autonomy cycle",
            details={"path": "/api/autonomy"},
        )
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
        ((t.get("copy_wallet") or "").lower(), db.get_trade_reconciliation_key(t))
        for t in db.get_trades(status="open", limit=500)
        if t.get("trade_type") == "copy" and db.get_trade_reconciliation_key(t) and t.get("copy_wallet")
    }
    out = []
    for row in db.get_watched_wallets(active_only=True):
        address, label = row["address"], row["label"]
        positions = copy_scanner.get_positions(address)
        for p in positions:
            identity = db.get_position_identity(p, wallet=address)
            p["mirrored"] = ((address or "").lower(), identity["canonical_ref"] or identity["condition_id"]) in mirrored
        value = copy_scanner.get_portfolio_value(address)
        out.append({
            "address": address,
            "label": label,
            "portfolio_usd": value,
            "positions": positions,
            "last_checked_at": row.get("last_checked_at"),
            "last_positions_count": row.get("last_positions_count"),
            "last_event_at": row.get("last_event_at"),
            "last_event_type": row.get("last_event_type"),
            "last_event_status": row.get("last_event_status"),
            "last_event_reason": row.get("last_event_reason"),
        })
    return out


@app.get("/api/copy/detached")
async def copy_detached_trades():
    """Return open copy trades whose source wallet is no longer actively watched."""
    detached = []
    for trade in db.get_trades(status="open", limit=500):
        if trade.get("trade_type") != "copy":
            continue
        if trade.get("copy_wallet_active") == 1:
            continue
        reason = trade.get("copy_wallet_reason") or ""
        if reason.startswith("manual_unwatch:"):
            status_label = "unwatched"
        elif reason:
            status_label = "inactive"
        else:
            status_label = "detached"
        detached.append({
            "trade_id": trade["id"],
            "wallet": trade.get("copy_wallet"),
            "label": trade.get("copy_label") or (trade.get("copy_wallet") or "")[:10] + "...",
            "event": trade.get("event"),
            "outcome": trade.get("copy_outcome") or trade.get("side_a"),
            "opened_at": trade.get("opened_at"),
            "size_usd": trade.get("size_usd"),
            "status_label": status_label,
            "reason": reason,
        })
    return {"trades": detached}


@app.get("/api/copy/settings")
async def get_copy_settings():
    return db.get_copy_trade_settings()


@app.post("/api/copy/settings")
async def update_copy_settings(
    cap_enabled: bool = False,
    per_wallet_cap: int | None = None,
    total_open_cap: int | None = None,
):
    def _normalize_cap(value):
        if value is None:
            return None
        value = int(value)
        return value if value > 0 else None

    settings = {
        "cap_enabled": bool(cap_enabled),
        "per_wallet_cap": _normalize_cap(per_wallet_cap),
        "total_open_cap": _normalize_cap(total_open_cap),
    }
    db.set_setting("copy_trade_limits", settings)
    return {"ok": True, "settings": db.get_copy_trade_settings()}


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
    copy_settings = db.get_copy_trade_settings()
    decision = db.inspect_copy_trade_open(
        wallet,
        pos,
        size_usd=size_usd,
        max_wallet_open=(copy_settings["per_wallet_cap"] if copy_settings["cap_enabled"] else None),
        max_total_open=(copy_settings["total_open_cap"] if copy_settings["cap_enabled"] else None),
    )
    if not decision["ok"]:
        _safe_record_paper_trade_attempt(
            source="manual_api",
            strategy="copy",
            outcome="blocked",
            reason_code=decision["reason_code"],
            reason=decision["reason"],
            event=pos.get("title"),
            token_id=pos.get("asset"),
            wallet=wallet,
            condition_id=condition_id,
            size_usd=size_usd,
            details={"path": "/api/copy/mirror"},
        )
        status_code = 400 if decision["reason_code"] == "insufficient_cash" else 409
        return JSONResponse(
            status_code=status_code,
            content={
                "ok": False,
                "error": decision["reason"],
                "reason": decision["reason"],
                "reason_code": decision["reason_code"],
                "paper_account": decision.get("account"),
                "copy_settings": copy_settings,
                "policy": {
                    "position_policy": decision.get("position_policy"),
                    "label": decision.get("position_policy_label"),
                    "detail": decision.get("position_policy_detail"),
                },
            },
        )
    trade_id = db.open_copy_trade(
        wallet,
        label,
        pos,
        size_usd=size_usd,
        max_wallet_open=(copy_settings["per_wallet_cap"] if copy_settings["cap_enabled"] else None),
        max_total_open=(copy_settings["total_open_cap"] if copy_settings["cap_enabled"] else None),
    )
    if trade_id is None:
        _safe_record_paper_trade_attempt(
            source="manual_api",
            strategy="copy",
            outcome="error",
            reason_code="open_failed",
            reason="Copy trade could not be opened after preflight passed.",
            event=pos.get("title"),
            token_id=pos.get("asset"),
            wallet=wallet,
            condition_id=condition_id,
            size_usd=size_usd,
            details={"path": "/api/copy/mirror"},
        )
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": "Copy trade could not be opened.", "reason_code": "open_failed"},
        )
    _safe_record_paper_trade_attempt(
        source="manual_api",
        strategy="copy",
        outcome="allowed",
        reason_code="opened",
        reason="Paper copy trade opened.",
        event=pos.get("title"),
        trade_id=trade_id,
        token_id=pos.get("asset"),
        wallet=wallet,
        condition_id=condition_id,
        size_usd=size_usd,
        details={"path": "/api/copy/mirror", "label": label, "outcome": pos.get("outcome")},
    )
    return {"ok": True, "trade_id": trade_id, "label": label,
            "market": pos.get("title"), "outcome": pos.get("outcome"),
            "price": pos.get("curPrice"), "size_usd": size_usd,
            "trade_state_mode": db.TRADE_STATE_WALLET,
            "reconciliation_mode": db.RECONCILIATION_WALLET,
            "canonical_ref": decision.get("canonical_ref"),
            "paper_account": db.get_paper_account_state(refresh_unrealized=True)}


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
    """Score a wallet address and get the brain recommendation."""
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
    record_wallet_event = getattr(db, "record_wallet_monitor_event", None)
    if callable(record_wallet_event):
        try:
            record_wallet_event(
                source="server",
                wallet=address,
                label=label,
                event_type="watch_added",
                status="watching",
                reason="Wallet added to watch list. First poll will set a baseline from current positions; only later positions can auto-mirror.",
            )
        except Exception as exc:
            log.warning("Watch-add event logging failed for %s: %s", address, exc)
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
    """Stop watching a wallet without force-closing its open copy trades."""
    address = address.lower()
    open_copy_remaining = sum(
        1
        for trade in db.get_trades(status="open", limit=500)
        if trade.get("trade_type") == "copy" and trade.get("copy_wallet") == address
    )
    removed = db.unwatch_wallet(address)
    if removed:
        log.info(
            "Copy wallet unwatched: address=%s open_copy_trades_remaining=%d",
            address,
            open_copy_remaining,
        )
    return {
        "ok": removed,
        "address": address,
        "action": "unwatched",
        "future_mirroring_stopped": removed,
        "open_copy_trades_remaining": open_copy_remaining,
        "close_policy": "Existing copy trades stay open until manually closed or risk/resolution rules close them.",
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting Polymarket Scanner on http://localhost:8899")
    uvicorn.run(app, host="0.0.0.0", port=8899)
