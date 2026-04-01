# 2026-04-01 Open Trade Policy And Weather Blocking Fix Log

## Source
- Open-trade audit for the autonomy loop, weather openings, stale accounting, and dashboard/API counts.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/AGENTS.md).

## Findings
- `autonomy.py` hard-coded `paper.max_open = 25`, which contradicted the repo policy that paper mode should not impose a separate position-count cap.
- `tracker.py` and several autonomy paths called `db.get_trades(status="open")` with the default limit of `50`, so refresh, auto-close, and dedup/accounting logic silently ignored older open trades once the book grew past 50 rows.
- Repeated weather scans kept saving fresh `weather_signals` rows for the same outcome token. Autonomy then skipped openings through duplicate suppression, but the weather API/dashboard exposed those rows as fresh tradeable signals without showing why they were blocked.
- The manual weather-trade endpoint only surfaced generic errors, so operators could not distinguish missing signals, duplicate-token suppression, and paper-cash exhaustion.

## Fixes Applied
- Updated [autonomy.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/autonomy.py) so paper mode no longer enforces a hard `max_open` cap; paper openings are now constrained by bankroll/cash rather than an arbitrary position count.
- Updated [autonomy.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/autonomy.py) and [tracker.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/tracker.py) to read all open trades with `limit=None`, fixing stale open-trade refresh, auto-close, and dedup/accounting behavior once more than 50 trades exist.
- Added `db.inspect_weather_trade_open()` in [db.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/db.py) to return a structured weather opening decision with explicit reason codes for `signal_not_found`, `signal_already_open`, `token_already_open`, `max_open_reached`, and `insufficient_cash`.
- Updated [db.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/db.py) so `open_weather_trade()` uses the centralized opening inspector, which now blocks duplicate opens on the same weather token at the DB layer instead of only in autonomy.
- Updated [db.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/db.py) so `get_weather_signals()` annotates each signal with `can_open_trade`, `open_trade_id`, `blocking_reason`, and `blocking_reason_code`, allowing the UI and API to reflect operational status instead of only math tradeability.
- Updated [server.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/server.py) so the weather-trade endpoint returns the specific blocking reason with an appropriate HTTP status instead of a generic “not found or already traded” failure.
- Updated [dashboard.html](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/dashboard.html) so the weather tab shows blocking reasons for tradeable-but-blocked rows and the open-trades tab fetches up to 500 rows instead of silently truncating at 50.
- Added targeted regression coverage in [test_all.py](/Users/will/.cline/worktrees/05fbc/polymarket-scanner/test_all.py) for uncapped paper autonomy config, duplicate weather-token blocking visibility, and `get_trades(limit=None)` returning every open row.

## Verification
- `python3 -m py_compile autonomy.py tracker.py db.py server.py test_all.py`
- `python3 test_all.py`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
