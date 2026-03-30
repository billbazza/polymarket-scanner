---
name: scan
description: Run a Polymarket cointegration scan and show results
user_invocable: true
---

# /scan — Run Scanner

Run the Polymarket scanner and display results.

## Steps:
1. Check if the server is running on port 8899: `lsof -ti:8899`
2. If not running, start it: `cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 server.py &`
3. Run a fast (async) scan via the API:
   ```bash
   curl -s -X POST http://localhost:8899/api/scan/fast | python3 -m json.tool
   ```
4. Show the results summary: how many opportunities, top signals by grade
5. If any A+ (tradeable) signals found, highlight them with their action, z-score, and EV%

## Arguments:
- `--strict`: Pass z_threshold=2.0 and p_threshold=0.05 to the scan endpoint
- `--sync`: Use `/api/scan` instead of `/api/scan/fast` (slower but simpler)

## Example output format:
```
Scan complete: 5 opportunities in 18.2s
  [A+] z=+2.81 ev=12.3% | SELL Market_A / BUY Market_B
  [A]  z=-1.92 ev=4.1%  | BUY Market_A / SELL Market_B
  [B]  z=+1.73 ev=2.8%  | ...
```
