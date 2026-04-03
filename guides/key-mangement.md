Secure Key Export Workflow

  - Offline export: Use your Trezor’s export or
    recover flow in an air-gapped environment. Don’t
    type the seed/key on a connected machine—write it
    down by hand, verify it, then import it only on a
    machine you trust. If you prefer MetaMask for
    convenience, import the Trezor seed into MetaMask
    temporarily, export the private key there, then
    delete the imported account afterward.
  - Vaulted storage: Store the exported key in a
    local secrets store (Vault, pass, Keychain, GPG
    file, etc.). Create a short script that:
      1. Fetches/decrypts the key from the vault
         (e.g., PASSHPASS=$(pass polymarket/scanner)
         or VAULT_TOKEN=... vault read -field=value
         secret/polymarket/private_key).
      2. Exports it only for the lifetime of the
         scanner run:

         export POLYMARKET_PRIVATE_KEY="$VAULT_KEY"
         python3 autonomy.py
         unset POLYMARKET_PRIVATE_KEY
      3. Makes sure the .env in the repo never
         contains the raw key—only the script ever
         injects it at runtime, and the
         repo’s .env.example stays key-free per
         AGENTS “Never Do” list.
  - Automated helper: If you want
    execution.check_balance() or blockchain to see
    the key without editing .env, wrap the launch
    command in a script that pulls the key from Vault
    and runs the scanner in one shot. Keep the script
    on the secured machine, log each live run (AGENTS
    requires logging every trading decision), and
    rotate the stored key whenever the hardware
    wallet’s custody changes.