# Cloudflare tunnel for the scanner

This project already runs a Flask dashboard on `http://localhost:8899`. To reach it from your phone without opening ports, expose it through Cloudflare Tunnel and serve it on a permanent hostname (like an "app" shortcut in the home screen).

## Reuse the existing Cloudflare account
1. The account details (tunnel UUID, DNS name, credential file) are stored in your other vault under `/Users/will/Obsidian-Vaults/BattleShip-Vault`. The LaunchAgent there (`scripts/launchagents/com.battleship.tunnel.plist`) shows the tunnel name (`battleship`) and points to `/usr/local/bin/cloudflared tunnel run battleship` with the working directory of that repo. Copy the same credential file (usually under `~/.cloudflared/<tunnel>.json`) or the full `config.yml` from that vault so you don’t have to recreate the tunnel from scratch.
2. Decide on the hostname you want to use (e.g., `scanner.willnav.com` or `polymarket.app`). Either reuse an existing Cloudflare DNS record from the Battleship tunnel (if that domain is available) or add a new `CNAME` in the Cloudflare dashboard that points to the tunnel. The tunnel can route multiple hostnames; you set this in `cloudflared tunnel route dns` or directly in the `ingress` section of the config.

## Configure this repo
1. Install `cloudflared` (Homebrew: `brew install cloudflared`).
2. Copy or symlink the Battleship credential file and config so the tunnel knows your account. If you don’t want to reuse the same JSON, run `cloudflared login`/`cloudflared tunnel create <name>` and note the `tunnel` UUID.
3. Create a `cloudflared/config.yml` (or set environment variables) inside this repo:
   ```yaml
   tunnel: <uuid>
   credentials-file: /Users/will/.cloudflared/<uuid>.json
   ingress:
     - hostname: scanner.example.com
       service: http://localhost:8899
     - service: http_status:404
   ```
   Replace `<uuid>` and `scanner.example.com` with the values from the Battleship vault (or the new hostname you chose).
4. To run the tunnel manually, go to this repo and start:
   ```bash
   cd /Users/will/Obsidian-Vaults/polymarket-scanner
   cloudflared tunnel --config cloudflared/config.yml run
   ```
   Keep this process running while you want the mobile app to reach the scanner.

## Automate with a helper
- Follow the Battleship pattern: copy `scripts/launchagents/com.battleship.tunnel.plist`, adjust the `Label`, `WorkingDirectory`, and the `ProgramArguments` (it can be `cloudflared tunnel run <name>`), and place it under `~/Library/LaunchAgents`. Load it with `launchctl load ~/Library/LaunchAgents/<your-label>.plist` so the tunnel launches at login and restarts automatically.
- Record the log path somewhere (`logs/tunnel.log`) so you can tail it if the tunnel flaps.

## Mobile workflow
1. Add the Cloudflare-hosted hostname (e.g., `https://scanner.example.com`) to your phone’s home screen as a web app shortcut (browser → share → Add to Home Screen).
2. The tunnel handles HTTPS, so no extra certificates are required and it works over 4G/Wi‑Fi.
3. When you update the scanner, the tunnel automatically proxies to port 8899, so refresh the shortcut if the path changes.

## Monitoring and cleanup
- Use the existing health-check pattern, e.g. `cloudflared tunnel list`, `cloudflared tunnel info <name>`, and the log in `logs/tunnel.log` (or battle-vault’s `scripts/COMMANDS.md`) to confirm it is up.
- If you ever stop using this tunnel, unload the LaunchAgent and/or stop the `cloudflared` process to avoid stale DNS entries.

If you need me to copy the Battleship credentials or build a dedicated LaunchAgent for this repo, let me know which hostname to publish and I can script it for you.
