#!/usr/bin/env python3
"""Polymarket cointegration scanner — find mispriced pairs.

Usage:
    python scan.py                          # default scan
    python scan.py --strict                 # stricter thresholds
    python scan.py --interval 1m            # longer history window
    python scan.py --top 5                  # show top 5 only
    python scan.py --min-liquidity 10000    # higher liquidity filter
"""
from dotenv import load_dotenv
load_dotenv()

import argparse
import json
import sys
from datetime import datetime

from log_setup import init_logging
from scanner import scan, format_opportunity

init_logging()


def main():
    parser = argparse.ArgumentParser(description="Polymarket cointegration scanner")
    parser.add_argument("--z-threshold", type=float, default=1.5,
                        help="Min |z-score| to flag (default: 1.5)")
    parser.add_argument("--p-threshold", type=float, default=0.10,
                        help="Max cointegration p-value (default: 0.10)")
    parser.add_argument("--min-liquidity", type=float, default=5000,
                        help="Min event liquidity in USD (default: 5000)")
    parser.add_argument("--interval", type=str, default="1w",
                        help="Price history window: 1d, 1w, 1m (default: 1w)")
    parser.add_argument("--fidelity", type=int, default=100,
                        help="Number of price data points (default: 100)")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N results (default: 10)")
    parser.add_argument("--strict", action="store_true",
                        help="Strict mode: z>2.0, p<0.05")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress progress output")
    args = parser.parse_args()

    if args.strict:
        args.z_threshold = 2.0
        args.p_threshold = 0.05

    print(f"Polymarket Cointegration Scanner")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Settings: z>{args.z_threshold} p<{args.p_threshold} "
          f"liq>${args.min_liquidity:,.0f} interval={args.interval}")
    print()

    opportunities = scan(
        z_threshold=args.z_threshold,
        p_threshold=args.p_threshold,
        min_liquidity=args.min_liquidity,
        interval=args.interval,
        fidelity=args.fidelity,
        verbose=not args.quiet,
    )

    if args.json:
        # JSON output for piping to other tools
        out = []
        for opp in opportunities[:args.top]:
            out.append({k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                        for k, v in opp.items()})
        print(json.dumps(out, indent=2))
        return

    if not opportunities:
        print("\nNo opportunities found with current thresholds.")
        print("Try: --z-threshold 1.0 or --p-threshold 0.15 for looser criteria")
        return

    print(f"\n{'#'*60}")
    print(f"  TOP {min(args.top, len(opportunities))} OPPORTUNITIES")
    print(f"{'#'*60}\n")

    for i, opp in enumerate(opportunities[:args.top], 1):
        print(format_opportunity(opp, rank=i))
        print()

    # Summary
    print(f"\nTotal opportunities: {len(opportunities)}")
    avg_z = sum(abs(o["z_score"]) for o in opportunities) / len(opportunities)
    print(f"Average |z-score|: {avg_z:.2f}")

    # Risk warning
    print(f"\n{'='*60}")
    print("PAPER TRADE FIRST. This scanner finds statistical patterns,")
    print("not guaranteed profits. Spreads can widen before reverting.")
    print(f"{'='*60}")


if __name__ == "__main__":
    # Handle numpy import for JSON serialization
    try:
        import numpy as np
    except ImportError:
        pass
    main()
