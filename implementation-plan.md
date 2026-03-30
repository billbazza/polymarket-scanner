# Implementation Plan

## 2026-03-30 Daily Report
1. Implement whale signal trading - 10 alerts with 60+ suspicion scores but zero trades executed
2. Fix signal tracking database - 12 trades missing signal_id preventing proper auto-close
3. Add fallback price fetching for weather markets to handle 404 errors gracefully
4. Increase position sizing on high-confidence weather trades (currently flat $20 per trade)
5. Implement stop-loss on copy trades - holding 50 open positions without exit strategy

## 2026-03-30 Report Queue
- Implement position limits per copy wallet to prevent concentration risk (max 2-3 concurrent)
- Add stop-loss rules for weather trades when forecasts shift unfavorably

- Fix signal reference tracking to enable proper auto-close functionality for all trade types

- Implement maximum open position limit (e.g., 20-25 trades) to force exits and realize profits
