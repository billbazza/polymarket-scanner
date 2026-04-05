# 2026-04-05 Brain Keychain Provider Detection

## What changed
- Fixed [runtime_config.py](/Users/will/.cline/worktrees/95a63/polymarket-scanner/runtime_config.py) so `SCANNER_KEYCHAIN_SERVICE` now falls back to the default `polymarket-scanner` service when the env override is unset, blank, or whitespace-only instead of querying an empty service name.
- Added focused regressions in [tests/test_runtime_config.py](/Users/will/.cline/worktrees/95a63/polymarket-scanner/tests/test_runtime_config.py) to verify blank-service fallback and to confirm `get()` still resolves `OPENAI_API_KEY` from the default Keychain service.
- Added brain-provider regressions in [tests/test_brain_provider_migration.py](/Users/will/.cline/worktrees/95a63/polymarket-scanner/tests/test_brain_provider_migration.py) covering Keychain-backed detection for the exact account names `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `XAI_API_KEY`, plus the wallet-discovery warning path when providers are configured but provider clients are unavailable.

## Behavior change
- Wallet discovery no longer misclassifies a blank `SCANNER_KEYCHAIN_SERVICE` override as “no provider configured”. With Keychain-backed provider keys present under `polymarket-scanner`, `brain.py` now sees the configured provider order correctly.
- Explicit process env overrides are unchanged and still take precedence over Keychain values for tests, CI, and one-shot operator runs.
- Graceful degradation remains intact: the brain still disables itself when no provider is actually configured, and it now emits the more accurate “clients unavailable for configured providers ...” warning when keys are present but the provider SDK/client layer is unavailable.

## Verification
- `python3 -m unittest tests.test_runtime_config tests.test_brain_provider_migration`
- Reproduced the prior false-negative case with `SCANNER_KEYCHAIN_SERVICE=\"   \"` and mocked Keychain entries for `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `XAI_API_KEY`; after the fix, provider detection resolves to `polymarket-scanner` and the false `No brain provider configured — brain disabled` warning no longer appears.
