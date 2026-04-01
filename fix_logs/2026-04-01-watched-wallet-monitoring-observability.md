# 2026-04-01 Watched Wallet Monitoring Observability

## Source
- Operator report: watched wallets appeared idle, with no clear signal whether copy-trading polling was healthy, whether new activity was seen, or whether actions were blocked/ignored downstream.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/05582/polymarket-scanner/AGENTS.md), especially the audit-trail and `fix_logs/` requirements.

## Findings
- The autonomy copy-trader path treated `copy_condition_id` as globally unique, even though the DB gates copy trades by `copy_wallet + copy_condition_id`.
- That global dedupe made one watched wallet look "already mirrored" when another watched wallet held the same market, and it also prevented auto-closes while any other watched wallet still held that same condition.
- Operator visibility was too thin: the copy tab showed current positions and mirrored trades, but not whether a wallet had been polled recently, whether a new position was detected, or why an observed position was ignored or blocked.

## Fixes Applied
- Added `wallet_monitor_events` persistence plus watched-wallet poll status fields in [db.py](/Users/will/.cline/worktrees/05582/polymarket-scanner/db.py) so the system records recent watched-wallet outcomes such as `watching`, `baseline_skipped`, `no_change`, `blocked`, `ignored`, `mirrored`, `closed`, and `fetch_failed`.
- Updated [wallet_monitor.py](/Users/will/.cline/worktrees/05582/polymarket-scanner/wallet_monitor.py) to record operator-facing events for baseline creation, new-position discovery, trade-open blocking, successful mirroring, close-on-exit, fetch failures, and heartbeat-style successful polls.
- Corrected the autonomy copy-trader logic in [autonomy.py](/Users/will/.cline/worktrees/05582/polymarket-scanner/autonomy.py) to key copy trades by `(wallet, condition_id)` instead of only `condition_id`, and to emit the same watched-wallet event trail during autonomy-run mirroring.
- Updated [server.py](/Users/will/.cline/worktrees/05582/polymarket-scanner/server.py):
  - new `GET /api/copy/events`
  - watched-wallet add now records an initial `watch_added` event
  - `GET /api/copy/positions` now marks mirrored positions per wallet, not globally, and returns each wallet's latest poll/event status
- Updated [dashboard.html](/Users/will/.cline/worktrees/05582/polymarket-scanner/dashboard.html) so operators can see:
  - recent watched-wallet activity cards with status badges
  - per-wallet last poll / last event status on watch cards
  - whether the pipeline is actively polling with no changes versus detecting and then blocking/ignoring/mirroring activity
- Added focused regression coverage in [tests/test_watched_wallet_monitoring.py](/Users/will/.cline/worktrees/05582/polymarket-scanner/tests/test_watched_wallet_monitoring.py).

## Verification
- `python3 -m pytest tests/test_watched_wallet_monitoring.py tests/test_paper_trade_attempts.py`
  - result: `8 passed`
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain, wallet_monitor, autonomy, server; print('OK')"`
  - result: `OK`
- `python3 -m py_compile db.py wallet_monitor.py autonomy.py server.py`
