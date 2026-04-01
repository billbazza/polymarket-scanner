"Write a Python function called ask() that takes a natural language question, loads the wallet index JSON, finds the 15 most relevant wallets using keyword matching and stats filtering, passes them to Claude as context, and returns Claude's analysis with specific wallet addresses and recommendations. Make it a single file that I can run from terminal."
 Detect insider activity. Map which wallets exploit weather data, NOAA forecasts, sports odds, political polling shifts. Build me a weekly report with actionable intelligence.
What he actually delivered: a spreadsheet with 200 wallets scanned per week. 2-3 worth following. No correlations because he couldn't cross-reference fast enough. 
No insider detection because by the time he found a suspicious wallet, the move had already happened. No strategy reverse-engineering because he couldn't process enough data to identify patterns.
Polymarket has over 1.3 million wallet addresses. He saw 0.015% of the market. 80% of participants lose money. The profitable 7.6% use systematic advantages that a human simply cannot replicate manually. Speed, scale, pattern recognition across millions of data points.
Then I built a RAG system over one weekend. Total cost: $12. It does more in 4 minutes than he did in an entire week.
Image
What RAG Actually Is 

Forget the acronym. Here is what it does in practice.
You have millions of rows of Polymarket trading data. Too much for any person to read. Too much for Claude to process in one conversation. Claude is context window holds roughly 50,000rows. 
That is 0.06% of the full dataset.
RAG works like a research librarian. You organize the data into summaries. You store them in a searchable index. When you ask a question, the system pulls the 10 most relevant summaries and hands them to Claude. Claude reads those 10 summaries and gives you a specific answer with addresses, statistics, and recommendations.
You do not need to understand embeddings, vector math, or database internals. You need pip install and 20 minutes.
Image
The Plan

Image
Total: one afternoon. Cost: ~$12 for Claude API calls. After that, each question costs about $0.02.
Step 1: Get the Data
Polymarket operates on the Polygon blockchain, making every trade publicly accessible. Use open-source tools to download and structure this data into CSV files. The required dataset should include:
>Every trade
>Every wallet address
>Every market
>Every timestamp
The Polymarket API provides market data and trade history. Additionally, community projects on GitHub offer pre-processed, ready-to-use datasets. Search for terms like "polymarket data retriever" or "polymarket trades csv" to find reliable options.
Result: a CSV file containing columns such as timestamp, market_id, wallet, side (buy/sell), size, price. This dataset will include tens of millions of rows, covering every trade ever made on the platform.
Step 2: Turn Raw Trades Into Wallet Profiles
Millions of individual trades are noise. The signal is in wallet-level aggregation. Group every trade by wallet address, then compute what matters for each one:

import polars as pl

df = pl.read_csv('trades.csv')

# Group by wallet and compute key metrics
profiles = df.group_by('wallet').agg([
    pl.col('size').count().alias('trade_count'),
    pl.col('size').sum().alias('total_volume'),
    pl.col('market_id').n_unique().alias('markets_traded'),
])

# Keep wallets with 50+ trades (filter noise)
active = profiles.filter(pl.col('trade_count') >= 50)

# Result: ~23,000 active wallets from 1M+ total
For each wallet you also need: win rate, Sharpe ratio, maximum drawdown, which market categories they focus on (politics, sports, crypto, weather), average hold time, and whether the pattern looks human or automated.
The Polars library handles this efficiently. It processes tens of millions of rows in seconds on a regular laptop. No cloud infrastructure needed.
Step 3: Claude Writes a Profile for Each Wallet
This is the core of the system. Each wallet&#39;s statistics become a text description that captures its trading behavior, strategy type, and edge quality. This text is what gets searched later when you ask questions.

import anthropic

client = anthropic.Anthropic()

def profile_wallet(stats: dict) -> str:
    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=250,
        messages=[{
            'role': 'user',
            'content': f'''
Write a 3-4 sentence analytical profile of this
Polymarket wallet. Focus on: strategy type, market
category specialization, edge sustainability,
and whether behavior is automated or manual.

Address: {stats['wallet']}
Trades: {stats['trades']} | Win rate: {stats['wr']}%
Sharpe: {stats['sharpe']} | Total P&L: ${stats['pnl']:,.0f}
Max drawdown: {stats['dd']}% | Avg hold: {stats['hold']}
Categories: {stats['cats']}
'''
        }]
    )
    return response.content[0].text
Example Output

0x7a3f operates as a weather-specialized automated system
with 89% win rate across 1,247 trades (Sharpe 3.1). Primary
strategy: exploits the latency between NOAA forecast
publication and Polymarket price adjustment, entering
positions 9-15 minutes before data becomes priced in.
Edge appears stable with no degradation over 90 days.
Execution pattern is clearly algorithmic: entries cluster
in 3-second bursts suggesting automated pipeline
Run this for all 23,000 active wallets. It takes 2-3 hours because of API rate limits. Start the script, go do something else, come back to a complete index.
Step 4: Build a Searchable Index (No Database Required)
You do not need a vector database. A simple JSON file with wallet profiles is enough for most use cases. When you ask a question, Claude searches through the profiles using its own reasoning.

import json

# Save all profiles to a single JSON file
index = {}
for wallet in active_wallets:
    stats = compute_stats(wallet)
    profile = profile_wallet(stats)
    index[stats['wallet']] = {
        'profile': profile,
        'wr': stats['wr'],
        'sharpe': stats['sharpe'],
        'pnl': stats['pnl'],
        'trades': stats['trades'],
        'categories': stats['cats'],
    }

with open('polymarket_index.json', 'w') as f:
    json.dump(index, f)

# That's it. ~15MB file. Your entire index.
For advanced users: if your index grows beyond 50,000 wallets, consider using a lightweight search library like whoosh or sqlite with FTS5. Both are pip install away and require zero configuration.
Step 5: Ask Questions, Get Intelligence
The query function loads your index, finds relevant profiles based on your question, and passes them to Claude with instructions to analyze and recommend.

def ask(question: str) -> str:
    index = json.load(open('polymarket_index.json'))
    
    # Simple keyword matching + stats filtering
    relevant = []
    for addr, data in index.items():
        if any(kw in data['profile'].lower() for kw in question.lower().split()):
            relevant.append((addr, data))
    
    # Sort by Sharpe, take top 15
    relevant.sort(key=lambda x: x[1]['sharpe'], reverse=True)
    
    context = '\n'.join([
        f"Wallet {a}: WR {d['wr']}%, Sharpe {d['sharpe']},"
        f" P&L ${d['pnl']:,.0f}\n{d['profile']}"
        for a, d in relevant[:15]
    ])
    
    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=1000,
        messages=[{
            'role': 'user',
            'content': f'Polymarket wallet intelligence:\n{context}'
                       f'\n\nAnalysis request: {question}'
                       f'\nProvide addresses, numbers, and assessment.'
        }]
    )
    
    return response.content[0].text

# Usage
print(ask('reverse-engineer weather trading strategies'))
Image
5 Prompts I Run Every Morning

These are the exact queries I feed to the system daily. Each one replaced a specific task my analyst spent hours on.
1. Strategy Reverse-Engineering

"Identify the 5 most common profitable strategies across all wallets with Sharpe above 2.0. For each strategy: name it, describe the pattern, list the top 3 wallets executing it, and estimate whether the edge is growing, stable, or decaying."
Result: 14 distinct strategies mapped. NOAA weather lag (89% WR), event front-running (76% WR), NegRisk arbitrage (100% WR, low margin), 5-min BTC momentum (81% WR, edge decaying), political polling correlation (74% WR)
2. Insider Detection (OSINT)

"Flag wallets created in the last 72 hours that entered positions above $5,000 in low-liquidity markets. Cross-reference with market category, position size relative to market volume, and timing relative to any public news. Calculate insider probability."
Result: 12 wallets flagged. 3 with 87%+ insider probability. One entered a political market 4 hours before a major announcement. $35,000 position. Zero prior trading history.
3. Edge Decay Analysis

"For each wallet I currently copy, compare last 30-day performance vs previous 90-day average. Flag any wallet where win rate dropped more than 10% or Sharpe fell below 1.5. Estimate remaining edge life."
Result: Saved me from a decaying strategy. One wallet's BTC lag arbitrage window compressed from 12.3 to 2.7 seconds. Claude recommended stop copying immediately.
4. Correlation Mapping

"Which external data sources correlate with profitable trades? Check: NOAA forecasts, BTC spot price feeds (Binance/Coinbase), polling aggregators, and major news wires. Show time lag between source publication and Polymarket price adjustment."
Result: NOAA: 9-15 minute lag. Binance BTC: 2.7 second lag (was 12.3 in 2024). Polling aggregators: 4-8 hour lag on political markets. Reuters/AP: 90-second lag on breaking news events.
5. Morning Briefing

"60-second briefing. What changed overnight? Unusual whale activity? New insider patterns? Markets with sudden volume spikes? Any of my copy targets made unexpected moves? Rank by urgency."
My analyst needed 2 hours every morning for this. The RAG system does it in 3 minutes. And it checks all 23,000 active wallets, not 200.
Prompts to Build This RAG System Yourself

You do not need to write a single line of code manually. Give Claude these prompts and it will build the entire system for you. Each prompt is designed to produce working, copy-paste ready code.
Prompt A: Data Processing Script

"Write a Python script that reads a CSV file of Polymarket trades (columns: timestamp, market_id, wallet, side, size, price). Group by wallet. For each wallet compute: trade count, win rate, Sharpe ratio, max drawdown, dominant category, and average hold time. Filter to wallets with 50+ trades. Save as JSON. Use Polars library."
Prompt B: Wallet Profiler

"Write a Python script that reads the wallet JSON from Prompt A. For each wallet, call Claude API (anthropic library) and generate a 3-sentence analytical profile covering: strategy type, market specialization, edge quality, automated vs manual. Save all profiles back into the same JSON file. Include progress bar and rate limiting."
Prompt C: Query Engine

"Write a Python function called ask() that takes a natural language question, loads the wallet index JSON, finds the 15 most relevant wallets using keyword matching and stats filtering, passes them to Claude as context, and returns Claude's analysis with specific wallet addresses and recommendations. Make it a single file that I can run from terminal."
Paste each into Claude, run the output. Total build time: under 30 minutes if you already have the data.
The Numbers: $2,000/month Human vs $12 System

Image
The analyst is a smart person. He has good intuition, understands market structure, reads news fluently. But he is one person with one pair of eyes and 8 working hours.
The RAG system has no intuition. It has no market feel. But it processes 23,000 wallets in 4 minutes, cross-references every data source simultaneously, and never takes a day off.
For $12.
Common Issues and Solutions
Claude returns vague answers. Your wallet profiles are too generic. Add more numbers to the profiling prompt: exact P&L, exact dates, exact market names. The more specific the profile, the better Claude retrieves and reasons.
Building profiles takes too long. Use Claude Haiku instead of Sonnet for the profiling step. 3x faster, significantly cheaper, quality is sufficient for profile generation. Save Sonnet for the query step where reasoning quality matters most.
Data gets stale. Set up a daily cron job to re-download recent trades and update profiles for wallets that traded in the last 24 hours. You only re-profile active wallets, not all 23,000.
Search misses relevant wallets. Keyword matching is simple but limited. For better results, add category tags to each profile and filter by category before keyword search. Or upgrade to a proper embedding search using the sentence-transformers library (pip install sentence-transformers)
