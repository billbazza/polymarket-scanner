# 2026-04-05 Penny Cointegration Execution Parity

## Source
- Operator request to make penny cointegration follow paper admission/execution semantics unless a live-only safeguard explicitly vetoes the trade, and to surface that veto clearly in audit logs and the UI.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/4a955/polymarket-scanner/AGENTS.md), especially Architecture, Trading Modes, Rules, and Always Do.

## Problem
- Penny/live cointegration autonomy was not persisting several operator-facing decisions that paper already logged, including duplicate-position suppression, brain rejections, execution vetoes, and execution exceptions.
- Live execution failures often collapsed into a generic `error` string, so the dashboard and attempt feed could not distinguish slippage, wallet/balance, HMRC, missing live config, or exchange/order failures.
- The signals table was scan-only. In penny view it showed an A+ signal as tradeable without any scoped execution context, so operators could not see whether penny had already opened it, blocked it, or failed it.

## Fixes Applied
- Updated [autonomy.py](/Users/will/.cline/worktrees/4a955/polymarket-scanner/autonomy.py) so penny/book cointegration now records the same operator-facing `pairs` attempt events as paper for:
  - duplicate signal/event suppression
  - live brain rejections
  - execution vetoes and failures
  - successful penny opens
- Updated [execution.py](/Users/will/.cline/worktrees/4a955/polymarket-scanner/execution.py) so live pairs failures now return structured `reason_code` values for balance checks, insufficient balance, price fetch failures, slippage blocks, HMRC gating, missing private key/client, and exchange order failures.
- Updated [server.py](/Users/will/.cline/worktrees/4a955/polymarket-scanner/server.py) so `/api/signals` is runtime-scope aware, and manual pairs opens now persist the precise live `reason_code` instead of a generic `open_failed`.
- Updated [db.py](/Users/will/.cline/worktrees/4a955/polymarket-scanner/db.py) so scoped signal rows now include penny/paper execution context: open trade id, latest scoped attempt, preflight blocker, manual-open readiness, and a runtime status/detail string.
- Updated [dashboard.html](/Users/will/.cline/worktrees/4a955/polymarket-scanner/dashboard.html) so penny signals show scoped execution status instead of only raw scan tradeability, and the gate panel now labels itself by active runtime (`Research Gate` vs `Penny Gate`).

## Behavior
- Paper and penny now share the same visible admission/execution audit trail for cointegration; the remaining penny-only differences are explicit live safeguards such as wallet/balance checks, slippage, HMRC/live config requirements, brain/stage gates, duplicate live positions, and exchange/order failures.
- No strategy semantics were widened here: the change is parity of decision logging/status surfacing plus structured live veto reporting, not a relaxation of live safeguards.

## Verification
- `python3 -m py_compile autonomy.py execution.py server.py db.py`
- `python3 - <<'PY' ... db.get_signals(limit=2, runtime_scope='penny', size_usd=3) ... PY`
