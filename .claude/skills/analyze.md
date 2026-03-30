---
name: analyze
description: Run analysis on historical scanner data — grades, performance, patterns
user_invocable: true
---

# /analyze — Historical Analysis

Run analysis on accumulated scanner data.

## Steps:
1. Run the analysis report:
   ```bash
   cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 analysis.py
   ```
2. Present key findings:
   - Grade distribution (how many A+, A, B, C, D, F)
   - Which events produce the most signals
   - Scan frequency and timing
   - If trades exist: win rate, avg P&L, Sharpe ratio
3. If the user asks about specific patterns, query the DB directly:
   ```bash
   cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 -c "
   import db
   signals = db.get_signals(limit=500)
   # ... custom analysis
   "
   ```

## Arguments:
- `grades` — just show grade distribution
- `events` — show which events produce the most signals
- `trades` — analyze trade performance only
