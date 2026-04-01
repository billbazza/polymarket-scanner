# 2026-04-01 Wallet Monitor Duplicate Mirroring Dedupe

## Source
- Operator report from 2026-04-01: the same watched-wallet position was logged repeatedly as `Validator NEW position` and `AUTO-MIRRORED Validator -> trade #1 ($20 paper)` within minutes for the same market and outcome.
- Followed the repo contract in [AGENTS.md](/Users/will/.cline/worktrees/1a4f7/polymarket-scanner/AGENTS.md), especially Architecture, Database, Never Do, and Always Do sections requiring structured persistence, graceful recovery, and audit logging.

## Findings
- The root cause was the copy-trade dedupe identity, not missing poll state or failed mirrored-trade persistence.
- `wallet_monitor.py` already persisted the current poll snapshot in `_known_positions` and on disk, and mirrored trades were being inserted into SQLite correctly.
- The failure was in [db.py](/Users/will/.cline/worktrees/1a4f7/polymarket-scanner/db.py): `inspect_copy_trade_open()` delegated duplicate detection to `has_open_copy_trade()`, which preferred `external_position_id` when present.
- For watched-wallet positions, the stable business identity is `wallet + conditionId + outcome`. If Polymarket returned the same live position with a different `asset` token id after a restart or snapshot loss, the DB preflight could miss the already-open mirror and allow a duplicate open.
- Operator visibility was also too coarse because this path was reported as a generic block or post-check error instead of an explicit `already_mirrored` outcome.

## Fixes Applied
- Added `find_open_copy_trade()` in [db.py](/Users/will/.cline/worktrees/1a4f7/polymarket-scanner/db.py) so watched-wallet copy-trade dedupe resolves by stable wallet-position identity:
  - `canonical_ref` first
  - then `copy_condition_id + copy_outcome`
  - then `external_position_id`
  - then plain `copy_condition_id` as a final fallback
- Updated `inspect_copy_trade_open()` to use that identity lookup and return `existing_trade_id` plus a clearer operator reason when the position is already mirrored.
- Hardened `open_copy_trade()` with an atomic `INSERT ... SELECT ... WHERE NOT EXISTS (...)` guard using the same identity rules, so restart/race scenarios cannot silently open a second mirror for the same watched-wallet position.
- Updated [wallet_monitor.py](/Users/will/.cline/worktrees/1a4f7/polymarket-scanner/wallet_monitor.py) so duplicate suppression is logged and persisted as `already_mirrored` instead of a generic `blocked` or `error` event.

## Verification
- Added restart-style regression coverage in [tests/test_watched_wallet_monitoring.py](/Users/will/.cline/worktrees/1a4f7/polymarket-scanner/tests/test_watched_wallet_monitoring.py):
  - existing open mirror plus changed `asset` token id stays at one open trade
  - wallet monitor records `already_mirrored`
  - DB insert path rejects duplicate `wallet + condition + outcome` mirrors even if the token id changes
