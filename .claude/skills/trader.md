---
name: trader
description: Check autonomous trader status, journal, promote levels
user_invocable: true
---

# /trader — Autonomous Trader Management

Manage the autonomous trading ladder (Scout → Paper → Penny → Book).

## Steps:

### Status (default):
```bash
cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 autonomy.py --status
```

### Journal:
```bash
cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 autonomy.py --journal
```

### Promote:
```bash
cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 autonomy.py --promote
```

## Arguments:
- `status` — show performance and graduation readiness (default)
- `journal` — show recent trading decisions and reasoning
- `promote` — check graduation criteria and promote if ready
- `reset` — reset level counters (keeps current level)
- `level paper|penny|book|scout` — manually set level
