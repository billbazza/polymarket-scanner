# 2026-04-01 X Article Research Review

## Scope
- Reviewed [`research/x-article.md`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/research/x-article.md), [`research/x-article2.md`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/research/x-article2.md), and [`research/x-article3.md`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/research/x-article3.md).
- Applied the repo contract in [`AGENTS.md`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/AGENTS.md), especially:
  - Architecture: new work should fit the scanner → math → brain → execution → db flow where possible.
  - Rules / Never Do: no live-trading changes, no execution shortcuts, no skipping error recovery.
  - Scoring Pipeline: ideas are only useful here if they become structured, rankable signals rather than vague narratives.

## Bottom Line
- Most of the material is marketing-heavy and evidence-light.
- The strongest usable idea is not "AI magic"; it is expanding the repo's existing wallet-intelligence path with better wallet-level features and retrieval.
- The second-best idea is maker-side microstructure research, but only as paper-only market-structure measurement first. The article's profit claims are not enough to justify a strategy build.
- The MCP article is almost entirely tooling noise for this repo.

## Ranked Ideas

| Rank | Idea | Plausibility | Expected edge | Complexity | Repo fit | Recommendation |
|---|---|---:|---:|---:|---:|---|
| 1 | Wallet profiling + searchable wallet intelligence from historical trades | 4/5 | 3/5 | 3/5 | 5/5 | Immediate research note, then small paper-only experiment |
| 2 | Wallet pattern drift / edge decay tracking for watched wallets | 4/5 | 3/5 | 2/5 | 5/5 | Immediate experiment |
| 3 | Event/news/forecast lag measurement as a generic "external data latency" framework | 3/5 | 3/5 | 3/5 | 4/5 | Research note, then targeted experiments by category |
| 4 | Maker-only liquidity capture / queue-priority strategy | 3/5 | 4/5 if real | 5/5 | 3/5 | Research only for now |
| 5 | Insider/whale detection from wallet creation + low-liquidity timing patterns | 2/5 | 2/5 | 3/5 | 4/5 | Fold into diagnostics, not a trading strategy yet |
| 6 | MCP integrations as alpha source | 1/5 | 1/5 | 2/5 | 2/5 | Ignore for now |

## Strongest Actionable Ideas

### 1. Wallet intelligence is worth exploring
- This is the only idea with a clear fit to existing code:
  - wallet data collection in [`copy_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/copy_scanner.py)
  - wallet scoring in [`wallet_monitor.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_monitor.py)
  - candidate persistence in [`db.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/db.py)
  - AI summarization / recommendation in [`brain.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/brain.py)
  - auto-discovery in [`wallet_discovery.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_discovery.py)
- Useful extraction from `x-article.md`:
  - aggregate to wallet level, not trade row level
  - classify by category concentration, hold time, timing behavior, and regime drift
  - retrieve a small candidate set before asking the model for synthesis
- What is new relative to current repo:
  - current wallet scoring is mostly heuristic and recent-history based
  - there is no persistent wallet profile index, feature store, or "query top similar/specialist wallets" layer
- Immediate experiment:
  - build a paper-only wallet research pass that stores richer derived fields for watched wallets and discovered candidates:
    - hold-time buckets
    - entry timing vs market creation / major price move
    - category-specialist tags
    - concentration score
    - recent-vs-baseline performance drift
  - then rank candidates with rules first, `brain.recommend_wallet()` second
- Extension points:
  - new wallet profile fields in `watched_wallets` / `wallet_candidates` in [`db.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/db.py)
  - feature computation in [`wallet_monitor.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_monitor.py)
  - retrieval/filtering in [`wallet_discovery.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_discovery.py)

### 2. Edge-decay tracking is more credible than "find insiders"
- `x-article.md` overreaches on insider detection, but its decay-monitoring idea is useful.
- This repo already watches wallets and scores them hourly. A better question is:
  - which watched wallets are losing edge
  - which category-specific edges are stable
  - whether a wallet's timing advantage is shrinking
- Immediate experiment:
  - add a research report that compares each watched wallet's last 30 days vs prior 90 days on:
    - realized/unrealized PnL ratio
    - average trade size
    - category mix
    - trades/month
    - open-position churn
  - demote candidates when deterioration is persistent instead of relying on a one-shot score
- Best fit:
  - [`wallet_monitor.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_monitor.py)
  - [`server.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/server.py) reporting endpoints
  - optional summary text through [`brain.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/brain.py)

### 3. External-data lag is a reusable framework, not one strategy
- The repo already proves this pattern in weather: external truth estimate vs market price in [`weather_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/weather_scanner.py).
- The strongest transferable insight is not "copy NOAA wallets" but:
  - identify categories where an external feed updates before Polymarket reprices
  - measure lag distribution and edge persistence
- Good research directions:
  - sports odds lag
  - economic release lag
  - polling aggregator lag
  - crypto reference-venue lag
- Constraint:
  - the article gives claimed lag numbers but no evidence, methodology, or false-positive analysis
- Recommendation:
  - treat this as a measurement program first, not alpha yet
  - if any category shows repeatable lag windows, then it can become a dedicated scanner following the weather pattern:
    - fetch external source
    - convert to probability
    - compare to market
    - score via `math_engine`
- Extension points:
  - new scanner modules following [`weather_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/weather_scanner.py)
  - signal grading in [`math_engine.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/math_engine.py)
  - storage in new signal tables via [`db.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/db.py)

## Ideas Better Suited For Research Notes Only

### 4. Maker-side liquidity capture
- `x-article3.md` is directionally plausible on market microstructure:
  - maker fee advantage matters
  - queue priority matters
  - merges recycle inventory
  - fills and inventory imbalance are path-dependent, which makes naive backtests unreliable
- But as presented, it is still mostly unsupported:
  - no fill data
  - no queue-position measurement
  - no inventory path analysis
  - no comparison against simpler maker strategies already in the repo
- Repo fit is partial:
  - it aligns with maker assumptions already used in [`longshot_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/longshot_scanner.py) and [`near_certainty_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/near_certainty_scanner.py)
  - it does not fit the current execution model cleanly because live execution is trade-oriented, not quote-engine-oriented
  - there is no order management subsystem for continuous ladder maintenance, replacement, queue tracking, or merge accounting
- Recommendation:
  - do not build this as a live strategy now
  - first run a paper-only microstructure study:
    - record top-of-book snapshots over time
    - estimate spread width, book replenishment, and short-horizon fill likelihood by market type
    - compare maker-edge assumptions in existing scanners to observed book behavior

### 5. Whale / insider detection
- The repo already has a heuristic detector in [`whale_detector.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/whale_detector.py).
- The article's "insider probability" framing is flashy but weak:
  - it assumes intent from timing and size
  - it does not establish base rates
  - it ignores benign explanations like hedging, market making, or public but niche information
- Best use:
  - as an alerting / triage layer for research
  - as context for `brain.validate_signal()`
  - not as a standalone trading edge

## Mostly Noise

### MCP article
- `x-article2.md` is mostly infrastructure promotion, not strategy research.
- Browser automation, Git MCP, Phantom MCP, and similar tools do not create alpha by themselves.
- At best, Dune-style data access could accelerate research workflows, but that is an ops improvement, not a scanner edge.
- Recommendation:
  - no repo work based on this article

## Flashy But Unsupported Claims
- "RAG over a weekend for $12 does more than a human analyst in a week."
- "Find insiders from wallet timing and size with high probability."
- "Exact lag numbers" like NOAA 9-15 minutes or Binance 2.7 seconds without methodology.
- "$650k/month passive BTC bot" style anecdotes.
- "Simple keyword matching plus Claude" as a sufficient research system for millions of rows.

These may contain a kernel of truth, but they should not drive implementation without local measurement.

## Recommended Next Experiments

### Immediate experiments
1. Wallet drift report
- Add a paper-only report that ranks watched wallets by score deterioration and category drift.
- Use existing wallet polling/scoring paths. No execution changes.

2. Wallet profile enrichment
- Add derived wallet features and tags for specialist behavior, then use them in discovery ranking before Claude review.
- Keep it deterministic first; add model-generated summaries only after the features prove useful.

3. External-lag measurement notebook or script
- Pick one category beyond weather and measure publication-to-repricing lag on historical snapshots or forward observation.
- Success criterion: repeated lag windows with enough liquidity to survive slippage assumptions.

### Research backlog
1. Maker microstructure instrumentation
- Snapshot books over time and estimate maker fill probability by market regime.

2. Whale-alert validation study
- Check whether high-suspicion alerts actually precede resolution edge, repricing, or wallet follow-through.

## Suggested Implementation Mapping
- Wallet profile enrichment:
  - [`copy_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/copy_scanner.py)
  - [`wallet_monitor.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_monitor.py)
  - [`wallet_discovery.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/wallet_discovery.py)
  - [`db.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/db.py)
- External-lag scanners:
  - [`weather_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/weather_scanner.py) as template
  - [`math_engine.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/math_engine.py)
  - [`brain.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/brain.py) as optional validation layer
- Maker microstructure research:
  - reuse book-fetching patterns from [`math_engine.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/math_engine.py)
  - compare assumptions with [`longshot_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/longshot_scanner.py) and [`near_certainty_scanner.py`](/Users/will/.cline/worktrees/024ea/polymarket-scanner/near_certainty_scanner.py)

## Final Recommendation
- Worth acting on now:
  - wallet profile enrichment
  - wallet edge-decay reporting
  - one measured external-lag experiment
- Worth parking in research:
  - maker ladder / merge strategy
  - insider-probability claims
  - MCP/tooling ideas

No live-trading behavior changes are justified by these materials as written.
