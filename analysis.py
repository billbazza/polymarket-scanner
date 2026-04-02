#!/usr/bin/env python3
"""Signal analysis and reporting — historical scan data from SQLite.

Usage:
    python3 analysis.py          # print full report
"""
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from log_setup import init_logging

init_logging()
log = logging.getLogger("scanner.analysis")

import db


def signal_summary():
    """Count signals by grade, tradeable %, avg z-score."""
    conn = db.get_conn()
    rows = conn.execute("SELECT grade_label, tradeable, z_score FROM signals").fetchall()
    conn.close()

    if not rows:
        return {"total": 0, "by_grade": {}, "tradeable_pct": 0, "avg_z_score": 0}

    total = len(rows)
    grades = Counter(r["grade_label"] or "?" for r in rows)
    tradeable = sum(1 for r in rows if r["tradeable"])
    avg_z = sum(abs(r["z_score"]) for r in rows) / total

    result = {
        "total": total,
        "by_grade": dict(grades.most_common()),
        "tradeable_pct": round(tradeable / total * 100, 1),
        "avg_z_score": round(avg_z, 2),
    }
    log.debug("Signal summary: %d total, %.1f%% tradeable, avg |z|=%.2f",
              total, result["tradeable_pct"], avg_z)
    return result


def grade_distribution():
    """Count how many A+, A, B, C, D, F signals exist."""
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT grade_label, COUNT(*) as cnt FROM signals GROUP BY grade_label ORDER BY cnt DESC"
    ).fetchall()
    conn.close()

    dist = {r["grade_label"] or "?": r["cnt"] for r in rows}
    log.debug("Grade distribution: %s", dist)
    return dist


def best_events(limit=10):
    """Which events produce the most signals."""
    conn = db.get_conn()
    rows = conn.execute("""
        SELECT event, COUNT(*) as signal_count,
               AVG(ABS(z_score)) as avg_z,
               SUM(tradeable) as tradeable_count
        FROM signals
        GROUP BY event
        ORDER BY signal_count DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    events = []
    for r in rows:
        events.append({
            "event": r["event"],
            "signal_count": r["signal_count"],
            "avg_z_score": round(r["avg_z"], 2),
            "tradeable_count": r["tradeable_count"],
        })
    log.debug("Best events: %d events returned", len(events))
    return events


def scan_performance(limit=50):
    """Scan frequency, avg duration, signal trend over time."""
    conn = db.get_conn()
    runs = conn.execute(
        "SELECT * FROM scan_runs ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    if not runs:
        return {"total_scans": 0, "avg_duration": 0, "avg_opportunities": 0, "recent": []}

    durations = [r["duration_secs"] for r in runs if r["duration_secs"] is not None]
    opps = [r["opportunities"] for r in runs if r["opportunities"] is not None]

    # Calculate scan frequency (avg time between scans)
    timestamps = sorted(r["timestamp"] for r in runs)
    intervals = []
    for i in range(1, len(timestamps)):
        intervals.append(timestamps[i] - timestamps[i - 1])
    avg_interval_min = (sum(intervals) / len(intervals) / 60) if intervals else 0

    result = {
        "total_scans": len(runs),
        "avg_duration": round(sum(durations) / len(durations), 1) if durations else 0,
        "avg_opportunities": round(sum(opps) / len(opps), 1) if opps else 0,
        "avg_interval_min": round(avg_interval_min, 1),
        "recent": [
            {
                "time": datetime.fromtimestamp(r["timestamp"]).strftime("%Y-%m-%d %H:%M"),
                "opportunities": r["opportunities"],
                "duration": round(r["duration_secs"], 1) if r["duration_secs"] else 0,
            }
            for r in runs[:10]
        ],
    }
    log.debug("Scan performance: %d scans, avg %.1fs, avg %.1f opps",
              result["total_scans"], result["avg_duration"], result["avg_opportunities"])
    return result


def strategy_performance():
    """Strategy-level attribution and paper-utilization audit."""
    result = db.get_strategy_performance(refresh_unrealized=False)
    log.debug(
        "Strategy performance: %d strategies, paper committed=$%.2f, net=$%.2f",
        len(result.get("strategies", [])),
        result.get("total_committed_capital", 0.0),
        result.get("total_realized_pnl", 0.0) + result.get("total_unrealized_pnl", 0.0),
    )
    return result


def print_report():
    """Print a formatted analysis report."""
    print()
    print("=" * 60)
    print("  POLYMARKET SCANNER ANALYSIS REPORT")
    print("=" * 60)

    # Signal Summary
    summary = signal_summary()
    print(f"\n--- Signal Summary ---")
    print(f"  Total signals:   {summary['total']}")
    print(f"  Tradeable:       {summary['tradeable_pct']}%")
    print(f"  Avg |z-score|:   {summary['avg_z_score']}")

    # Grade Distribution
    grades = grade_distribution()
    print(f"\n--- Grade Distribution ---")
    if grades:
        for grade, count in sorted(grades.items()):
            bar = "#" * min(count, 40)
            print(f"  {grade or '?':>3}: {count:>5}  {bar}")
    else:
        print("  No signals recorded yet.")

    # Best Events
    events = best_events()
    print(f"\n--- Top Events by Signal Count ---")
    if events:
        print(f"  {'Event':<36}{'Signals':>8}{'Avg |z|':>10}{'Trade':>8}")
        print(f"  {'-' * 62}")
        for e in events:
            name = e["event"][:35]
            print(f"  {name:<36}{e['signal_count']:>8}{e['avg_z_score']:>10.2f}{e['tradeable_count']:>8}")
    else:
        print("  No event data yet.")

    # Scan Performance
    perf = scan_performance()
    print(f"\n--- Scan Performance ---")
    print(f"  Total scans:        {perf['total_scans']}")
    print(f"  Avg duration:       {perf['avg_duration']}s")
    print(f"  Avg opportunities:  {perf['avg_opportunities']}")
    print(f"  Avg interval:       {perf['avg_interval_min']} min")

    if perf["recent"]:
        print(f"\n  Recent scans:")
        for r in perf["recent"][:5]:
            print(f"    {r['time']}  opps={r['opportunities']}  dur={r['duration']}s")

    strategy = strategy_performance()
    print(f"\n--- Strategy Performance ---")
    print(f"  Scope:              {strategy.get('reporting_scope', 'unknown')}")
    print(f"  Paper capital used: ${strategy.get('total_committed_capital', 0):.2f}")
    print(f"  Realized P&L:       ${strategy.get('total_realized_pnl', 0):.2f}")
    print(f"  Unrealized P&L:     ${strategy.get('total_unrealized_pnl', 0):.2f}")
    dq = strategy.get("data_quality", {})
    print(
        "  Data quality:       "
        f"inferred states={dq.get('trade_state_inferred_trades', 0)}  "
        f"missing marks={dq.get('open_trades_missing_marks', 0)}  "
        f"external open excluded from paper util={dq.get('external_open_trades_excluded_from_paper_utilization', 0)}"
    )
    rows = strategy.get("strategies", [])
    if rows:
        print(f"\n  {'Strategy':<16}{'Net':>10}{'Paper Util':>14}{'Open':>8}{'Coverage':>14}")
        print(f"  {'-' * 62}")
        for row in rows:
            coverage = "clean" if row.get("data_quality_status") == "ok" else "warning"
            print(
                f"  {row.get('label', row.get('strategy', '?')):<16}"
                f"{row.get('net_pnl', 0):>10.2f}"
                f"{(str(row.get('bankroll_utilization_pct', 0)) + '%'):>14}"
                f"{row.get('open_trades', 0):>8}"
                f"{coverage:>14}"
            )

    print()
    print("=" * 60)
    print()


def main():
    log.info("Running analysis report...")
    print_report()
    log.info("Analysis report complete.")


if __name__ == "__main__":
    main()
