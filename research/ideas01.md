Here's what the data shows: 87% of prediction market wallets lose money. But the top 13% aren't lucky - they're using a specific set of mathematical frameworks that most traders don't even know exist.
This article breaks down the 5 game theory formulas that separate winners from losers in prediction markets. 

Each one comes with the math, the real-world example, and Python code you can run today.

Example of traders using these formulas in their strategies:
• RN - Polymarket algo bot that made +$6M PnL on Sports using the models from this article. Profile: https://polymarket.com/@rn1?r=following#EL2kxhb

￼
• Distinct-baguette turned 560$ → $812K market-making UP/DOWN markets. Profile: https://polymarket.com/profile/%40distinct-baguette?r=following#N8G8PGV

￼
Many other traders and bots use these formulas daily to shift their approach from gambling to a precise, math-driven strategy.

1) Expected Value - the main formula 

Every decision you make on Polymarket is an expected value calculation. Most traders do it with their gut. The top 13% do it with math.
EV tells you whether a bet is worth taking, regardless of the outcome of any single trade. It's the average return you'd get if you made the same bet a thousand times.

￼
EXPECTED VALUE FORMULA
EV = (Pwin × Payout) - (Plose × Cost)
Pwin = your estimated probability of winning
Plose = 1 - Pwin
Payout = what you receive if the contract settles YES
Cost = what you paid for the contract


Real example on Polymarket:
A contract asks: "Will Bitcoin hit $150K by June 2026?" The YES price is 12¢ . That implies the market thinks there's a 12% chance.
But you've done your research - on-chain data, halving cycle analysis, ETF flows - and you estimate the real probability is 20%. Should you buy?






0:04 / 0:07










Run the EV calculation: 
java
EV = (0.20 × $0.88) + (0.80 × −$0.12) = $0.176 − $0.096 = +$0.08 per contract
Positive EV ⭢ Every contract you buy at 12¢ earns you 8¢ on average. Buy 100 contracts = $8 expected profit on a $12 investment. That's +66.7% ER.
But here's what the research found: most traders on prediction markets don't calculate EV at all. 

They bet because "Bitcoin always pumps" or "my gut says YES." That's why the average taker loses 1.12% per trade across 72 million trades

Python: EV Calculator for Polymarket
python
# Expected Value Calculator for Polymarket

def calculate_ev(market_price, your_probability):
    """
    market_price: current YES price (0.01 to 0.99)
    your_probability: your estimated true probability
    Returns: expected value per $1 risked
    """
    cost = market_price
    payout = 1.0 - market_price  # profit if YES wins
    
    ev = (your_probability * payout) - ((1 - your_probability) * cost)
    roi = ev / cost * 100
    
    return {
        "ev_per_contract": round(ev, 4),
        "roi_percent": round(roi, 2),
        "verdict": "BUY ✅" if ev > 0 else "SKIP ❌"
    }

# Example: BTC $150K contract at 12¢, you think 20%
result = calculate_ev(0.12, 0.20)
print(f"EV per contract: ${result['ev_per_contract']}")
print(f"ROI: {result['roi_percent']}%")
print(f"Verdict: {result['verdict']}")

# Output:
# EV per contract: $0.08
# ROI: 66.67%
# Verdict: BUY ✅
The key insight from 72M trades: Takers (people who market-buy) lose an average of -1.12% per trade. 

Makers (people who set limit orders) gain +1.12%. The difference isn't information - it's patience. Makers wait for positive EV. Takers act on impulse.

2) Mispricing formula - сheap contracts trap

The longshot bias is the most expensive mistake in prediction markets. Traders systematically overpay for low-probability outcomes. 
* A contract priced at 5 cents should win 5% of the time. On Kalshi, it wins only 4.18% - that's a -16.36% mispricing.
* At the extreme: 1¢ contracts should win 1% of the time. For takers, they win only 0.43%. That's a -57% mispricing. 






0:02 / 0:07









The chart above shows the calibration curve. Green dashed line is "perfect efficiency" - where actual win rate equals implied probability. The blue line is reality. 

Below 20¢, the blue line dips below the green: contracts win less than they should. Above 80¢, it rises above: contracts win more than they should.
The market is remarkably well-calibrated in the middle (30-70¢). The inefficiency concentrates at the tails - exactly where emotional bettors congregate.

Two Formulas That Reveal Everything
Formula 1: Mispricing (δ): 
Mispricing measures how far a contract's actual win rate deviates from its implied probability. 

￼
MISPRICING FORMULA
Ó = Actual Win Rate - Implied Probability
If a 5¢ contract wins 4.18% of the time: Ó = 4.18% - 5% = -0.82% (overpriced)
If a 95¢ contract wins 95.83% of the time:
Ó= 95.83% - 95% = +0.83% (underpriced)



• Example - 5¢ contracts: 
plaintext
100,000 trades at 5¢ across all resolved markets
4,180 of them resolved YES (won)

Actual win rate = 4,180 / 100,000 = 4.18%
Implied probability = 5 / 100 = 5.00%

δ = 4.18% − 5.00% = −0.82 percentage points
Relative mispricing = −0.82 / 5.00 = −16.36%
You're overpaying by 16.36% on every 5¢ contract.

Formula 2: Gross Excess Return (rᵢ)
While mispricing shows the aggregate bias, gross excess return shows what happens on each individual trade. 

￼

ri = (100 x oi - pi)/pi

oi = outcome (0 or 1)
pi = price in cents

This is where the psychology becomes visible. Let's look at what happens when you buy a 5¢ contract: 
* Scenario A - contract wins:
rᵢ = (100 × 1 − 5) / 5 = 95 / 5 = +1,900% return ( х20 returns ) 
* Scenario B - contract loses: 
rᵢ = (100 × 0 − 5) / 5 = −5 / 5 = −100% return ( 5¢ is gone )
This is exactly why longshots are addictive. When they hit, the return is enormous. +1,900%. 

Your brain remembers that. It tells stories about that. It tweets about that.
But they hit less often than the price implies. And the asymmetry between "lose everything" and "win big" - averaged over thousands of trades -produces a negative expected value. 

You're buying lottery tickets that are priced above their fair value.

How "Mispricing" looks like across every price level: 

PRICE	IMPLIED PROB	ACTUAL WIN RATE	MISPRICING (A)	RETURN ON $1
10	1.00%	0.43% (taker)	-57.0%	$0.43
20	2.00%	1.36%	-32.0%	$0.68
5¢	5.00%	4.18%	-16.4%	$0.84
10c	10.00%	9.20%	-8.0%	$0.92
20¢	20.00%	19.10%	-4.5%	$0.96
50¢	50.00%	48.70%	-2.6%	$0.97
80¢	80.00%	80.50%	+0.6%	$1.01
90¢	90.00%	91.50%	+1.7%	$1.02
95c	95.00%	95.83%	+0.87%	$1.01
99¢	99.00%	99.50%	+0.5%	$1.01

￼
Read the "Return on $1" column. For every dollar you invest in 1¢ contracts as a taker, you get back 43¢. 

For every dollar in 90¢ contracts, you get back $1.02. The pattern is monotonic - the cheaper the contract, the worse the deal.






0:02 / 0:04









The chart above separates the data by role. 
* The red line (Takers) dives to -57% at the left edge.
* The green line (Makers) mirrors it at +57%.
* The purple line (Combined) shows the aggregate market mispricing.
Makers are literally the mirror image of takers - every cent a taker loses, a maker gains.

Python: Detect Mispriced Markets
python
# Scan Polymarket for longshot bias opportunities
import requests

def scan_mispriced_markets():
    """Find markets where longshot bias creates edge"""
    url = "https://gamma-api.polymarket.com/markets"
    params = {"active": "true", "limit": 50,
              "order": "volume24hr", "ascending": "false"}
    
    markets = requests.get(url, params=params).json()
    opportunities = []
    
    for m in markets:
        price = float(m.get("bestAsk", 0))
        
        # Flag longshots (under 10¢) — historically overpriced
        if 0.01 < price < 0.10:
            expected_mispricing = -16 * (0.10 - price) / 0.10
            opportunities.append({
                "market": m["question"][:60],
                "price": price,
                "estimated_mispricing": f"{expected_mispricing:.1f}%",
                "action": "SELL YES (or BUY NO)"
            })
        
        # Flag near-certainties (over 90¢) — historically underpriced
        elif price > 0.90:
            opportunities.append({
                "market": m["question"][:60],
                "price": price,
                "estimated_mispricing": "+underpriced",
                "action": "BUY YES (near-certainty edge)"
            })
    
    return opportunities

for opp in scan_mispriced_markets():
    print(opp)
The game theory takeaway: Low-probability contracts are systematically overpriced. High-probability contracts are systematically underpriced. The smart money sells longshots and buys near-certainties.

3) Kelly Criterion - how much to bet 

You found a positive EV trade on Polymarket. You're 70% confident the market is mispriced. Your bankroll is $5,000. How much do you bet?
If you bet too much, a single loss wipes out weeks of gains. If you bet too little, your edge compounds so slowly it's barely worth the effort. 

Somewhere between "everything" and "nothing" is a mathematically optimal amount.


￼

F*‎ = ( p x b -q )/ b

f* = fraction of bankroll to bet
p = your probability of winning
q = 1 - p (probability of losing)
b = net odds (payout / cost)

That amount has a name. It's called the Kelly Criterion, and it was invented in 1956 by John Kelly Jr. at Bell Labs. 

Originally designed to optimize long-distance telephone signal noise, it turned out to be the most powerful position sizing formula ever discovered for gambling, trading, and - as it turns out - prediction markets.
Every professional poker player, every serious sports bettor, every quant fund on Wall Street uses some version of Kelly. 

Kelly Criterion for Prediction Markets
On prediction markets, the mechanics are slightly different because contracts are binary (pay $1 or $0) and prices directly represent probabilities. 


￼
KELLY CRITERION - PREDICTION MARKET VERSION
f*= (px b- q) /b
f* = optimal fraction of bankroll to bet
p = your estimated true probability of the event
q = 1 - p (probability you're wrong)
b = net odds = (1 - market_price) / market_price
market_price = current YES contract price (0.01 to 0.99)

Let's unpack the { b } term: 

On Polymarket, if you buy a YES contract at 30¢, you risk 30¢ to potentially win 70¢ (the contract pays $1 if YES, so your profit is $1 − $0.30 = $0.70). 

Your net odds are:
at 30¢: b = 0.70 / 0.30 = 2.33 (win $2.33 per $1 risked) 
at 50¢: b = 0.50 / 0.50 = 1.00 (win $1.00 per $1 risked) 
at 10¢: b = 0.90 / 0.10 = 9.00 (win $9.00 per $1 risked) 
at 80¢: b = 0.20 / 0.80 = 0.25 (win $0.25 per $1 risked)
The higher the odds, the more Kelly tells you to bet - if you have edge. 

{ critical rule } - Never use Full Kelly
Full Kelly maximizes the long-run growth rate of your bankroll. Mathematically, it's optimal. In practice, it's a disaster. 

Full Kelly produces drawdowns of 50% or more regularly. 

Over 1,000 bets with genuine edge, full Kelly will eventually make you the most money - but along the way, you'll experience stomach-churning swings that make most humans abandon the strategy entirely.






0:02 / 0:06









The chart above simulates 1,000 bets with a consistent 55% win rate at even odds. 
* Full Kelly (blue) - produces the highest ending bankroll but swings wildly
* Quarter Kelly (green) - grows steadily with manageable drawdowns
* Half Kelly (orange) - sits in between.
* 
* 
Kelly Bet Size - lookup table:
Use this table to quickly estimate your quarter-Kelly bet size without doing the math. Find your probability estimate on the left, the market price on the top, and read the fraction of bankroll.

￼
YOUR PROB 1 / PRICE -	10€	20€	30€	400	50¢	600
20%	2.8%					
30%	5.6%	3.1%				
40%	8.3%	6.3%	3.6%			-
50%	11.1%	9.4%	7.1%	4.2%		
60%	13.9%	12.5%	10.7%	8.3%	5.0%	ー
70%	16.7%	15.6%	14.3%	12.5%	10.0%	6.3%
80%	19.4%	18.8%	17.9%	16.7%	15.0%	12.5%

Production-Ready Kelly Calculator: 
python
# Kelly Criterion for Polymarket — Production Version

class KellyCalculator:
    def __init__(self, bankroll, kelly_fraction=0.25,
                 max_bet_pct=0.05):
        """
        bankroll: total capital
        kelly_fraction: 0.25 = quarter-Kelly (default)
        max_bet_pct: hard cap per position (5% default)
        """
        self.bankroll = bankroll
        self.fraction = kelly_fraction
        self.max_bet_pct = max_bet_pct
    
    def calculate(self, price, your_prob, correlated=False):
        """Calculate optimal bet for a YES contract"""
        b = (1 - price) / price  # net odds
        q = 1 - your_prob
        
        full_kelly = (your_prob * b - q) / b
        
        if full_kelly <= 0:
            # Check NO side
            no_price = 1 - price
            no_prob = 1 - your_prob
            no_b = (1 - no_price) / no_price
            no_kelly = (no_prob * no_b - your_prob) / no_b
            
            if no_kelly > 0:
                return self._build_result(
                    no_kelly, no_price, "NO", correlated
                )
            return {"action": "NO BET",
                    "reason": "No edge on either side"}
        
        return self._build_result(
            full_kelly, price, "YES", correlated
        )
    
    def _build_result(self, fk, price, side, correlated):
        adj = fk * self.fraction
        if correlated:
            adj *= 0.5  # halve for correlated positions
        
        # Hard cap
        adj = min(adj, self.max_bet_pct)
        
        bet = round(self.bankroll * adj, 2)
        contracts = int(bet / price)
        max_profit = round(contracts * (1 - price), 2)
        
        return {
            "side": side,
            "full_kelly": f"{fk*100:.1f}%",
            "adjusted": f"{adj*100:.1f}%",
            "bet": bet,
            "contracts": contracts,
            "max_profit": max_profit,
            "max_loss": bet,
            "risk_reward": f"{max_profit/bet:.1f}x"
        }

# Usage
k = KellyCalculator(bankroll=5000)

print("=== Fed Rate Cut: 30c, you think 45% ===")
print(k.calculate(0.30, 0.45))

print("\n=== BTC $200K: 5c, you think 12% ===")
print(k.calculate(0.05, 0.12))

print("\n=== No edge: 8c, you think 6% ===")
print(k.calculate(0.08, 0.06))

print("\n=== Correlated crypto bet ===")
print(k.calculate(0.30, 0.45, correlated=True))
Calculate your edge (your probability minus the market's implied probability). If edge is positive, Kelly tells you how much to bet. 

4) Bayesian Updating - change mind like pro

Prediction markets move because new information arrives. The question isn't whether your original estimate was right - it's how you update when the evidence changes.
Most traders either ignore new evidence entirely (stubbornness) or overcorrect wildly (panic).

 Bayesian updating gives you the mathematically correct amount to adjust.

￼

BAYES' THEOREM
P(H|E) = P(E|H) × P(H) / P(E)
P(HIE) = your updated belief after seeing the evidence (posterior)
P(EIH) = how likely the evidence is IF your hypothesis is true (likelihood)
P(H) = what you believed before the evidence (prior)
P(E) = how common the evidence is overall (normalizer)

simply: your new belief = how well the evidence fits your theory × your old belief ÷ how common the evidence is in general.
the denominator P(E) is usually expanded using the law of total probability, which gives us the practical version:

￼

PRACTICAL VERSION (WHAT YOU ACTUALLY CALCULATE)
P(H|E) = P(e|H) × P(H) / [P(|н) × P(H) + P(e|-H) ×
P (-H)]
P(EI-H) = how likely the evidence is if your hypothesis is FALSE
P(-H) = 1 - P(H)

Example: Fed Rate Cut on Polymarket

￼
You hold a contract: "Will the Fed cut rates at the June meeting?" The market price is 35 cents, and you agree - your prior is 35%.
Then the monthly jobs report drops. It's much weaker than expected: 120K jobs added vs 200K expected. Unemployment ticks up. Wage growth slows.
1. If the Fed IS going to cut, how likely is a weak jobs report? Pretty likely. A weak economy is exactly why the Fed would cut. Your estimate: 70%.
2. If the Fed is NOT going to cut, how likely is a weak jobs report? Less likely, but possible - weak reports happen even in strong economies. Your estimate: 25%.
* Bayesian Update Calculation:
plaintext
P(cut | weak jobs) = P(weak | cut) × P(cut) / [P(weak | cut) × P(cut) + P(weak | no cut) × P(no cut)]

= 0.70 × 0.35 / [(0.70 × 0.35) + (0.25 × 0.65)]

= 0.245 / [0.245 + 0.1625]

= 0.245 / 0.4075

= 0.601 = 60.1%
One data point: 35% → 60.1%. A shift of +25.1 percentage points.

Likelihood Ratio - Bayes without formula
You don't need to compute the full formula every time. There's a shortcut that professional forecasters use: the likelihood ratio.

￼
LIKELIHOOD RATIO (LR)
LR = P(E|H) / P(E|-H)
If LR > 1, the evidence supports your hypothesis (probability goes up).
If LR ‹ 1, the evidence contradicts it (probability goes down).
If LR = 1, the evidence is irrelevant (probability doesn't change).
You don't need to compute the full formula every time. There's a shortcut that professional forecasters use: the likelihood ratio.
* LR Reference Table for Common Prediction Market Scenarios

￼

EVIDENCE TYPE	LR	STRENGTH	TYPICAL SHIFT
Rumor on social media	12 - 1.5	Very weak	+2 to +5 pp
Credible journalist report	2 - 4	Moderate	+8 to +15 pp
Insider trading activity	3 - 5	Moderate-strong	+10 to +20 pp
Official statement / denial	0.5 - 0.8	Weak negative	-5 to -10 pp
Regulatory filing (SEC, CFTC)	10 - 20	Very strong	+20 to +40 pp
Official confirmation	50-100	Near-conclusive	+30 to +50 pp
Mathematical impossibility	0 (exactly)	Conclusive negative	→ 0%
The chart below shows that the same evidence (LR = 3) has different effects depending on your prior. Starting at 10%, it moves you to 25%. 

Starting at 50%, it moves you to 75%. Starting at 90%, it barely moves you to 96%. Evidence matters most when you're uncertain.






0:03 / 0:06









Production Bayesian updater for Polymarket: 
python
class BayesianTracker:
    def __init__(self, prior, market_name="Unnamed"):
        self.prior = prior
        self.current = prior
        self.market = market_name
        self.history = [{"event": "Initial prior",
                         "posterior": prior, "shift": 0}]
    
    def update(self, p_if_true, p_if_false, evidence_name=""):
        """Single Bayesian update"""
        num = p_if_true * self.current
        den = num + (p_if_false * (1 - self.current))
        posterior = num / den
        shift = posterior - self.current
        
        lr = p_if_true / p_if_false
        
        self.history.append({
            "event": evidence_name,
            "prior": round(self.current * 100, 1),
            "posterior": round(posterior * 100, 1),
            "shift": round(shift * 100, 1),
            "LR": round(lr, 2)
        })
        
        self.current = posterior
        return self
    
    def edge_vs_market(self, market_price):
        """Compare your posterior to market price"""
        diff = self.current - market_price
        if abs(diff) < 0.03:
            return "No edge (within 3pp of market)"
        side = "YES" if diff > 0 else "NO"
        return f"Edge on {side}: your {self.current*100:.0f}% vs market {market_price*100:.0f}%"
    
    def summary(self):
        print(f"\n=== {self.market} ===")
        for h in self.history:
            if "prior" in h:
                direction = "+" if h["shift"] > 0 else ""
                print(f"  {h['event']}: {h['prior']}% -> {h['posterior']}% ({direction}{h['shift']} pp, LR={h['LR']})")
            else:
                print(f"  {h['event']}: {h['posterior']*100:.0f}%")

# Usage: Fed rate cut example
fed = BayesianTracker(0.35, "Fed Rate Cut June")
fed.update(0.70, 0.25, "Weak jobs report")
fed.update(0.60, 0.30, "Dovish Fed speech")
fed.update(0.20, 0.50, "Hot CPI print")
fed.summary()
print(fed.edge_vs_market(0.45))
The traders who beat prediction markets aren't the ones who are right most often. They're the ones who update fastest when the evidence changes. Bayes gives you the exact speed.

5) Nash Equilibrium - poker formula that predicts who wins on Polymarket

In poker, a bluff isn't a guess. It's a calculation. There's a mathematically optimal frequency at which you should bluff - and if you deviate from it, a skilled opponent will exploit you.
The same math applies to prediction markets. Except on Polymarket, the "bluff" is a contrarian trade - going against the crowd when the market is mispriced. And "folding" is being a passive taker who pays the optimism tax.

How Bluff Frequency works in Poker
In No-Limit Hold'em, when you bet, your opponent faces a decision: call or fold. Your bet gives them specific pot odds - the ratio of what they can win to what it costs to call.
If you bet $100 into a $200 pot, your opponent must call $100 to win $300 total. Their pot odds are 100/300 = 33%. They need to win at least 33% of the time to break even on a call.
Here's where Nash Equilibrium enters: your optimal bluff frequency must make your opponent indifferent between calling and folding. 
If you bluff too often, they always call and profit. If you never bluff, they always fold and you never get paid on your value bets.

￼

OPTIMAL BLUFF FREQUENCY (POKER)
Bluff% = Bet / (Bet + Pot)
Bet = the amount you're risking
Pot = what's already in the middle
Result = the frequency at which you should bluff so your opponent can't exploit you
* Poker example: 
plaintext
Pot = $200. You bet $100.

Bluff% = 100 / (100 + 200) = 100 / 300 = 33.3%

For every 2 value bets, you should make 1 bluff.
Your opponent can't exploit this — calling and folding both yield the same EV.

This is Nash Equilibrium: the strategy that can't be beaten by any counter-strategy.

From Poker Bluffs to Contrarian Trades
On a prediction market, the two "players" are Makers (who provide liquidity with limit orders) and Takers (who consume liquidity with market orders). 

The parallel to poker is direct:
* Poker: Bluff ( weak hand bet ) = PM: Contrarian trade (against crowd bet)
* Poker: Value bet (strong hand bet) = PM: Conviction trade (follow market)

The adapted formula for prediction markets:

￼

OPTIMAL CONTRARIAN FREQUENCY (PREDICTION MARKETS)
CF* = Spread / (Spread + Taker Loss)
CF* = optimal contrarian (maker) frequency
Spread = average maker profit per trade (bid-ask spread captured)
Taker Loss = average taker loss per trade (the optimism tax)
From Becker's data: Spread = 2-3¢, Taker Loss = 1.12%
But the more useful formulation comes from the indifference principle. At Nash Equilibrium, the market should make a marginal trader indifferent between being a maker and a taker. This gives us:

￼

NASH EQUILIBRIUM MAKER-TAKER RATIO
Makers = Taker_EV_1oss / (Maker_EV_gain +
Taker_EV_loss)
The fraction of your trades that should be limit orders (maker) to maximize long-run returns.

The Optimal Ratio Changes by Category
Just like a poker player adjusts their bluff frequency against different opponents, your maker-taker ratio should change based on which category you're trading. The data shows dramatically different optimal frequencies.
CATEGORY	TAKER LOSS	MAKER GAIN
GAP		OPTIMAL MAKERS	(
STRATEGY
Finance	-0.08%	0.17
+0.08%
PP		52%	Nearly balanced - edge is thin
Politics	-0.51%	1.02
+0.51%		58%	ttps://x.com/0xMovez/article/
2037499562064073209/media,
2037456077629935616
an
Sports	- 1.11%	2.23
+1.12%
pP		67%	Strong maker preference
Crypto	-1.34%	2.69
+1.34%
pp		70%	Heavily favor maker
Entertainment	-2.40%	4.79
+2.40%
Pp		78%	Almost always maker
World Events	-3.66%	7.32
+3.66%
pp		85%	Default to maker
￼
The poker parallel is exact: against a tight, rational opponent (Finance), you bluff less - they'll catch you. 
Against a loose, emotional opponent (Entertainment, Sports), you bluff more - they overpay for hope and you exploit it by providing liquidity.

How the Equilibrium shifted over time on Prediction markets 
One of the most fascinating findings from Becker's research: the Nash Equilibrium of the market has shifted dramatically. 

In the early days (2021-2023), takers were the winning population. The equilibrium strategy was the opposite of today.






0:02 / 0:07









Before October 2024, the optimal strategy was 60%+ taker - amateur makers were the losing population, and takers captured value from their poorly-priced limit orders. 

After the volume explosion (Q4 2024), professional market makers entered, and the equilibrium flipped. Now the optimal strategy is 65-70% maker.
This is exactly what game theory predicts. As the player pool changes, the equilibrium shifts. A strategy that was optimal against amateurs becomes suboptimal against professionals, and vice versa. The meta evolves.
