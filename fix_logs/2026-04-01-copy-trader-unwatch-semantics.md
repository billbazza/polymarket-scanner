# 2026-04-01 Copy Trader Unwatch Semantics

## Source
- Manual operator report: removing a watched copy trader claimed it would close open copy trades, removed those trades from the global open-trades list, but left contradictory state visible on the copy-trader page.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/AGENTS.md).

## Policy Decision
- Manual wallet removal now means `unwatch`, not `force-close`.
- Unwatching stops future mirroring for that wallet immediately.
- Existing open copy trades remain open until the operator closes them explicitly or existing risk/resolution rules close them.
- Wallet history is preserved in `watched_wallets` as inactive instead of being hard-deleted.

## Fixes Applied
- Updated [server.py](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/server.py) `DELETE /api/copy/watch/{address}` to deactivate the wallet instead of closing its open copy trades.
- Added `db.unwatch_wallet()` in [db.py](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/db.py) so the stop-watching behavior is explicit and preserves history.
- Extended trade queries in [db.py](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/db.py) with watched-wallet join metadata so copy trades can expose whether their source wallet is still active.
- Added `/api/copy/detached` in [server.py](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/server.py) to surface open mirrored trades whose source wallet is no longer watched.
- Updated [dashboard.html](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/dashboard.html):
  - remove button now says `Unwatch`
  - confirmation text states that open copy trades stay open
  - watchlist cards distinguish manual `UNWATCHED` from `AUTO-DROPPED`
  - copy tab shows detached open copy trades from unwatched wallets
  - global open-trades rows mark copy trades from unwatched wallets
- Added regression coverage in [test_all.py](/Users/will/.cline/worktrees/0a08d/polymarket-scanner/test_all.py) for the full unwatch lifecycle.

## Verification
- `python3 -m py_compile db.py server.py test_all.py`
- `python3 test_all.py`
  - new unwatch regression passes
  - suite result in this worktree: `200/201` passing
  - remaining failure is pre-existing and unrelated: autonomy test still expects `paper max_open = 100`, repo currently has `25`
