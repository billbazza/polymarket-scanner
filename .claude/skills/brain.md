---
name: brain
description: Use Claude AI to estimate probabilities or validate signals
user_invocable: true
---

# /brain — AI Probability Estimation

Use Claude (Haiku) to estimate market probabilities or validate trading signals.

## Prerequisites:
- `ANTHROPIC_API_KEY` must be set in `/Users/will/Obsidian-Vaults/polymarket-scanner/.env`
- `anthropic` package must be installed

## Steps:

### Validate a signal:
1. Get the signal details:
   ```bash
   curl -s "http://localhost:8899/api/signals?limit=10"
   ```
2. Pick a signal and validate:
   ```bash
   curl -s -X POST "http://localhost:8899/api/brain/validate/{signal_id}"
   ```
3. Present Claude's assessment: TRADE or SKIP, reasoning, risk flags

### Batch estimate (from Python):
```bash
cd /Users/will/Obsidian-Vaults/polymarket-scanner && python3 -c "
import brain, db
signals = db.get_signals(limit=5, status='new')
enriched = brain.estimate_batch(signals)
for s in enriched:
    b = s.get('brain', {})
    edge = b.get('max_edge', 0)
    print(f'{s[\"event\"][:40]} | edge={edge:.1%} | has_edge={b.get(\"has_edge\")}')
"
```

### Update prompt:
Current prompt is at `prompts/v1_probability.txt`. To iterate:
1. Copy to `prompts/v2_probability.txt`
2. Update `PROMPT_VERSION = "v2"` in `brain.py`
3. Test on a few signals and compare results

## Arguments:
- `{signal_id}` — validate a specific signal
- `batch` — estimate probabilities for all recent new signals
- `prompt` — show the current prompt template
