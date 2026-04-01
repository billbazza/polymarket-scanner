# 2026-04-01 Paper Accounting Fix Log

## Source
- Paper-trading bankroll/accounting/dashboard clarity task.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/f4079/polymarket-scanner/AGENTS.md).

## Fixes Applied
- Centralized paper mark-to-market math in [db.py](/Users/will/.cline/worktrees/f4079/polymarket-scanner/db.py) for both single-leg and pairs trades so snapshots, account summaries, and closes all use one valuation path.
- Added probability-price validation to ignore malformed or out-of-range marks before they can pollute snapshots or unrealized P&L.
- Fixed `BUY_NO` copy-trade entry handling in [db.py](/Users/will/.cline/worktrees/f4079/polymarket-scanner/db.py): Polymarket Data API `curPrice` already refers to the held token's price, so NO positions must not be inverted on insert.
- Added migration `007_copy_no_entry_price_fix` in [db.py](/Users/will/.cline/worktrees/f4079/polymarket-scanner/db.py) to repair existing open copy `BUY_NO` trades created with inverted entry prices. This was the direct source of absurd unrealized P&L and total-equity numbers.
- Updated [tracker.py](/Users/will/.cline/worktrees/f4079/polymarket-scanner/tracker.py) to reuse the centralized valuation helpers and skip invalid marks instead of recording bad snapshots.
- Updated [dashboard.html](/Users/will/.cline/worktrees/f4079/polymarket-scanner/dashboard.html) to display equity as `cash + marked open positions`, while still showing committed capital and unrealized P&L explicitly.
- Added targeted open-trade valuation coverage in [test_all.py](/Users/will/.cline/worktrees/f4079/polymarket-scanner/test_all.py) for single-leg `BUY_NO` paper marks and pairs share-based unrealized P&L.

## Verification
- `python3 -m py_compile db.py tracker.py execution.py server.py test_all.py`
- `python3 test_all.py`
- `python3 - <<'PY' ... db.get_paper_account_state(refresh_unrealized=False) ... PY` against the local `scanner.db` after migration to confirm paper equity returned to plausible levels.
