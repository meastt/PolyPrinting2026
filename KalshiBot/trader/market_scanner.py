#!/usr/bin/env python3
"""
Market Scanner - Find Tradeable Kalshi Markets

Scans Kalshi for active crypto prediction markets and identifies
opportunities where our calculated fair value differs from market price.
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Kalshi API configuration
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_USE_DEMO = os.getenv("KALSHI_USE_DEMO", "false").lower() == "true"
KALSHI_BASE_URL = "https://demo-api.elections.kalshi.com" if KALSHI_USE_DEMO else "https://api.elections.kalshi.com"


class MarketScanner:
    """Scans Kalshi for tradeable crypto markets."""
    
    def __init__(self, api_key_id: str, private_key_path: str):
        self.api_key_id = api_key_id
        self.private_key_path = private_key_path
        self._markets_cache = []
        self._last_scan = None
    
    def _make_request(self, method: str, path: str) -> dict:
        """Make unauthenticated request to Kalshi API (public endpoints)."""
        url = f"{KALSHI_BASE_URL}{path}"
        try:
            response = requests.request(method, url, timeout=15)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Kalshi API error: {e}")
            return {}
    
    def _parse_market_response(self, m: dict) -> dict:
        """Helper to parse raw API market dict into internal format."""
        return {
            "ticker": m.get("ticker"),
            "title": m.get("title", ""),
            "subtitle": m.get("subtitle", ""),
            "yes_bid": m.get("yes_bid", 0) / 100,
            "yes_ask": m.get("yes_ask", 0) / 100,
            "no_bid": m.get("no_bid", 0) / 100,
            "no_ask": m.get("no_ask", 0) / 100,
            "volume": m.get("volume", 0),
            "open_interest": m.get("open_interest", 0),
            "close_time": m.get("close_time"),
            "expiration_time": m.get("expiration_time"),
            "floor_strike": m.get("floor_strike"),
            "cap_strike": m.get("cap_strike"),
            "strike": self._parse_strike(m) # Eagerly parse strike
        }

    def get_all_markets(self) -> list[dict]:
        """
        Fetch ALL active markets with pagination.
        Handles rate limits by fetching pages sequentially.
        """
        markets = []
        cursor = None
        page_count = 0
        
        while True:
            path = "/trade-api/v2/markets?status=open&limit=100"
            if cursor:
                path += f"&cursor={cursor}"
                
            try:
                result = self._make_request("GET", path)
                batch = result.get("markets", [])
                cursor = result.get("cursor")
                
                if not batch:
                    break # Stop if no markets returned
                
                for m in batch:
                    markets.append(self._parse_market_response(m))

                # Debug: Log first market to see what data we're getting
                if page_count == 1 and len(markets) > 0:
                    sample = markets[0]
                    logger.info(f"DEBUG Sample market: ticker={sample.get('ticker')}, yes_ask={sample.get('yes_ask')}, yes_bid={sample.get('yes_bid')}")

                if len(markets) > 15000:
                    logger.warning("Global Scan hit safety limit (15000)")
                    break

                page_count += 1
                if page_count % 5 == 0:
                     logger.info(f"Scanning... Fetched {len(markets)} markets so far")

                if not cursor:
                    break
                
                time.sleep(0.5) # Increased sleep for safety
                    
            except Exception as e:
                logger.error(f"Global scan failed: {e}")
                break
                
        logger.info(f"Global Scan: Fetched {len(markets)} active markets")
        return markets

    def get_crypto_markets(self) -> list[dict]:
        """Fetch active crypto prediction markets."""
        markets = []
        series_list = ["KXBTC", "KXBTCD", "INXBTC", "INXD"]
        
        for series in series_list:
            try:
                result = self._make_request("GET", f"/trade-api/v2/markets?series_ticker={series}&status=open")
                for m in result.get("markets", []):
                    markets.append(self._parse_market_response(m))
            except Exception as e:
                logger.warning(f"Failed to fetch series {series}: {e}")
                continue
        
        self._markets_cache = markets
        self._last_scan = datetime.now(timezone.utc)
        logger.info(f"Crypto Scan: Found {len(markets)} active markets")
        return markets
    
    def get_orderbook(self, ticker: str) -> dict:
        """
        Get orderbook for a specific market.
        
        Returns:
        {
            "yes": [(price, quantity), ...],  # Bids for YES
            "no": [(price, quantity), ...],   # Bids for NO
        }
        """
        result = self._make_request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
        
        orderbook = result.get("orderbook", {})
        
        return {
            "yes_bids": [(l[0] / 100, l[1]) for l in orderbook.get("yes", [])],
            "no_bids": [(l[0] / 100, l[1]) for l in orderbook.get("no", [])],
        }
    
    def find_opportunities(self, btc_price: float, min_edge: float = 0.05) -> list[dict]:
        """
        Find markets where our fair value differs from market price.
        
        Args:
            btc_price: Current BTC price from Coinbase
            min_edge: Minimum edge in dollars to consider (default $0.05)
        
        Returns:
            List of opportunities with calculated edge
        """
        from probability import ProbabilityCalculator
        
        calc = ProbabilityCalculator()
        opportunities = []
        
        for market in self._markets_cache:
            # Parse strike price from market title/subtitle
            strike = self._parse_strike(market)
            if strike is None:
                continue
            
            # Calculate time to expiry
            expiry_str = market.get("expiration_time") or market.get("close_time")
            if not expiry_str:
                continue
                
            try:
                expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                hours_to_expiry = (expiry - datetime.now(timezone.utc)).total_seconds() / 3600
                
                if hours_to_expiry <= 0:
                    continue  # Already expired
                    
            except Exception:
                continue
            
            # Calculate fair probability
            fair_prob = calc.btc_above_strike(btc_price, strike, hours_to_expiry)
            fair_price = fair_prob  # Probability = price in dollars
            
            # Compare to market
            market_yes_ask = market.get("yes_ask", 1.0)
            market_yes_bid = market.get("yes_bid", 0.0)
            
            # Edge on buying YES (we think it's underpriced)
            buy_edge = fair_price - market_yes_ask
            
            # Edge on selling YES / buying NO (we think it's overpriced)
            sell_edge = market_yes_bid - fair_price
            
            if buy_edge >= min_edge:
                opportunities.append({
                    "ticker": market["ticker"],
                    "title": market["title"],
                    "side": "BUY_YES",
                    "market_price": market_yes_ask,
                    "fair_price": fair_price,
                    "edge": buy_edge,
                    "strike": strike,
                    "hours_to_expiry": hours_to_expiry,
                })
            elif sell_edge >= min_edge:
                opportunities.append({
                    "ticker": market["ticker"],
                    "title": market["title"],
                    "side": "SELL_YES",
                    "market_price": market_yes_bid,
                    "fair_price": fair_price,
                    "edge": sell_edge,
                    "strike": strike,
                    "hours_to_expiry": hours_to_expiry,
                })
        
        # Sort by edge (best opportunities first)
        opportunities.sort(key=lambda x: x["edge"], reverse=True)
        
        return opportunities
    
    def _parse_strike(self, market: dict) -> Optional[float]:
        """
        Parse strike price from market title.
        
        Examples:
        - "Bitcoin above $76,000?" → 76000
        - "BTC to close above 75.5K" → 75500
        """
        import re
        
        title = market.get("title", "") + " " + market.get("subtitle", "")
        
        # Pattern: $XX,XXX or $XX.XK
        patterns = [
            r'\$([0-9,]+)',           # $76,000
            r'\$([0-9.]+)[Kk]',       # $76.5K
            r'([0-9,]+)\s*(?:USD|dollars)', # 76,000 USD
        ]
        
        for pattern in patterns:
            match = re.search(pattern, title)
            if match:
                value = match.group(1).replace(",", "")
                if "K" in title.upper() and "." in value:
                    return float(value) * 1000
                return float(value)
        
        # Also check floor/cap strikes from API
        if market.get("floor_strike"):
            return float(market["floor_strike"])
        
        return None


if __name__ == "__main__":
    # Test the scanner
    logging.basicConfig(level=logging.INFO)
    
    scanner = MarketScanner(
        api_key_id=KALSHI_API_KEY_ID,
        private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
    )
    
    markets = scanner.get_crypto_markets()
    
    print(f"\nFound {len(markets)} crypto markets:")
    for m in markets[:5]:
        print(f"  {m['ticker']}: {m['title']}")
        print(f"    YES: bid=${m['yes_bid']:.2f} / ask=${m['yes_ask']:.2f}")
        print(f"    Volume: {m['volume']}")
        
    print("\nTesting Opportunity Finder (BTC=$76,500)...")
    opportunities = scanner.find_opportunities(btc_price=76500.0)
    print(f"Found {len(opportunities)} potential trades:")
    for op in opportunities[:5]:
        print(f"  {op['side']} {op['ticker']} (Strike: {op['strike']})")
        print(f"    Market: ${op['market_price']:.2f} | Fair: ${op['fair_price']:.2f} | Edge: ${op['edge']:.2f}")

