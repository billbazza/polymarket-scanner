# FIRMS / Geopolitical Signal Assessment

Date: 2026-04-02

## Executive view

Short version: this concept is plausible as a research aid for event verification, but weakly supported as a durable trading edge for this repo today.

The strongest validated use case is not "predict geopolitics from space." It is narrower:

- use near-real-time thermal anomalies and related geospatial feeds to confirm that a physically observable event likely occurred in a specific place and time;
- combine that with market microstructure and wording checks to decide whether Polymarket has underreacted;
- treat the result as a bounded confidence/risk score for manual review or paper-trading only.

The weakest part of the pitch is the leap from "a satellite or ADS-B anomaly exists" to "there is a tradeable Polymarket edge." That leap is not established by the sources reviewed here.

## Bottom-line recommendation

Recommendation for this repo: `research-only` now, with an optional `paper-trading experiment` later if and only if a narrow market class and a timestamped evaluation dataset can be assembled.

Do not add this to live trading. Do not feed it into existing Kelly sizing. Do not route it into autonomy auto-execution without a separate validation track.

## Repo fit

Against the current architecture in `AGENTS.md`, this idea is not a natural extension of the existing three production strategies:

- cointegration pairs are math-first and reversion-based;
- weather is a well-specified exogenous-data mispricing strategy with directly interpretable probabilities;
- locked arb is mechanical.

The FIRMS/geopolitical idea is different. It is sparse, event-driven, noisy, attribution-heavy, and highly dependent on market wording and latency. That makes it much closer to a research/alerting layer than a production scanner.

## Validated facts vs promotional claims

### Validated facts

- NASA FIRMS provides near-real-time active fire and thermal anomaly detections from multiple sensors. VIIRS global detections are published with stated latency under 3 hours, and some US/Canada products are faster. Source: NASA FIRMS one-pager and FAQ.
- FIRMS explicitly states that the signal alone cannot distinguish the source of a thermal anomaly. Static industrial heat sources, gas flares, volcanoes, and wildfire-like signals can all appear. Source: NASA FIRMS Q&A.
- OpenSky provides crowdsourced ADS-B / Mode S air traffic data, with strongest coverage in Europe and the US, and coverage gaps elsewhere or at low altitude where receiver density is poor. Commercial use requires consent/license. Source: OpenSky FAQ and coverage docs.
- USGS earthquake GeoJSON feeds update every minute and are intended for programmatic real-time use. Source: USGS feed docs.
- Polymarket exposes public market discovery, order book, and WebSocket data, so mapping a signal to a market and checking tradability is feasible from an engineering standpoint. Source: Polymarket docs.

### Promotional or unsupported claims

- "FIRMS gives real-time insight into covert military operations." Overstated. FIRMS can show heat signatures after detectable burning or explosions, but not actor identity, intent, or many non-burning military activities.
- "Thermal anomalies can systematically predict geopolitical markets before they move." Unsupported by the reviewed sources. Some event verification use cases are credible; a persistent tradeable lead over Polymarket is not demonstrated.
- "OpenSky tracks military activity." Only partially true. It tracks aircraft that are broadcasting and within coverage. Non-cooperative, masked, military-restricted, or poorly covered flights will be missed.
- "More sources justify higher Kelly sizing." Unsupported and dangerous. These sources increase complexity and false-positive surface area at least as much as they increase evidence.

## Source-by-source credibility review

### FIRMS

| Source | What it validates | Strength | Main limitations |
|---|---|---:|---|
| [NASA FIRMS one-pager](https://www.earthdata.nasa.gov/s3fs-public/2023-03/FIRMS_OnePager_2022_Prnt-Web.pdf?VersionId=1.pHw_vC487jLkt_aDy.bMvHQxDFC44f) | Product scope, sensor mix, spatial resolution, stated latencies | High | Product summary, not a trading study |
| [NASA FIRMS FAQ](https://www.earthdata.nasa.gov/data/tools/firms/faq) | Operational details, access patterns, latency notes | High | FAQ, not a conflict-specific validation source |
| [NASA FIRMS 2025 Q&A](https://appliedsciences.nasa.gov/sites/default/files/2025-04/FIRMS_Part1_QA.pdf) | Explicit limitations: cannot identify anomaly source from signal alone; clouds/smoke/canopy/view angle affect detection | High | Webinar Q&A rather than formal validation paper |
| [Schroeder et al. 2014 / VIIRS active fire methodology as referenced by NASA](https://doi.org/10.1016/j.rse.2013.08.032) | Algorithmic basis for active fire detection | High | Establishes measurement science, not market edge |
| [Naghizadeh 2024](https://journals.sagepub.com/doi/10.1177/20531680241261769) | Thermal/fire data can supplement conflict-event verification under specific conditions | Medium-High | Academic conflict-use case, not a trading paper |

Assessment:

- Credible for detecting that "something hot/burning happened here."
- Not credible on its own for identifying who caused it, why it happened, or whether a specific market is mispriced.
- Best use is corroboration, not standalone prediction.

### OpenSky

| Source | What it validates | Strength | Main limitations |
|---|---|---:|---|
| [OpenSky FAQ](https://opensky-network.org/about/faq) | Data origin, best coverage in Europe/US, live API, commercial-use restrictions | High | Platform policy and coverage description, not latency benchmarking |
| [OpenSky coverage page](https://opensky-network.org/about/network) | Coverage is receiver-dependent and weaker for low-altitude or poorly instrumented regions | High | Coverage visualization, not a reliability audit |
| [OpenSky historical data docs](https://opensky-network.org/data/trino) | State vector fields, stale-state retention up to 300 seconds, access model | High | Historical interface docs, not event-detection validation |

Assessment:

- Credible for civilian/cooperative aviation patterns in covered regions.
- Weak for "dark" military operations, disputed airspace with sparse receivers, or attribution beyond the aircraft metadata actually visible.
- Licensing/commercial restrictions matter if this ever leaves research mode.

### USGS

| Source | What it validates | Strength | Main limitations |
|---|---|---:|---|
| [USGS GeoJSON summary feed docs](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php) | Real-time feed shape and one-minute update cadence | High | Earthquake-specific only |
| [USGS event API docs](https://earthquake.usgs.gov/fdsnws/event/1/swagger.json) | Query interface, `updatedafter`, recommendation to use real-time GeoJSON feeds for performance | High | Not a prediction source, just an authoritative event feed |
| [USGS feed lifecycle policy](https://earthquake.usgs.gov/earthquakes/feed/policy.php) | Versioning and operational stability expectations | High | Does not solve interpretation or market mapping |

Assessment:

- High credibility for earthquake-triggered event confirmation.
- Useful only for markets explicitly tied to earthquakes, tsunamis, volcanic/seismic knock-on effects, or infrastructure damage narratives that are directly observable.
- Not a general geopolitical feed.

### Market / sentiment inputs

| Source | What it validates | Strength | Main limitations |
|---|---|---:|---|
| [X filtered stream docs](https://docs.x.com/x-api/posts/filtered-stream/introduction) | Near-real-time delivery and access model | Medium | Platform access constraints; content truthfulness not validated |
| [X rate limits docs](https://docs.x.com/x-api/fundamentals/rate-limits) | Operational and billing constraints | Medium | Does not address manipulation risk |
| [GDELT overview](https://www.gdeltproject.org/) | Global news-derived event/tone system, translation and broad coverage | Medium | Measures media coverage "as seen through the eyes of the world's news media," not ground truth |
| [GDELT docs / blog on update cadence](https://blog.gdeltproject.org/web-part-of-speech-dataset-now-updating-every-minute/) | Fast update cadence, machine-processed coverage | Medium | GDELT itself warns that error is unavoidable in global automated extraction |
| [GDELT GEO 2.0 API notes](https://blog.gdeltproject.org/gdelt-geo-2-0-api-debuts/) | Explicit acknowledgement of extraction/geocoding error | Medium | Strong reminder that machine-coded media signals are noisy |
| [Bessi and Ferrara 2016](https://firstmonday.org/ojs/index.php/fm/article/download/7090/5653doi) | Social bots can materially distort online political discussion | Medium-High | Election-context evidence, not Polymarket-specific |

Assessment:

- Good for narrative velocity, rumor detection, and public-information saturation checks.
- Poor as standalone evidence of real-world events.
- Should be treated as context and contamination risk, not as a primary truth source.

### Polymarket data dependencies

| Source | What it validates | Strength | Main limitations |
|---|---|---:|---|
| [Polymarket API introduction](https://docs.polymarket.com/api-reference/introduction) | Public market discovery, data API, CLOB API separation | High | Platform docs, not latency SLA |
| [Polymarket orderbook docs](https://docs.polymarket.com/trading/orderbook) | Public order book access and liquidity visibility | High | Tradability still varies market by market |
| [Polymarket market channel docs](https://docs.polymarket.com/market-data/websocket/market-channel) | Real-time book/price/trade updates via WebSocket | High | Does not guarantee enough depth to exploit a signal |
| [Gamma markets overview](https://docs.polymarket.com/developers/gamma-markets-api/overview) | Market metadata and indexing | High | Hosted metadata layer can lag on some fields vs direct trading state |

Assessment:

- Engineering dependency is solid.
- Economic dependency is the harder problem: many geopolitical markets are thin, episodic, or quickly repriced on public news.
- This repo's existing slippage discipline is directly relevant here.

## Feasibility: can thermal anomaly signals become a tradeable geopolitical edge?

### What looks feasible

- Event confirmation for markets whose resolution is tightly tied to a physically observable destructive event in a known area.
- Cross-verification of low-information conflict zones where public reporting is delayed or censored.
- Alerting on divergence between observed physical signals and Polymarket pricing when the market has not yet fully incorporated the event.

### What looks weak or infeasible

- Predicting diplomatic decisions, ceasefires, elections, sanctions, coups, or leadership moves from thermal anomalies.
- Predicting non-burning military movement.
- Generalizing from one conflict theater to another without regime-specific calibration.
- Assuming any observed anomaly implies a tradable lead rather than a market already informed by faster OSINT channels.

### Why the edge is hard

- FIRMS is often reactive, not predictive. It observes burning, explosions, or heat after they manifest.
- Global VIIRS latency under 3 hours is not "high-frequency" relative to modern news, Telegram, X, and local OSINT channels.
- Conflict-related fires coexist with wildfire, agriculture, gas flaring, steel mills, power plants, and other persistent heat sources.
- Many geopolitical Polymarket contracts are phrased broadly enough that a local anomaly is not resolution-relevant.
- Relevant markets may be illiquid exactly when the signal appears.

### Credible narrow thesis

The most defensible thesis is:

`multi-source physical corroboration may occasionally detect that a market-relevant event likely occurred before Polymarket fully reprices`

That is much narrower than:

`satellite and geopolitical signals can systematically forecast world events`

The first is researchable. The second is mostly marketing language.

## Key risks

### Latency

- FIRMS global VIIRS latency is measured in minutes to hours, not seconds.
- OpenSky visibility depends on receivers and broadcasting behavior.
- By the time multiple sources agree, the market may already have moved.

### False positives and confounds

- FIRMS itself says anomaly source cannot be identified from the signal alone.
- Wildfires, agricultural burns, industrial activity, gas flares, and persistent heat sources are major confounds.
- Social/news sentiment can amplify false narratives around genuine but irrelevant physical signals.

### Attribution risk

- A heat event is not actor attribution.
- A flight anomaly is not mission attribution.
- A seismic event is not necessarily market-relevant geopolitically.

### Liquidity and execution risk

- The market may be too thin for the repo's existing slippage standards.
- Geopolitical contracts can gap violently on public confirmation.
- Spread and depth may make the theoretical edge non-executable.

### Backtest bias

- Easy to select memorable conflicts and work backward.
- Hard to reconstruct what the model and market knew at each minute.
- Survivorship bias in market selection is severe because only some events spawn contracts.

### Unsupported-claim risk

- The concept invites overclaiming because satellite imagery feels "secret" or "intel-like."
- That framing can lead to unjustified confidence, hidden look-ahead bias, and bad sizing decisions.

## Pragmatic scoring framework

The right output for this repo is not a probability estimate suitable for Kelly. It is a bounded `confidence_score` plus a separate `risk_score`.

### Hard rejects

Reject before scoring if any of these are true:

- no directly relevant Polymarket market exists;
- market wording does not map cleanly to the observed event;
- estimated slippage exceeds the repo's 2% rule;
- only a single noisy source exists with no corroboration;
- the signal requires attribution the data cannot support.

### Evidence components

Score each component from 0 to 1, then weight:

| Component | Weight | Practical meaning |
|---|---:|---|
| `market_alignment` | 0.25 | Does the observed event map directly to contract wording and resolution criteria? |
| `source_reliability` | 0.20 | Are the primary sources authoritative for this event type? |
| `cross_source_corroboration` | 0.20 | Do independent sources agree in space and time? |
| `timeliness_advantage` | 0.15 | Is the evidence likely earlier than or under-reflected in market price? |
| `spatial_temporal_specificity` | 0.10 | Is the event localized tightly enough to matter? |
| `tradability` | 0.10 | Is there enough depth and a workable spread? |

Base score:

`confidence_score = 100 * weighted_sum`

### Risk penalties

Compute a separate penalty bucket from 0 to 100:

| Penalty | Suggested range |
|---|---:|
| `confound_risk` | 0-25 |
| `attribution_risk` | 0-20 |
| `news_saturation_risk` | 0-20 |
| `coverage_gap_risk` | 0-15 |
| `backtest_uncertainty` | 0-10 |
| `execution_gap_risk` | 0-10 |

Adjusted score:

`net_signal_score = max(0, confidence_score - 0.6 * risk_penalty)`

### Suggested operating bands

| Net score | Action |
|---|---|
| 0-39 | Ignore |
| 40-59 | Log as research observation only |
| 60-74 | Operator alert, manual review only |
| 75-89 | Paper-trading candidate with fixed tiny size |
| 90-100 | Still paper-only until out-of-sample evidence exists |

### Sizing rule

Do not use Kelly for this strategy class.

If tested at all:

- fixed paper size only;
- if later moved to real money, start with a flat micro-stake unrelated to score magnitude;
- never let confidence score map linearly into bankroll fraction.

Reason: the score is partly epistemic and partly operational. It is not a calibrated win probability.

## Recommended evaluation path

### Stage 1: research only

- Collect example markets where the signal could have mattered.
- Reconstruct minute-level timeline: source publication, first social/news mention, first Polymarket move, peak slippage.
- Label whether the signal added anything beyond public reporting.

### Stage 2: shadow scoring

- Run a non-trading pipeline on future events.
- Store source timestamps, raw evidence, confound flags, and market snapshots.
- Measure lead time versus price response and whether any executable window existed.

### Stage 3: paper-trading only

- Require a minimum number of out-of-sample events in a narrowly defined market class.
- Use fixed stake and manual review.
- Compare against a naive baseline: "buy only after Reuters/AP or official government confirmation" if such timestamps can be captured.

## Recommendation on whether it belongs in the repo

### Best answer

`Research-only branch` is the right default.

### Acceptable next step

`Paper-trading experiment`, but only as an isolated experimental module after stage-1 and stage-2 evidence.

### Not recommended

- inclusion in the current live/autonomy path;
- inclusion as a generic "geopolitical scanner";
- any messaging that implies intelligence-grade predictive capability.

### When to say "not at all"

If the proposal depends on any of these assumptions, it should not be added:

- FIRMS alone can identify military action;
- OpenSky can reliably observe non-cooperative military flights;
- sentiment spikes are trustworthy ground truth;
- sparse anecdotal backtests are enough to justify production rollout.

## Implementation guardrails

If this ever moves past research, these guardrails are needed to avoid breaking current strategies:

- keep it in a separate module such as `geo_scanner.py`; do not modify existing pairs/weather/locked logic as part of the experiment;
- keep a separate table such as `geo_signals_experimental`; do not alter existing `signals`, `weather_signals`, or trade joins until evidence exists;
- do not route experimental signals through `autonomy.py` auto-open logic;
- keep `math_engine.score_opportunity()` untouched for current strategies; this concept needs its own bounded evidence/risk scorer;
- enforce the repo's existing slippage discipline and graceful-degradation behavior;
- require stored raw evidence and timestamps for every scored alert;
- record explicit confound flags such as `wildfire_risk`, `industrial_heat_risk`, `coverage_gap`, `attribution_unverified`;
- treat market/sentiment feeds as secondary evidence only;
- require manual review before any paper trade;
- prohibit live trading unless there is a documented out-of-sample evaluation and explicit user confirmation.

## Final assessment

This concept has enough substance to justify careful research, but not enough evidence to justify production trading.

The credible contribution to this repo is a disciplined, skeptical experiment in event verification and market-underreaction detection. The non-credible version is a hype narrative about "satellite intelligence alpha." The available sources support the first framing and do not support the second.

## Sources

- NASA FIRMS one-pager: https://www.earthdata.nasa.gov/s3fs-public/2023-03/FIRMS_OnePager_2022_Prnt-Web.pdf?VersionId=1.pHw_vC487jLkt_aDy.bMvHQxDFC44f
- NASA FIRMS FAQ: https://www.earthdata.nasa.gov/data/tools/firms/faq
- NASA FIRMS 2025 Q&A: https://appliedsciences.nasa.gov/sites/default/files/2025-04/FIRMS_Part1_QA.pdf
- OpenSky FAQ: https://opensky-network.org/about/faq
- OpenSky coverage: https://opensky-network.org/about/network
- OpenSky historical/Trino docs: https://opensky-network.org/data/trino
- USGS GeoJSON summary feeds: https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php
- USGS event API docs: https://earthquake.usgs.gov/fdsnws/event/1/swagger.json
- USGS feed lifecycle policy: https://earthquake.usgs.gov/earthquakes/feed/policy.php
- Polymarket API introduction: https://docs.polymarket.com/api-reference/introduction
- Polymarket orderbook docs: https://docs.polymarket.com/trading/orderbook
- Polymarket market-data WebSocket docs: https://docs.polymarket.com/market-data/websocket/market-channel
- Polymarket Gamma markets overview: https://docs.polymarket.com/developers/gamma-markets-api/overview
- GDELT overview: https://www.gdeltproject.org/
- GDELT cadence note: https://blog.gdeltproject.org/web-part-of-speech-dataset-now-updating-every-minute/
- GDELT GEO 2.0 API notes: https://blog.gdeltproject.org/gdelt-geo-2-0-api-debuts/
- Naghizadeh 2024: https://journals.sagepub.com/doi/10.1177/20531680241261769
- Bessi and Ferrara 2016: https://firstmonday.org/ojs/index.php/fm/article/download/7090/5653doi
