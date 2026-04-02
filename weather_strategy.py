"""Shared weather strategy runner.

Keeps the existing threshold scanner as the default path and layers new
sub-strategies behind explicit enable flags.
"""

import logging

import weather_exact_temp_scanner
import weather_scanner

log = logging.getLogger("scanner.weather_strategy")


def scan_weather_opportunities(
    min_edge=weather_scanner.MIN_EDGE,
    min_liquidity=weather_scanner.MIN_LIQUIDITY,
    verbose=True,
    intraday_observations=None,
    correction_mode="shadow",
    include_exact_temp=False,
):
    """Run the default threshold scanner and optional exact-temp sub-strategy."""
    threshold_opps, threshold_meta = weather_scanner.scan(
        min_edge=min_edge,
        min_liquidity=min_liquidity,
        verbose=verbose,
        intraday_observations=intraday_observations,
        correction_mode=correction_mode,
    )
    exact_opps = []
    exact_meta = {
        "enabled": False,
        "markets_checked": 0,
        "exact_temp_events": 0,
        "tradeable": 0,
    }
    if include_exact_temp:
        exact_opps, exact_meta = weather_exact_temp_scanner.scan(
            min_edge=min_edge,
            min_liquidity=min_liquidity,
            verbose=verbose,
        )

    opportunities = threshold_opps + exact_opps
    opportunities.sort(
        key=lambda item: (
            not item.get("tradeable"),
            -abs(item.get("selected_edge", item.get("combined_edge", 0))),
        )
    )
    meta = {
        **threshold_meta,
        "threshold_opportunities": len(threshold_opps),
        "exact_temp_enabled": bool(exact_meta.get("enabled")),
        "exact_temp_opportunities": len(exact_opps),
        "exact_temp_tradeable": exact_meta.get("tradeable", 0),
        "exact_temp_markets_checked": exact_meta.get("markets_checked", 0),
        "opportunities": len(opportunities),
        "tradeable": sum(1 for item in opportunities if item.get("tradeable")),
    }
    log.info(
        "Weather strategy scan complete: threshold=%d exact_temp=%d total=%d tradeable=%d",
        len(threshold_opps),
        len(exact_opps),
        len(opportunities),
        meta["tradeable"],
    )
    return opportunities, meta
