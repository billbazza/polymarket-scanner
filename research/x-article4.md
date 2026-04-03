1/7
Built a Polymarket trading bot over 3 months. Here are the biggest mistakes that cost me real money 

> Went from v1 to v61. Every version fixed something painful.

---

2/7
Stop Loss killed more money than it saved.

> Binary markets need room to breathe - fluctuations are normal.

> Stop Loss was cutting positions on random noise and locking in losses right before the market flipped.

> Removed it in v61. Immediately better.

---

3/7
Martingale + Stop Loss = a loss cascade.

> Seemed logical: lost $5 -> bet $8, lost again -> bet $10.

> In practice: a losing streak plus early exits = a hole in your balance in a single day.

> Killed it. For good.

---

4/7
Smart Exit without Force Exit is a trap.

> Token hits 90c (+75% profit), but the bot was waiting for a "BTC reversal" signal.

> Market closes, token drops, profit gone.

> Fix: hard Force Exit at 85c. No conditions, no waiting.

---

5/7
Blocking the 5:30-10:30 PM ET window felt safe. It wasn't.

> NYSE open = sharp spikes = bad signals. Made sense to block it.

> But the full block was also killing clean entries at 8-10:30 PM.

> Had to split the zone into segments with different edge/move thresholds.

---

6/7
The Gamma API lies about market start time.

> Start price ("price to beat") is the core input for every signal.

> Gamma was returning stale data. Had to pull prices directly from Chainlink on-chain on Polygon.

> That's its own adventure - polling a smart contract every 2 seconds at 2 AM.

---

7/7
The real lesson: don't overcomplicate what works.

> v1: complex system, 10 indicators -> -$200/day

> v61: "buy the expensive token for $5, exit at +30%" -> consistently green

> Simpler logic = fewer failure points.

> The bot runs 96 intervals a day. Every mistake shows up fast.