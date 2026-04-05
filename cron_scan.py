#!/usr/bin/env python3
"""Cron-triggered scan — runs scanner, saves to DB, prints summary.

Usage:
    python3 cron_scan.py              # default scan
    python3 cron_scan.py --strict     # z>2.0, p<0.05

Called by cron every 30 minutes. Logs to logs/scanner.log.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure we can import project modules
sys.path.insert(0, str(Path(__file__).parent))

from log_setup import init_logging
import logging
import runtime_config

init_logging()
log = logging.getLogger("scanner.cron")
runtime_config.log_runtime_status("cron_scan.py")

import db
import scanner
import cointegration_trial
import perplexity


def main():
    strict = "--strict" in sys.argv

    z_thresh = 2.0 if strict else 1.5
    p_thresh = 0.05 if strict else 0.10

    log.info("=== Cron scan started (z>%.1f p<%.2f) ===", z_thresh, p_thresh)
    t0 = time.time()

    try:
        scan_result = scanner.scan(
            z_threshold=z_thresh,
            p_threshold=p_thresh,
            min_liquidity=5000,
            interval="1w",
            verbose=False,
            include_stats=True,
        )
    except Exception as e:
        log.error("Cron scan failed: %s", e)
        return

    duration = time.time() - t0
    opportunities = scan_result["opportunities"]
    trial_settings = cointegration_trial.get_trial_settings()
    for opp in opportunities:
        cointegration_trial.annotate_opportunity(opp, mode="paper", settings=trial_settings)
        perplexity.annotate_profitable_candidate(opp)

    # Save scan run
    db.save_scan_run(
        pairs_tested=scan_result["pairs_tested"],
        cointegrated=scan_result["pairs_cointegrated"],
        opportunities=len(opportunities),
        duration=duration,
    )

    # Save signals
    new_ids = []
    for opp in opportunities:
        try:
            sid = db.save_signal(opp)
            new_ids.append(sid)
        except Exception as e:
            log.warning("Failed to save signal: %s", e)

    # Summary
    tradeable = [o for o in opportunities if o.get("tradeable")]
    grades = {}
    for o in opportunities:
        g = o.get("grade_label", "?")
        grades[g] = grades.get(g, 0) + 1

    log.info("=== Cron scan complete: %d signals (%d tradeable) in %.1fs ===",
             len(opportunities), len(tradeable), duration)
    log.info("Grades: %s", " ".join(f"{g}={n}" for g, n in sorted(grades.items())))

    if tradeable:
        log.info("TRADEABLE SIGNALS:")
        for o in tradeable:
            log.info("  [%s] z=%+.2f ev=%.1f%% | %s",
                     o["grade_label"], o["z_score"],
                     o.get("ev", {}).get("ev_pct", 0),
                     o.get("action", "")[:60])


if __name__ == "__main__":
    main()
