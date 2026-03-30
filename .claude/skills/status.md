---
name: status
description: Show system status — server, cron, trades, signals summary
user_invocable: true
---

# /status — System Status

Show the overall status of the Polymarket scanner system.

## Steps:
1. Check server status:
   ```bash
   lsof -ti:8899 2>/dev/null && echo "Server: RUNNING" || echo "Server: STOPPED"
   ```

2. Check cron/LaunchAgent status:
   ```bash
   launchctl list | grep polymarket
   ```

3. Check last cron scan:
   ```bash
   tail -5 /Users/will/Obsidian-Vaults/polymarket-scanner/logs/cron.log
   ```

4. Get stats from API (or DB directly):
   ```bash
   curl -s http://localhost:8899/api/stats 2>/dev/null || \
   cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 -c "import db; import json; print(json.dumps(db.get_stats(), indent=2))"
   ```

5. Check if brain is configured:
   ```bash
   grep -c "ANTHROPIC_API_KEY=sk-" /Users/will/Obsidian-Vaults/polymarket-scanner/.env 2>/dev/null && echo "Brain: ACTIVE" || echo "Brain: NOT CONFIGURED"
   ```

6. Show DB size:
   ```bash
   ls -lh /Users/will/Obsidian-Vaults/polymarket-scanner/scanner.db
   ```

## Output format:
```
Polymarket Scanner Status
─────────────────────────
Server:     RUNNING (PID 12345)
Cron:       ACTIVE (every 30min)
Last scan:  2 hours ago
Brain:      NOT CONFIGURED (set ANTHROPIC_API_KEY in .env)

Signals:    47 total (3 tradeable)
Trades:     2 open, 5 closed
P&L:        +$12.50 (71% win rate)
DB size:    256KB
```
