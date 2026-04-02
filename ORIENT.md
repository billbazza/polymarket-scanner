# Polymarket Scanner — Orientation

## What is this?
A statistical arbitrage system for Polymarket prediction markets. It scans for pairs of related markets (e.g., "Will X happen by June?" and "Will X happen by December?"), tests if they're cointegrated (statistically linked), and flags when the price spread diverges beyond normal bounds — a mean-reversion trading opportunity.

The system runs locally on your Mac with a LaunchAgent scanning every 30 minutes. Results accumulate in a SQLite database and are viewable through a web dashboard.

## Mental Model

```
Markets → Pairs → Cointegration Test → Z-Score → Score (A+ to F) → Trade?
                                                        ↓
                                              AI Brain (optional)
                                              validates fundamentals
```

The scanner is the **quantitative layer** (pure math, no opinions). The brain is the **qualitative layer** (does this divergence make fundamental sense, or is it just noise?). Both must agree before a trade is "tradeable."

## Codebase Shape

**Data flow**: API clients (`api.py`, `async_api.py`) → Scanner (`scanner.py`, `async_scanner.py`) → Math engine (`math_engine.py`) → Database (`db.py`)

**AI layer**: `brain.py` calls the configured provider for probability estimates and validation. `bayes.py` updates reversion probabilities based on the brain assessment. Prompts live in `/prompts/` with version tracking.

**Execution**: `execution.py` handles paper and live trading. `blockchain.py` wraps web3/Polygon interactions. `tracker.py` monitors open positions.

**Math**: `math_engine.py` (EV, Kelly, slippage, scoring), `returns.py` (log returns, Sharpe), `bayes.py` (Bayesian updating)

**Interface**: `server.py` (FastAPI on :8899), `dashboard.html` (single-file dark-theme UI), `scan.py` (CLI)

**Automation**: `cron_scan.py` (triggered by LaunchAgent every 30min), `analysis.py` (reporting)

**Infrastructure**: `log_setup.py` (rotating logs), `deploy/` (systemd service files for VPS)

## Most Common Operations

**Run a scan**: Click "Fast Scan" on the dashboard, or `python3 scan.py --top 5`

**Check what's happening**: `tail -f logs/scanner.log` or visit http://localhost:8899

**Paper trade a signal**: Click "Trade" next to a signal on the dashboard

**Analyze historical performance**: `python3 analysis.py`

**Study top traders**: `python3 leaderboard.py --top 10`

**Restart automated scanning**:
```bash
launchctl unload ~/Library/LaunchAgents/com.polymarket.scanner.plist
launchctl load ~/Library/LaunchAgents/com.polymarket.scanner.plist
```

## Known Weirdness

- **Sync scan takes ~2 minutes** because it fetches price histories sequentially. Use the "Fast Scan" button (async) which parallelizes and takes ~20 seconds.

- **Scanner.db grows over time** with signals from every scan. Old signals aren't auto-cleaned. If it gets big, you can safely delete signals older than 30 days.

- **The brain module returns None** if no configured provider is available. All downstream code handles this gracefully — it just skips the AI validation step.

- **Grades are relative to the scoring filters**, not absolute quality. A grade "A+" means all 5 filters passed (EV, Kelly, z-score, cointegration, half-life), not that it's guaranteed profitable.

- **The Gamma API sometimes returns stale data** or rejects certain query parameters. The error recovery catches this and moves on.

- **LaunchAgent runs even when laptop is asleep** — it'll fire when you wake up if it missed its window.

## Key Links
- Dashboard: http://localhost:8899
- Polymarket CLOB API docs: https://docs.polymarket.com/trading/overview
- py-clob-client SDK: https://github.com/Polymarket/py-clob-client
- Log file: logs/scanner.log
- Cron log: logs/cron.log
