# Polymarket Scanner — Claude Code Contract

## What This Is
Multi-strategy scanner for Polymarket prediction markets. Three live strategies:
1. **Cointegration pairs** — finds diverged spreads between correlated markets, trades mean-reversion
2. **Weather edge** — compares NOAA + Open-Meteo forecasts vs market prices on temperature bucket markets (seasonal — markets appear in summer/autumn)
3. **Locked arb** — flags markets where YES+NO < $1 (alerting only; needs near-atomic fills to exploit)

Scores opportunities through math filters (EV, Kelly, slippage), optionally validates with the AI brain layer, and supports paper + live trading.

## IMPORTANT: Agent Instructions
You are working in the Polymarket Scanner project. 
ALWAYS read and strictly follow the full guidelines in this AGENTS.md file before starting any task, making changes, or reviewing code. 
Reference specific sections (Architecture, Rules, Never Do, Always Do, etc.) in your reasoning.

## Scope Of This File
This file is a repo-level guidance document for coding agents and humans working in this project.
It is not automatically enforced by Codex at runtime unless the agent or workflow explicitly opens and follows it.
`GEMINI.md` serves the same purpose for this repo.

## Architecture

```
Entry points:  server.py (:8899)  |  scan.py (CLI)  |  autonomy.py (30-min launchd loop)
                    |                    |                     |
Scanners:      scanner.py / async_scanner.py (cointegration pairs)
               weather_scanner.py (NOAA+Open-Meteo vs market price)
               locked_scanner.py (YES+NO < $1 arb detection)
                    |
Math:          math_engine.py (EV, Kelly, slippage, scoring)
                    |
AI:            brain.py (provider-backed probability estimation/validation) → bayes.py (updating)
                    |
Execution:     execution.py (paper/live trading) → blockchain.py (web3/Polygon)
                    |
Persistence:   db.py (SQLite) → scanner.db
               Tables: signals, trades, snapshots, scan_runs, weather_signals, locked_arb
               Runtime state: logs/autonomy_state.json (local only; legacy root file auto-migrates)
                    |
Monitoring:    log_setup.py → logs/scanner.log + logs/journal.jsonl (trade audit trail)
```

### Trade Types
- **pairs** (two-leg): `entry_price_a/b`, `side_a/b`, linked to `signals` table. Auto-closes when |z| < 0.5 or price resolves.
- **weather** (single-leg): `entry_price_a` only, `token_id_a` stored on trade. Auto-closes when price ≥ 0.99 (WIN) or ≤ 0.01 (LOSS). `signal_id=NULL`, `weather_signal_id` foreign key instead.

### Autonomy Loop (every 30 min via launchd)
`autonomy.py` levels: `scout` (scan only) → `paper` (auto paper-trade A+ signals) → `penny` (real $1-5) → `book` (Kelly-sized). Each cycle: scan pairs → scan weather → refresh open trades → auto-close reverted/resolved trades → open new trades up to `max_open`.

## Key Conventions

### Module Pattern
Every module follows: docstring → imports → `log = logging.getLogger("scanner.<name>")` → functions. Entry points call `init_logging()` and `load_dotenv()`. Library modules do neither.

### API Clients
- `api.py` — synchronous (requests), used by scanner.py and cron_scan.py
- `async_api.py` — async (httpx), used by async_scanner.py and server.py fast scan
- Both have identical function signatures. Both retry on connection errors.

### Scoring Pipeline
Every opportunity flows: scanner finds pair → `math_engine.score_opportunity()` grades A+ to F → optionally `brain.validate_signal()` for AI validation → `execution.execute_trade()` if tradeable.

### Trading Modes
- **Paper** (default): simulates against current prices, tracks in SQLite. There are no limits on numbers of trades while in paper mode. Autonomy now derives `mode="paper"` explicitly from the normalized `level` string so paper-level cointegration trades never invoke `brain.validate_signal()`; the system logs a `brain_validation_skipped` journal entry for each admitted A+ cohort signal, making the math-only trade path auditable while still enforcing slippage and balance checks.
- **Live**: requires `POLYMARKET_PRIVATE_KEY` in `.env`, uses py-clob-client

### Database
SQLite at `scanner.db`. Schema auto-migrates on import via `db.init_db()`. New columns added via ALTER TABLE with try/except. `get_trades()`/`get_trade()` use LEFT JOIN to both `signals` and `weather_signals` so both trade types are returned correctly.

## Rules

### Never Do
- Hardcode API keys or private keys anywhere. Always use `.env` via python-dotenv.
- Use `eval()` for JSON parsing. Always `json.loads()`.
- Skip error recovery on API calls. Every external call needs try/except.
- Commit `.env` or `scanner.db` to git.
- Commit code with exposed tokens or API keys
- Place real money trades without explicit user confirmation.
- Use FOK (Fill or Kill) orders — use GTC (Good Till Cancelled) instead.

### Always Do
- Log every trading decision with timestamp (the log file is the audit trail).
- Log bug-fix work in a dated markdown file under `fix_logs/` and update that file when behavior changes.
- Route Daily Report follow-ups explicitly: `Not Working` items can be logged to `fix_logs/` or `reports/diagnostics/`, and improvements should only go to `implementation-plan.md` or `testing-ideas.md` when intentionally promoted.
- Store daily reports directly within `reports/` as `YYYY-MM-DD-daily-report.md` files with checkbox-style bullets so the UI can show tickable items and the new “Check Log” action can point to the fix/diagnostic entry.
- Keep the daily-report workflow consolidated: always rewrite the single `reports/YYYY-MM-DD-daily-report.md` file per date, rely on the dashboard notice instead of separate needed/create buttons, and treat `fix_logs/` or `reports/diagnostics/` as the only follow-up sinks.
- Update `CLAUDE.md` and `GEMINI.md` and `AGENTS.md` if the bug-fix logging process or location changes.
- keep `CLAUDE.md` and `GEMINI.md` and `AGENTS.md` in sync and always updates all three files if changes are made.
- Cap Kelly fraction at 0.25 (quarter-Kelly). Full Kelly is too aggressive.
- Check slippage before any trade. Skip if >2%.
- Check balance before any live trade.
- Return structured dicts from functions (not bare values).
- Degrade gracefully when optional services are unavailable (AI providers, web3, Telegram).

## Strategy Guardrails

- Whale trades now auto-close as soon as a single position loses more than $15, has been held over 48h, or suffers a 15% adverse move, and the tracker logs an aggregate drawdown alert whenever open whale PnL drops below -$50 so the prior $-54.52 incident surfaces in `logs/scanner.log` with a dedicated warning.

### Testing Changes
```bash
# Verify all imports work
python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"

# Run a CLI scan
python3 scan.py --top 3

# Start server and check dashboard
python3 server.py  # then visit http://localhost:8899

# Check cron is working
tail -f logs/cron.log

# Run analysis report
python3 analysis.py
```

### Common Operations
```bash
# Manual scan via API
curl -X POST http://localhost:8899/api/scan

# Fast (async) scan
curl -X POST http://localhost:8899/api/scan/fast

# Check signals
curl http://localhost:8899/api/signals?limit=5

# Brain-validate a signal
curl -X POST http://localhost:8899/api/brain/validate/42

# Check staged brain-provider runtime / cutover readiness
curl http://localhost:8899/api/brain/runtime

# Open paper trade (pairs)
curl -X POST "http://localhost:8899/api/trades?signal_id=42&size_usd=100"

# Open paper trade (weather)
curl -X POST "http://localhost:8899/api/weather/7/trade"

# Weather scan
curl -X POST http://localhost:8899/api/scan/weather

# System stats
curl http://localhost:8899/api/stats

# Restart cron scanning
launchctl unload ~/Library/LaunchAgents/com.polymarket.scanner.plist
launchctl load ~/Library/LaunchAgents/com.polymarket.scanner.plist
```

## Situational Guides

- When modifying the scoring pipeline → read `guides/scoring.md`
- When adding new API endpoints → read `guides/api-patterns.md`
- When debugging scan failures → check `logs/scanner.log` first, then `logs/cron.log`

## Environment Variables
All in `.env` (see `.env.example`):
- `BRAIN_PROVIDER` — `auto` prefers Anthropic while credits remain, then falls forward to OpenAI and finally xAI/Grok; `anthropic`, `openai`, or `xai` pins the provider.
- `ANTHROPIC_API_KEY` — enables Anthropic as the current/default brain provider
- `OPENAI_API_KEY` — enables OpenAI/Codex as warm standby or cutover provider for brain.py
- `XAI_API_KEY` — enables xAI/Grok as an additional fallback brain provider
- `OPENAI_BASE_URL` — optional OpenAI-compatible base URL override for Codex/OpenAI testing or cutover
- `XAI_BASE_URL` — optional Grok/xAI base URL override (e.g., `https://api.x.ai/v1`)
- `BRAIN_ANTHROPIC_MODEL` / `BRAIN_ANTHROPIC_COMPLEX_MODEL` — optional Anthropic model overrides for `brain.py`
- `BRAIN_OPENAI_MODEL` / `BRAIN_OPENAI_COMPLEX_MODEL` — optional OpenAI model overrides for `brain.py`
- `BRAIN_XAI_MODEL` / `BRAIN_XAI_COMPLEX_MODEL` — optional xAI/Grok model overrides for `brain.py`
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — enables Telegram alerts
- `ALCHEMY_API_KEY` — enables blockchain.py (Polygon RPC)
- `POLYMARKET_PRIVATE_KEY` — enables live trading (Tier 3)
- `STAGE2_POLYGON_GATING` — `1`/`true`/`yes` turns on Stage 2 Polygon gating; paper trades log the Polygon block snapshot, chain parity, and dual-leg slippage before execution.

## Recent Fix Logs
- `fix_logs/2026-04-04-weather-stop-loss-tuning.md`: broadened the weather entry gate to 60+ hours and log detailed stop contexts (signal hours, obs lookback, trend) so the next tuning pass can correlate the worst -$4/-$5 exits with intraday noise.
- `fix_logs/2026-04-03-confidence-based-sizing-rollout.md`: confidence-based sizing now overrides the requested USD amount, enforces the 0.25 Kelly cap, and carries the recommendation metadata through execution so the 53 shadow decisions actually affect fills.
- `fix_logs/2026-04-03-copy-strategy-filter-tuning.md`: tightened the copy strategy so only "informed" wallets (score ≥65, avg trade ≥$750) get mirrored and `inspect_copy_trade_open()` now blocks wallets with negative stored PnL or an AI verdict other than `copy`.
- `fix_logs/2026-04-03-stage2-polygon-gating.md`: Stage 2 paper runs with `STAGE2_POLYGON_GATING` now capture Polygon block metadata, chain parity, and dual-leg slippage before trading so every attempt carries the rollout snapshot.
- `fix_logs/2026-04-03-copy-strategy-filter-rework.md`: enforced the informed/65/750 wallet filter using the precise avg size, centralized the wallet PnL/brain-verdict gate, and prevented `_add_column_if_missing()` from crashing when `signals` is still missing in a backfilled schema.
- `fix_logs/2026-04-04-stage2-perplexity-validation.md`: Stage 2 Perplexity validation now caches verdicts, stores `perplexity_json`, and annotates profitable candidates so downstream automation can see the fallback metadata before promoting signals.

## Daily Report Updates
- 2026-04-03 report refreshed so the “Not Working” and “Top 5 Improvements” entries now point to the resolved fix logs while still highlighting the Stage 2/3 live-test plan captured in `reports/2026-04-03-daily-report.md`.
- Status: system health is stable with the reopened issues closed; Stage 2 Perplexity validation is now integrated (`fix_logs/2026-04-04-stage2-perplexity-validation.md`), Stage 2 polygon gating instrumentation is logging block metadata and dual-leg slippage when `STAGE2_POLYGON_GATING=1` (`fix_logs/2026-04-03-stage2-polygon-gating.md`), and the remaining Stage 2/3 rollout work is tracked through the Kanban tasks for Polygon gating, Perplexity validation, and live-readiness.
