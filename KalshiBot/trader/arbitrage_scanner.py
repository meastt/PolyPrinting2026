"""
Arbitrage Scanner - The Math Accountant

Responsible for finding "broken vending machines" (logical inconsistencies) in Kalshi markets.
Strategies:
1. Strike Arbitrage (Monotonicity):
   - Logic: A Call option with Strike K1 MUST be more expensive than Call with Strike K2 if K1 < K2.
   - Violation: If Price(K2) > Price(K1), we can Sell K2 and Buy K1 for risk-free credit? 
     - Wait, Kalshi is binary. 
     - "BTC > 90k" (YES) vs "BTC > 95k" (YES).
     - If BTC > 95k, it IS > 90k.
     - So Probability(>90k) MUST be >= Probability(>95k).
     - Price(>90k) >= Price(>95k).
     - If Price(>95k) > Price(>90k) -> ARBITRAGE violation.
     - Trade: Sell >95k (expensive), Buy >90k (cheap). 
     - Net credit received (or cheaper cost). And >90k wins whenever >95k wins.
     - If result is 92k: >90k pays $1. >95k expires 0. YOU WIN.
     - If result >95k: >90k pays $1. >95k we sold pays $1 (we lose). Net 0 payout. But we pocketed credit.
     - This is a "Debit Spread" or "Credit Spread" depending on execution, but here it's a strictly dominant strategy.

2. Spread Arbitrage (Basic):
   - YES_Ask + NO_Ask < Fee_Threshold (e.g. 0.94).
   - Buy both. Guaranteed $1 payout.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class ArbitrageScanner:
    def __init__(self):
        pass

    def check_strike_monotonicity(self, markets: list) -> list:
        """
        Find monotonic violations in strike prices within the same series/expiry.
        Returns list of arbitrage opportunities.
        """
        # Group by series (e.g., 'KXBTC-25FEB15')
        series_groups = defaultdict(list)
        
        for m in markets:
            # We need to parse series ticker from 'ticker'
            # Format: KXBTC-25FEB15-B55000
            parts = m['ticker'].split('-')
            if len(parts) < 3:
                continue
            
            # Group ID: KXBTC-25FEB15 (everything except the strike part)
            # Actually, "Strike" series distinct from others?
            # We assume same underlying, same date.
            # Ticker usually: [SYMBOL]-[DATE]-[TYPE][STRIKE]
            # Example: KXBTC-26FEB0417-T87749.99
            
            # Let's group by the first 2 parts: KXBTC-26FEB0417
            group_id = "-".join(parts[:2])
            
            # Parse logic: is it a "Above/Below" or "Range"?
            # Kalshi crypto is typically "Above" (binary).
            # Ticker suffix: B69000 (Above 69000? Or Below? Kalshi 'Strike' usually implies > Strike)
            # Actually need to verify contract specs. 
            # Assuming standard "Price > Strike" (Call) logic for this scanner MVP.
            
            # IMPORTANT: We need numerical strike.
            if m.get('strike') is not None:
                series_groups[group_id].append(m)

        opportunities = []

        for group, items in series_groups.items():
            # Sort by strike price ascending
            items.sort(key=lambda x: x['strike'])
            
            # Iterate and check Price(LowStrike) < Price(HighStrike) -> Violation
            # Remember: Lower strike = Higher probability = Higher Price.
            # Logic: Price(Strike 90k) should be > Price(Strike 95k).
            # Violation: Price(90k) < Price(95k).
            # Trade: Buy 90k, Sell 95k. (Or just Buy 90k because it's insanely cheap relative to 95k? 
            # No, correct arb is Spread, but we can just highlight valid "Buy" opportunities).
            
            # Simple check:
            # If Strike A < Strike B, then YES_Ask(A) must be > YES_Bid(B) (to prevent arb).
            # Actually we look for: YES_Ask(A) < YES_Bid(B) -> Direct arb!
            # Buy A (cheap) from Ask, Sell B (expensive) to Bid. 
            # Since A < B, P(A) > P(B).
            # We own "BTC > A" and sold "BTC > B".
            # If BTC > B, then BTC > A. Both trigger. We pay $1 on B, get $1 on A. Net 0.
            # If A < BTC < B. We get $1 on A. We keep premium on B. PROFIT.
            # If BTC < A. Both 0.
            # So if we pay less for A than we receive for B, it's guaranteed profit?
            # Yes, if Cost(A) < Credit(B).
            
            for i in range(len(items) - 1):
                low_strike = items[i]
                high_strike = items[i+1]
                
                # Check for arb window
                # Buy Low_Strike (Ask)
                # Sell High_Strike (Bid)
                # Note: Currently market_scanner only gets "market_price" (Ask? Last?). 
                # We need Bid/Ask for true arb. Scanner returns 'yes_bid', 'yes_ask'.
                
                ask_low = low_strike.get('yes_ask', 0)
                bid_high = high_strike.get('yes_bid', 0)
                
                if ask_low == 0 or bid_high == 0:
                    continue
                    
                # Arb condition: We can buy "High Prob" leg CHEAPER than we can sell "Low Prob" leg.
                if ask_low < bid_high:
                    edge = bid_high - ask_low
                    opportunities.append({
                        "type": "STRIKE_ARB",
                        "ticker_buy": low_strike['ticker'],
                        "ticker_sell": high_strike['ticker'],
                        "strike_buy": low_strike['strike'],
                        "strike_sell": high_strike['strike'],
                        "buy_price": ask_low,
                        "sell_price": bid_high,
                        "raw_profit": edge,
                        # Rough fee adj (taker buy ~7%, maker sell ~2%?)
                        "net_profit_est": edge - 0.05 # Conservative fee buffer
                    })

        return opportunities

    def check_spread_arb(self, markets: list) -> list:
        """
        Check for cases where YES + NO cost < $1.00 (minus fees).
        """
        opportunities = []
        for m in markets:
            yes_ask = m.get('yes_ask', 0)
            no_ask = m.get('no_ask', 0)
            
            if yes_ask == 0 or no_ask == 0:
                continue
                
            total_cost = yes_ask + no_ask
            
            # Fee threshold: 
            # Taker fee is roughly 3.5% each side? Total ~7%.
            # Safe threshold: 0.93
            if total_cost < 0.93:
                opportunities.append({
                    "type": "SPREAD_ARB",
                    "ticker": m['ticker'],
                    "yes_price": yes_ask,
                    "no_price": no_ask,
                    "total_cost": total_cost,
                    "guaranteed_profit": 1.0 - total_cost - 0.07
                })
                
        return opportunities
