#!/usr/bin/env python3
"""Run the intraday weather correction backtest on a JSON sample file."""

import argparse
import json
from pathlib import Path

from log_setup import init_logging
import runtime_config
import weather_correction

init_logging()
runtime_config.log_runtime_status("weather_backtest.py")


def main():
    parser = argparse.ArgumentParser(description="Backtest intraday weather correction samples")
    parser.add_argument(
        "--samples",
        default="tests/fixtures/weather_intraday_backtest.json",
        help="Path to JSON sample file",
    )
    args = parser.parse_args()

    sample_path = Path(args.samples)
    with sample_path.open("r", encoding="utf-8") as handle:
        samples = json.load(handle)

    result = weather_correction.evaluate_intraday_correction(samples)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
