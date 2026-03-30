---
name: paper-trade
description: Open or manage paper trades from scanner signals
user_invocable: true
---

# /paper-trade — Paper Trading

Open, view, or close paper trades.

## Steps:

### Opening a trade (default):
1. Show recent tradeable signals (grade A+ preferred):
   ```bash
   curl -s "http://localhost:8899/api/signals?status=new&limit=10"
   ```
2. Present the best signals to the user with grade, z-score, EV%, action
3. Ask which signal ID to trade and size (default $100)
4. If brain is available, validate first:
   ```bash
   curl -s -X POST "http://localhost:8899/api/brain/validate/{signal_id}"
   ```
5. Open the paper trade:
   ```bash
   curl -s -X POST "http://localhost:8899/api/trades?signal_id={id}&size_usd={size}"
   ```

### Viewing open trades:
```bash
curl -s "http://localhost:8899/api/trades?status=open"
```

### Closing a trade:
1. Show open trades
2. Ask for exit prices (current market prices)
3. Close:
   ```bash
   curl -s -X POST "http://localhost:8899/api/trades/{id}/close?exit_price_a={a}&exit_price_b={b}&notes={notes}"
   ```

## Arguments:
- `open` — show open trades
- `close {id}` — close a specific trade
- `{signal_id}` — open trade from a signal ID directly
