# Polymarket Scanner — Plan

## Current State
Tiers 1-3 built. All modules import clean. Cron scanning every 30min via launchd.
23 signals in DB from 2 scans. Dashboard live on :8899.

To activate live features: set keys in `.env` (see `.env.example`).

---

## Tier 1 — Math & Resilience (DONE)
- [x] Kelly Criterion — quarter-Kelly position sizing
- [x] Expected Value filter — kill trades with <5% edge
- [x] Slippage Protection — walk order book, skip thin markets
- [x] Python Logging — rotating file handler (5MB x 5 backups)
- [x] Error Recovery — API retries, per-pair fallbacks, server never crashes
- [x] Scoring pipeline — grade A+ through F, 5 filters
- [x] Dashboard — Grade/EV/Kelly columns in signals table

## Tier 2 — Speed, Alerts & AI Brain (DONE minus Telegram)
- [x] Cron scanning — launchd every 30min, logs to logs/cron.log
- [x] httpx + asyncio — async_scanner.py, parallel price fetching (~5x faster)
- [x] Claude API brain — brain.py: estimate_probability, validate_signal, estimate_batch
- [x] Structured JSON prompts — prompts/v1_probability.txt
- [x] Prompt versioning — /prompts folder, version tracked in output
- [x] Bayesian Updating — bayes.py: update_with_brain, chain_updates
- [x] Log Returns — returns.py: pairs_pnl, sharpe_ratio, log_return_series
- [ ] Telegram bot — trade alerts to phone (aiogram) — SKIPPED for now

## Tier 3 — Execution (DONE — needs keys to activate)
- [x] execution.py — paper/live trading, GTC orders, balance pre-checks
- [x] blockchain.py — web3.py + Polygon RPC (Alchemy), USDC.e balance
- [x] python-dotenv — all entry points load .env
- [x] tracker.py — live price refresh, snapshot saving, auto-close on reversion
- [x] deploy/ — systemd service + timer + VPS setup script

## Tier 4 — Analysis & Intelligence (DONE)
- [x] analysis.py — signal summary, grade distribution, event analysis, scan performance
- [x] leaderboard.py — study top Polymarket traders
- [x] Rotating log files — 5MB x 5 backups

## Remaining (Future)
- [ ] Telegram bot (aiogram) — alerts to phone
- [ ] Position dashboard in Telegram — tap to see P&L, close trades
- [ ] Polygonscan transaction tracing — reverse-engineer winner patterns
- [ ] Perplexity integration — real-time research during scans
- [ ] Production VPS deployment — run deploy/setup.sh on a $5 box

## Layer Map (article → our system)
| Article Layer | Tools | Status |
|---|---|---|
| 1. Data | CLOB API, py-clob-client, Polygon RPC, USDC.e, dotenv | DONE |
| 2. AI Brain | Claude API, structured prompts, httpx, prompt versioning | DONE |
| 3. Math | EV, Kelly, Bayes, Log Returns, NumPy | DONE |
| 4. Execution | GTC, balance checks, SQLite, slippage, web3.py | DONE |
| 5. Monitoring | Telegram, dashboard, logging, error recovery | 90% (no Telegram) |
| 6. Infra | Python, asyncio, VPS, systemd, Git | DONE |
| 7. R&D | Leaderboard, Polygonscan, Claude, Perplexity | Leaderboard done |

## File Inventory (32 files)
```
Core:        scanner.py, async_scanner.py, scan.py, cron_scan.py
Math:        math_engine.py, bayes.py, returns.py
AI:          brain.py, prompts/v1_probability.txt
Data:        api.py, async_api.py
Execution:   execution.py, blockchain.py, tracker.py
Persistence: db.py, scanner.db
Server:      server.py, dashboard.html
Analysis:    analysis.py, leaderboard.py
Infra:       log_setup.py, requirements.txt, .env, .env.example, .gitignore
Deploy:      deploy/polymarket-scanner.service, deploy/polymarket-cron.service,
             deploy/polymarket-cron.timer, deploy/setup.sh
Docs:        CLAUDE.md, ORIENT.md, MEMORY.md, PLAN.md
Guides:      guides/scoring.md, guides/api-patterns.md
Skills:      .claude/skills/{scan,signals,status,paper-trade,analyze,brain}.md
```
