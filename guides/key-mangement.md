# Key Management

## Runtime Source Of Truth
- The scanner now reads runtime config from the macOS Keychain through `runtime_config.py`.
- Default Keychain service: `polymarket-scanner`
- Each config item is stored as a generic password whose account name matches the config key.
- Process env vars with the same names still override Keychain values for tests and one-off runs, but `.env` is no longer loaded automatically.

## Recommended Setup
- Offline export: use your Trezor’s export or recovery flow in an air-gapped environment. Do not type the seed or private key on a connected machine unless you are intentionally importing it to a trusted wallet for controlled export.
- Vaulted storage: keep the exported private key in the macOS Keychain on the machine that runs the scanner. If you also maintain Vault, `pass`, or GPG backups, treat Keychain as the runtime source and the other store as recovery only.
- Auditability: startup now logs a redacted runtime-config audit line showing whether Keychain is available, which config names are satisfied by env overrides, and whether the live-trading keys are present.

## Add Or Update Entries
```bash
security add-generic-password -U -s polymarket-scanner -a BRAIN_PROVIDER -w auto
security add-generic-password -U -s polymarket-scanner -a ANTHROPIC_API_KEY -w 'sk-ant-...'
security add-generic-password -U -s polymarket-scanner -a OPENAI_API_KEY -w 'sk-...'
security add-generic-password -U -s polymarket-scanner -a XAI_API_KEY -w 'xai-...'
security add-generic-password -U -s polymarket-scanner -a TELEGRAM_BOT_TOKEN -w '...'
security add-generic-password -U -s polymarket-scanner -a TELEGRAM_CHAT_ID -w '...'
security add-generic-password -U -s polymarket-scanner -a ALCHEMY_API_KEY -w '...'
security add-generic-password -U -s polymarket-scanner -a POLYMARKET_PRIVATE_KEY -w '0x...'
security add-generic-password -U -s polymarket-scanner -a PERPLEXITY_API_KEY -w 'pplx-...'
```

## Inspect Entries
```bash
security find-generic-password -w -s polymarket-scanner -a BRAIN_PROVIDER
security find-generic-password -w -s polymarket-scanner -a POLYMARKET_PRIVATE_KEY
```

## Service Override
```bash
export SCANNER_KEYCHAIN_SERVICE=polymarket-scanner-staging
python3 scan.py --top 3
unset SCANNER_KEYCHAIN_SERVICE
```

## One-Shot Override
```bash
OPENAI_API_KEY=temporary-test-key python3 server.py
```

Use this only for tests, CI, or emergency cutovers. Persistent operator config belongs in the Keychain.
