# 2026-04-05 Autonomy Runtime Scheduler Isolation

## Source
- Operator report dated 2026-04-05: penny operation was active while the background autonomy scheduler still logged `level=paper scope=paper`, which made it unclear whether live runtime activity was isolated from paper automation.
- Followed the repo contract in [AGENTS.md](/Users/will/.cline/worktrees/40df1/polymarket-scanner/AGENTS.md), including audit logging, fix-log requirements, and synced doc updates.

## Findings
- The unattended `autonomy.py` entrypoint still defaulted to a single implicit paper runtime whenever launchd invoked `python3 autonomy.py` with no explicit scope.
- Penny cycles were still entering paper-only autonomy steps such as weather auto-trading, copy-trader mirroring, and wallet discovery, which risked cross-runtime confusion and paper-style side effects during penny operation.
- The singleton `wallet_monitor` background service started unconditionally on server startup, so a penny-focused operator session could still inherit paper copy-trading background activity.
- Journal entries did not consistently stamp an explicit runtime label, which made mixed paper/penny audit review harder than it should be.

## Fixes Applied
- Updated [autonomy.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/autonomy.py) with `AUTONOMY_BACKGROUND_SCOPES`, defaulting unattended runs to `paper` while requiring explicit configuration for `penny` or concurrent `paper,penny` scheduler scopes.
- Added runtime labeling in [autonomy.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/autonomy.py) so journal entries and cycle logs now carry `runtime_scope` and `runtime_label` such as `autonomy:paper` and `autonomy:penny`.
- Gated paper-only autonomy steps in [autonomy.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/autonomy.py): penny/book cycles now skip weather auto-trading, copy-trader mirroring, and wallet discovery with explicit audit entries instead of silently reusing paper paths.
- Updated [server.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/server.py) so the singleton [wallet_monitor.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/wallet_monitor.py) only auto-starts when the paper runtime is included in `AUTONOMY_BACKGROUND_SCOPES`, and logs its runtime scope when enabled.
- Registered `AUTONOMY_BACKGROUND_SCOPES` in [runtime_config.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/runtime_config.py) so runtime-status logs surface the operator override cleanly.
- Added regression coverage in [tests/test_runtime_scope_split.py](/Users/will/.cline/worktrees/40df1/polymarket-scanner/tests/test_runtime_scope_split.py) for background-scope parsing, runtime-labeled journaling, and penny-cycle skipping of paper-only strategy steps.
- Synced [AGENTS.md](/Users/will/.cline/worktrees/40df1/polymarket-scanner/AGENTS.md), [CLAUDE.md](/Users/will/.cline/worktrees/40df1/polymarket-scanner/CLAUDE.md), and [GEMINI.md](/Users/will/.cline/worktrees/40df1/polymarket-scanner/GEMINI.md) so the runtime-semantics contract matches the code.

## Verification
- `python3 -m py_compile autonomy.py server.py wallet_monitor.py runtime_config.py tests/test_runtime_scope_split.py`
- `python3 -m unittest tests.test_runtime_scope_split -v`
