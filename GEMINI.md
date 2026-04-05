# Polymarket Scanner — Claude Code Contract

## What This Is
Multi-strategy scanner for Polymarket prediction markets. Three live strategies:
1. **Cointegration pairs** — finds diverged spreads between correlated markets, trades mean-reversion
2. **Weather edge** — compares NOAA + Open-Meteo forecasts vs market prices on temperature bucket markets (seasonal — markets appear in summer/autumn)
3. **Locked arb** — flags markets where YES+NO < $1 (alerting only; needs near-atomic fills to exploit)

Scores opportunities through math filters (EV, Kelly, slippage), optionally validates with the AI brain layer, and supports paper + live trading.

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
               Runtime state: logs/autonomy_state.paper.json + logs/autonomy_state.penny.json (local only; legacy shared files auto-migrate by scope)
                    |
Monitoring:    log_setup.py → logs/scanner.log + logs/journal.jsonl (trade audit trail)
```

### Trade Types
- **pairs** (two-leg): `entry_price_a/b`, `side_a/b`, linked to `signals` table. Auto-closes when |z| < 0.5 or price resolves.
- **weather** (single-leg): `entry_price_a` only, `token_id_a` stored on trade. Auto-closes when price ≥ 0.99 (WIN) or ≤ 0.01 (LOSS). `signal_id=NULL`, `weather_signal_id` foreign key instead.

### Autonomy Loop (every 30 min via launchd)
`autonomy.py` levels: `scout` (scan only) → `paper` (auto paper-trade A+ signals) → `penny` (real $1-5) → `book` (Kelly-sized). Paper and penny now persist isolated runtime state and scoped trade/accounting views, so paper experiments can stay open without consuming penny `max_open` capacity. Unattended launchd runs must use explicit scope selection via `AUTONOMY_BACKGROUND_SCOPES`; the default is paper-only, concurrent paper+penny loops are opt-in, and every cycle must log its `scope`/`runtime_label`. Paper-only strategy steps (copy mirroring, wallet discovery, wallet monitor) must stay disabled outside the paper runtime unless intentionally redesigned for a separate penny-safe lane. Weather now follows paper-parity runtime semantics: when the scoped primary `auto_trade_enabled` control is on, penny/book must scan and attempt eligible threshold-weather trades under the same admission rules as paper, surfacing any per-trade live safeguard veto explicitly in logs/journal/runtime status. Exact-temperature weather execution remains paper-only until separately approved. Each cycle: scan pairs → scan weather → refresh open trades → auto-close reverted/resolved trades → open new trades up to the scoped `max_open`.
Manual dashboard-triggered autonomy runs (`POST /api/autonomy`) execute in a background thread, so runtime status/UI must clearly label that background execution and only report completion after all enabled phases finish. For penny/book scopes, the weather phase must never silently inherit a scan-only gate; it must report whether it ran as `scan-only`, `live-auto-trade`, `slots_full`, or `error`, with timing/count metadata and any explicit runtime-control reason or live-trade veto.

## Key Conventions

### Module Pattern
Every module follows: docstring → imports → `log = logging.getLogger("scanner.<name>")` → functions. Entry points call `init_logging()` and `runtime_config.log_runtime_status(...)`. Library modules do neither.

### API Clients
- `api.py` — synchronous (requests), used by scanner.py and cron_scan.py
- `async_api.py` — async (httpx), used by async_scanner.py and server.py fast scan
- Both have identical function signatures. Both retry on connection errors.

### Scoring Pipeline
Every opportunity flows: scanner finds pair → `math_engine.score_opportunity()` grades A+ to F → optionally `brain.validate_signal()` for AI validation → `execution.execute_trade()` if tradeable.

### Trading Modes
- **Paper** (default): simulates against current prices, tracks in SQLite. There are no limits on numbers of trades while in paper mode. Paper-runtime trades, balances, and autonomy state are now isolated from the penny runtime, so open paper experiments do not block penny `max_open` limits or penny dashboard views. Autonomy now derives `mode="paper"` explicitly from the normalized `level` string so paper-level cointegration trades never invoke `brain.validate_signal()`; the system logs a `brain_validation_skipped` journal entry for each admitted A+ cohort signal, making the math-only trade path auditable while still enforcing slippage and balance checks.
- **Scoped strategy history**: weather dedupe/reopen/probation logic must only consult weather trades within the active `runtime_scope`, and cointegration history must stay independent from weather history even when token ids overlap. Operator-facing skip logs must identify both the active runtime lane and the blocking history source (for example `penny-weather` vs `paper-cointegration`).
- **Runtime ownership**: unattended autonomy and background wallet-monitor/copy-trader work must never implicitly follow the wrong scope. If penny is the active operational runtime, paper loops must be disabled unless `AUTONOMY_BACKGROUND_SCOPES` explicitly includes `paper`, and concurrent runtimes must remain clearly labeled as separate `autonomy:paper` / `autonomy:penny` audit lanes.
- **Live**: requires `POLYMARKET_PRIVATE_KEY` in the macOS Keychain (or an explicit per-process env override), uses py-clob-client
-- **Dashboard/runtime semantics**: `/api/runtime/account` is the canonical dashboard account endpoint. Paper scope reports bankroll accounting; penny scope reports verified Polygon wallet-backed cash plus only penny-scoped wallet/live positions and PnL. Penny mode is fail-closed: if live Polygon wallet data is unavailable, stale, or cannot be verified, the backend must error/block rather than falling back to paper balances, paper PnL, shared aggregate totals, or paper-derived closed-trade history, and the UI must show an explicit `LIVE / POLYGON WALLET` status. Penny runtime surfaces must exclude `paper_research` rows even if they were accidentally stamped with `runtime_scope="penny"`. `/api/stats`, `/api/runtime/account`, and `/api/autonomy/runtime` must reconcile exactly to the selected scope’s trade ledger and max-open usage, and penny strategy breakdowns must report live/wallet deployed capital rather than paper committed-capital placeholders.
- **Penny operator controls**: penny is a separate live book, not a paper alias. Runtime controls now live in scoped settings, with operator-visible `auto_trade_enabled` plus editable `max_open_override` surfaced in the dashboard and `/api/autonomy/runtime`. Operator edits to the live max-open control must be audit-visible in `logs/journal.jsonl`/`logs/scanner.log`, must apply only to the penny scope, and must never let paper controls mutate penny settings or vice versa.
- **Live execution/reporting ledger**: penny trades must persist real execution metadata end-to-end: order ids, tx hashes when available, entry/exit execution JSON, paid fees, and scoped live-book reporting (`/api/reporting/hmrc`) so HMRC/accounting workflows can read wallet-derived trade values without touching paper data.

### Database
SQLite at `scanner.db`. Schema auto-migrates on import via `db.init_db()`. New columns added via ALTER TABLE with try/except. `get_trades()`/`get_trade()` use LEFT JOIN to both `signals` and `weather_signals` so both trade types are returned correctly.

## Rules

### Never Do
- Hardcode API keys or private keys anywhere. Use the macOS Keychain via `runtime_config.py`; only use process env overrides for tests or short-lived operator runs.
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
- Store daily reports directly within `reports/` as `YYYY-MM-DD-daily-report.md` files, and keep `Working`, `Not Working`, and `Top 5 Improvements` in checkbox-style bullets so the UI can show tickable items and the new “Check Log” action can point to the fix/diagnostic entry.
- Keep daily-report summary metrics scoped explicitly: label realized vs net/unrealized PnL, label closed vs open vs total trades, and source the numbers from one `db.get_paper_account_overview()` snapshot per report.
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

## Runtime Config
Primary source: macOS Keychain service `polymarket-scanner` (override with `SCANNER_KEYCHAIN_SERVICE`).
Process environment variables with the same names remain valid as explicit one-shot overrides for tests, CI, or emergency local runs.
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
- `AUTONOMY_BACKGROUND_SCOPES` — comma-separated unattended autonomy scopes. Default `paper`; set to `penny` for penny-only launchd runs or `paper,penny` only when concurrent background runtimes are intentionally enabled and understood.
- `STAGE2_POLYGON_GATING` — `1`/`true`/`yes` turns on Stage 2 Polygon gating; paper trades log the Polygon block snapshot, chain parity, and dual-leg slippage before execution.

## Recent Fix Logs
- `fix_logs/2026-04-05-penny-weather-phase-runtime-visibility.md`: made manual dashboard autonomy runs report their background execution explicitly, added structured weather-phase timing/count telemetry to cycle status, and now log penny/book weather phases as an explicit paper-only skip instead of a silent omission.
- `fix_logs/2026-04-05-penny-weather-live-rollout-scope.md`: moved penny weather into an explicit live-rollout lane so penny/book always scan weather, added scoped `weather_auto_trade_enabled` controls plus live single-leg weather execution/ledger wiring, and kept exact-temperature weather execution paper-only while logging scan-only/live-auto-trade weather phase outcomes with timing.
- `fix_logs/2026-04-05-penny-weather-paper-parity.md`: removed the separate penny weather scan-only gate so threshold-weather now follows the primary penny auto-trade control, normalized the legacy weather toggle to runtime parity, and made live safeguard vetoes surface as explicit per-trade weather execution errors.
- `fix_logs/2026-04-05-autonomy-runtime-scheduler-isolation.md`: added explicit background-scope configuration for unattended autonomy, labeled every autonomy journal lane with its runtime scope, gated paper-only strategy steps out of the penny runtime, and tied the singleton wallet monitor to the paper background scope so penny operation no longer inherits paper loops unless intentionally configured.
- `fix_logs/2026-04-05-penny-live-book-controls-and-ledger.md`: added scoped penny auto-trade/max-open controls, surfaced the editable penny max-open UI in the dashboard, journaled/operator-logged every live trade-limit change, routed manual penny entry/close actions through the live execution path, persisted live order/fee metadata on trades, and exposed penny HMRC reporting inputs without mixing paper data.
- `fix_logs/2026-04-04-weather-guard-state.md`: added a persisted guard-state machine to keep the low-guardrail regime active until repeated stop-loss failures escalate the thresholds, and now log both the current and legacy blockers so we always know which filters would have vetoed the trade.
- `fix_logs/2026-04-04-weather-guard-relaxation.md`: relaxed the weather guard (liquidity>=5k / horizon>=48h / disagreement<=18pp) and now log the legacy vs relaxed tradeable counts so the before/after volumes stay auditable.
- `fix_logs/2026-04-04-weather-guardrail-improvements.md`: enforced the reopen probation counter for approved weather tokens, re-validated the horizon before fills, capped weather holds to ~72h, and logged stop contexts per token so the new diagnostics/journal payloads stay aligned.
- `fix_logs/2026-04-04-weather-stop-loss-tuning.md`: broadened the weather entry gate to 60+ hours and log detailed stop contexts (signal hours, obs lookback, trend) so the next tuning pass can correlate the worst -$4/-$5 exits with intraday noise.
- `fix_logs/2026-04-04-weather-guard-minimal.md`: pushed the weather guard to liquidity>=0/horizon>=0/disagreement<=1.0, captured every blocking filter per scan, and recorded the plan to raise these thresholds only once failed trades actually appear.
- `fix_logs/2026-04-03-confidence-based-sizing-rollout.md`: confidence-based sizing now overrides the requested USD amount, enforces the 0.25 Kelly cap, and carries the recommendation metadata through execution so the 53 shadow decisions actually affect fills.
- `fix_logs/2026-04-03-copy-strategy-filter-tuning.md`: tightened the copy strategy so only "informed" wallets (score ≥65, avg trade ≥$750) get mirrored and `inspect_copy_trade_open()` now blocks wallets with negative stored PnL or an AI verdict other than `copy`.
- `fix_logs/2026-04-03-stage2-polygon-gating.md`: Stage 2 paper runs with `STAGE2_POLYGON_GATING` now capture Polygon block metadata, chain parity, and dual-leg slippage before trading so every attempt carries the rollout snapshot.
- `fix_logs/2026-04-03-copy-strategy-filter-rework.md`: enforced the informed/65/750 wallet filter using the precise avg size, centralized the wallet PnL/brain-verdict gate, and prevented `_add_column_if_missing()` from crashing when `signals` is still missing in a backfilled schema.
- `fix_logs/2026-04-04-stage2-perplexity-validation.md`: Stage 2 Perplexity validation now caches verdicts, stores `perplexity_json`, and tags profitable candidates with fallback metadata for downstream automation.
- `fix_logs/2026-04-04-stage3-perplexity-gating.md`: Stage 3 readiness now leverages the cached Perplexity verdict so only profitable candidate features move into the live bucket, and the dashboard indicates which signals satisfy this check.
- `fix_logs/2026-04-04-grade-a-weighted-entries.md`: Grade-A trials now run smaller, grade-weighted entries (25–65% of the base size) with relaxed thresholds and explicit guardrail telemetry so the near-miss cohort keeps streaming real experience.

## Daily Report Updates
- 2026-04-03 report refreshed so the “Not Working” and “Top 5 Improvements” entries now point to the resolved fix logs while still highlighting the Stage 2/3 live-test plan captured in `reports/2026-04-03-daily-report.md`.
- Status: system health is stable with the reopened issues closed; Stage 2 Perplexity validation is now integrated (`fix_logs/2026-04-04-stage2-perplexity-validation.md`), Stage 2 polygon gating instrumentation is logging block metadata and dual-leg slippage when `STAGE2_POLYGON_GATING=1` (`fix_logs/2026-04-03-stage2-polygon-gating.md`), and the remaining Stage 2/3 rollout work is tracked through the Kanban tasks for Polygon gating, Perplexity validation, and live-readiness.
## Stage 3 Live Readiness
- Stage 3 live exposure is intentionally capped at $1–5 per trade via the `autonomy.py` `penny`/`book` levels. Follow the canonical checklist in `reports/2026-04-04-stage3-live-readiness.md` before escalating beyond paper: balance verification, ≤2% slippage locks, quarter-Kelly (≤0.25) sizing, and the presence/validation of `POLYMARKET_PRIVATE_KEY` plus `ALCHEMY_API_KEY`. Log each gate so the risk jury can audit why a live fill was permitted.
- Stage 3 readiness now depends on the cached Perplexity verdict so only profitable candidate features can progress into the live bucket, and the dashboard + automation wiring is captured in `fix_logs/2026-04-04-stage3-perplexity-gating.md`.

## Expanding A-Grade Coverage
- When you want to experiment beyond the current A+ cohort, treat this as a controlled trial: log all changes, keep the math/brain guardrails visible in `logs/journal.jsonl`, and capture your findings in `fix_logs/` or the daily report per the Always Do rules.
- Decide whether `math_engine.py:434-520` should keep `tradeable` strictly tied to every hard/soft filter; grade "A" soft misses (e.g., `momentum_pass`, `spread_std_pass`) stay blocked at stage 1 so the cointegration paper trial can safely log the failures and apply secondary guardrails.
- Broaden the cointegration trial guardrails (`cointegration_trial.py:12-212`) by limiting the allowed fails to the soft `momentum_pass`/`spread_std_pass` filters, capping the misses at 1, and by slightly lowering `min_liquidity` while raising `max_slippage_pct` so A-grade near-A+ signals earn paper trades without altering the grade label semantics (see `fix_logs/2026-04-04-grade-a-paper-promotion.md`).
- Surface the actual filter count and specific failures when a scope is blocked: enrich `record_attempt()`/journal entries with the number of failed filters, the individual filter names, and the aggregated grade so you can tell whether an "A" signal was a 7/8 near-miss or a 4/8 outlier, which in turn guides which filters may be safely relaxed.
- Weighted A-grade entries now respect their grade-weighted size (25–65% of the base size depending on the filter score), log `grade_weight` plus `weighted_entry_size_usd`, and benefit from the relaxed min_z_abs/min_ev_pct/min_liquidity/max_slippage gate described in fix_logs/2026-04-04-grade-a-weighted-entries.md so the existing A-grade signals start flowing again.
