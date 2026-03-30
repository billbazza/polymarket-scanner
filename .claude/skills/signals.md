---
name: signals
description: Show recent signals from the scanner database
user_invocable: true
---

# /signals — View Recent Signals

Show recent signals from the Polymarket scanner database.

## Steps:
1. Fetch signals from the API:
   ```bash
   curl -s "http://localhost:8899/api/signals?limit=20" | python3 -m json.tool
   ```
2. If the server isn't running, query the database directly:
   ```bash
   cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 -c "
   import db
   signals = db.get_signals(limit=20)
   for s in signals:
       gl = s.get('grade_label', '?')
       z = s['z_score']
       ev = s.get('ev', {})
       ev_pct = ev.get('ev_pct', '-') if ev else '-'
       print(f'[{gl}] z={z:+.2f} ev={ev_pct}% | {s[\"event\"][:50]} | {s[\"status\"]}')
   "
   ```
3. Format as a table showing: Grade, Z-Score, EV%, Event, Status
4. Highlight any tradeable signals

## Arguments:
- A number (e.g., `/signals 5`) limits the result count
- `new` — only show signals with status "new"
- `traded` — only show signals that have been traded
