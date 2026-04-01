# 2026-04-01 Paper Open Position Policy

## Source
- Operator request to standardize paper-trading open-position policy across autonomy, copy trading, weather/manual entry paths, and UI/API messaging.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/4e402/polymarket-scanner/AGENTS.md), especially Architecture, Trading Modes, Database, Never Do, and Always Do.

## Problem
- `autonomy.py` already treated paper mode as uncapped by setting `paper.max_open = None`, but copy-trader settings still defaulted to a dormant `total_open_cap=25`, which looked like an active platform-wide limit.
- Manual/API entry paths did not consistently expose why a paper trade was blocked, so operators could not distinguish duplicate suppression, cash exhaustion, and optional cap overrides.
- Copy-trader automation and wallet monitoring logged inferred cap failures after `db.open_copy_trade()` returned `None`, which could hide the real blocking cause.

## Fixes Applied
- Centralized paper open-decision checks in [db.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/db.py):
  - added `inspect_pairs_trade_open()`
  - added `inspect_copy_trade_open()`
  - extended weather decisions to include shared paper-policy metadata
  - normalized optional caps so `0`/`None` means uncapped
- Standardized copy-trade settings in [db.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/db.py) and [server.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/server.py):
  - default copy caps are now uncapped unless explicitly enabled
  - `effective_*_cap` fields only report active overrides
  - `caps_active` distinguishes enabled overrides from the default uncapped policy
- Updated [server.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/server.py) so pairs, weather, and copy-trade open endpoints return structured blocking payloads with `reason_code`, operator-facing `reason`, paper-account context when relevant, and explicit paper-policy text.
- Updated [autonomy.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/autonomy.py) and [wallet_monitor.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/wallet_monitor.py) to use the centralized copy-trade decision before opening and to journal/log the real blocking reason.
- Updated [dashboard.html](/Users/will/.cline/worktrees/4e402/polymarket-scanner/dashboard.html) so:
  - copy settings describe paper mode as uncapped and cash-limited by default
  - copy cap inputs use `0 = uncapped`
  - copy monitor messaging no longer implies a hidden `25`-trade ceiling
  - manual pairs/weather/copy actions surface specific block reasons from the API instead of generic failures
- Extended the paper-account payload in [db.py](/Users/will/.cline/worktrees/4e402/polymarket-scanner/db.py) with shared position-policy text so UI/API consumers can explain the policy consistently.

## Live Safety
- Live-trading protections remain intact: no changes were made to live-mode balance checks, live autonomy levels, or live position caps in `penny`/`book`.
- Optional copy caps remain available as explicit operator overrides; they are no longer presented as hidden paper defaults.

## Verification
- `python3 -m py_compile autonomy.py db.py server.py wallet_monitor.py test_all.py`
- `python3 test_all.py`
  - suite result: `230/230` passed
- `python3 -c "import log_setup, math_engine, db, scanner, async_api, async_scanner, brain, bayes, returns, execution, blockchain; print('OK')"`
