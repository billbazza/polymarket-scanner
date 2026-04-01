# 2026-04-01 Autonomy State Runtime File Fix Log

## Source
- Runtime autonomy state was tracked in git as `autonomy_state.json`, causing every autonomy cycle to produce commit noise from local counters and timestamps.
- Followed repo contract from [AGENTS.md](/Users/will/.cline/worktrees/45673/polymarket-scanner/AGENTS.md).

## Findings
- `autonomy.py` persisted mutable operational state to the repo root in `autonomy_state.json`.
- `server.py` reads autonomy state through `autonomy.load_state()`, so the safe fix point is the autonomy state loader/saver rather than dashboard-specific code.
- The repo already treats `logs/` as local runtime output and ignores it in git, and `wallet_monitor.py` already stores similar runtime state under `logs/`.
- Simply adding the tracked root file to `.gitignore` would not fix future diffs by itself because tracked files stay tracked until removed from the git index.

## Fixes Applied
- Updated [autonomy.py](/Users/will/.cline/worktrees/45673/polymarket-scanner/autonomy.py) so runtime autonomy state now persists to `logs/autonomy_state.json`.
- Added legacy migration logic in [autonomy.py](/Users/will/.cline/worktrees/45673/polymarket-scanner/autonomy.py): if the new runtime file is missing but the old repo-root `autonomy_state.json` exists, `load_state()` imports it, normalizes missing keys, and writes the migrated state to the new runtime path.
- Updated [autonomy.py](/Users/will/.cline/worktrees/45673/polymarket-scanner/autonomy.py) to write state atomically via a temporary file replace, reducing the risk of partial writes during autonomy cycles.
- Updated [.gitignore](/Users/will/.cline/worktrees/45673/polymarket-scanner/.gitignore) to ignore both the new runtime file and the old root path so migrated local files do not re-enter normal commits.
- Updated [AGENTS.md](/Users/will/.cline/worktrees/45673/polymarket-scanner/AGENTS.md), [CLAUDE.md](/Users/will/.cline/worktrees/45673/polymarket-scanner/CLAUDE.md), and [GEMINI.md](/Users/will/.cline/worktrees/45673/polymarket-scanner/GEMINI.md) so repo guidance reflects the runtime-state location and the legacy auto-migration behavior.
- Added regression coverage in [test_all.py](/Users/will/.cline/worktrees/45673/polymarket-scanner/test_all.py) for the runtime state path and legacy-file migration path.

## Migration Notes
- Existing local behavior is preserved. Operators can leave any current repo-root `autonomy_state.json` in place; the next autonomy or dashboard state load will migrate that data into `logs/autonomy_state.json`.
- After migration, the runtime file under `logs/` is the active source of truth. The old repo-root file is ignored and no longer needed for normal operation.

## Verification
- `python3 -m py_compile autonomy.py server.py test_all.py`
- `python3 test_all.py`
- `python3 -c "import autonomy; s=autonomy.load_state(); autonomy.save_state(s); print(autonomy.STATE_FILE)"`
