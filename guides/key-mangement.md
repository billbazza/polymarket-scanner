# Key Management

## Runtime Source Of Truth
- The scanner now reads runtime config from the macOS Keychain through `runtime_config.py`.
- Default Keychain service: `polymarket-scanner`
- Each config item is stored as a generic password whose account name matches the config key.
- Process env vars with the same names still override Keychain values for tests and one-off runs, but `.env` is no longer loaded automatically.

<<<<<<< Updated upstream
## Recommended Setup
- Offline export: use your Trezor’s export or recovery flow in an air-gapped environment. Do not type the seed or private key on a connected machine unless you are intentionally importing it to a trusted wallet for controlled export.
- Vaulted storage: keep the exported private key in the macOS Keychain on the machine that runs the scanner. If you also maintain Vault, `pass`, or GPG backups, treat Keychain as the runtime source and the other store as recovery only.
- Auditability: startup now logs a redacted runtime-config audit line showing whether Keychain is available, which config names are satisfied by env overrides, and whether the live-trading keys are present.

## Legacy Vault Wrapper
- If you still prefer a Vault, `pass`, or GPG wrapper, fetch/decrypt the secret and export it only for the lifetime of the process:

```bash
export POLYMARKET_PRIVATE_KEY="$VAULT_KEY"
python3 autonomy.py
unset POLYMARKET_PRIVATE_KEY
```

- Keep raw secrets out of `.env`; the Keychain or one-shot process env injection should be the runtime path.

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

## Read Or Delete Entries
```bash
security find-generic-password -w -s polymarket-scanner -a BRAIN_PROVIDER
security find-generic-password -w -s polymarket-scanner -a OPENAI_API_KEY
security delete-generic-password -s polymarket-scanner -a OPENAI_API_KEY
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

## Keychain-Only Test
You can rename `.env` temporarily on macOS to confirm the scanner is no longer loading it automatically.

```bash
mv .env .env.disabled
python3 scan.py --top 3
python3 -c "import uvicorn, server; uvicorn.run(server.app, host='127.0.0.1', port=8901)"
mv .env.disabled .env
```

Two caveats:
- If you have shell-exported env vars, they still override Keychain values, so unset them first if you want a true Keychain-only test.
- The deploy files still mention `.env` for Linux/systemd fallback, so do not remove it as part of deployment cleanup unless you also want to change that path later.
