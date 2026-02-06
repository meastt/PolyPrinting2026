#!/usr/bin/env python3
"""
Weather Market Scanner - Fetch and parse Kalshi weather markets

Scans for:
- KXHIGH* (high temperature markets)
- KXLOW* (low temperature markets)

For cities: NYC, Boston, Chicago, LA, SF, Austin, Denver, Miami
"""

import os
import sys
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

# Add trader path for Kalshi API utilities
sys.path.append("/app/trader")

from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import requests

load_dotenv()

logger = logging.getLogger(__name__)

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "/app/keys/private_key.pem")
KALSHI_USE_DEMO = os.getenv("KALSHI_USE_DEMO", "true").lower() == "true"
KALSHI_BASE_URL = "https://demo-api.elections.kalshi.com" if KALSHI_USE_DEMO else "https://api.elections.kalshi.com"


class WeatherMarketScanner:
    """Scanner for Kalshi weather markets."""

    # Weather series to scan
    WEATHER_SERIES = [
        "KXHIGHNY", "KXLOWNY",      # NYC
        "KXHIGHBOS", "KXLOWBOS",    # Boston
        "KXHIGHCHI", "KXLOWCHI",    # Chicago
        "KXHIGHLA", "KXLOWLA",      # LA
        "KXHIGHSF", "KXLOWSF",      # SF
        "KXHIGHAUS", "KXLOWAUS",    # Austin
        "KXHIGHDEN", "KXLOWDEN",    # Denver
        "KXHIGHMIA", "KXLOWMIA",    # Miami
    ]

    def __init__(self):
        self.api_key_id = KALSHI_API_KEY_ID
        self.private_key_path = KALSHI_PRIVATE_KEY_PATH
        self.base_url = KALSHI_BASE_URL

        if not self.api_key_id:
            raise ValueError("KALSHI_API_KEY_ID not set")

        if not Path(self.private_key_path).exists():
            raise ValueError(f"Private key not found: {self.private_key_path}")

        logger.info(f"WeatherMarketScanner initialized ({'DEMO' if KALSHI_USE_DEMO else 'LIVE'})")

    def _load_private_key(self):
        """Load RSA private key."""
        with open(self.private_key_path, 'rb') as f:
            return serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )

    def _sign_request(self, method: str, path: str, timestamp: int) -> str:
        """Sign API request with RSA-PSS."""
        private_key = self._load_private_key()
        message = f"{timestamp}{method}{path}".encode()

        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )

        import base64
        return base64.b64encode(signature).decode()

    def _kalshi_request(self, method: str, path: str, data: dict = None) -> dict:
        """Make authenticated Kalshi API request."""
        url = f"{self.base_url}{path}"
        timestamp = int(time.time() * 1000)

        # Strip query params for signature
        path_for_signature = path.split('?')[0]

        headers = {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": self._sign_request(method, path_for_signature, timestamp),
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
            "Content-Type": "application/json"
        }

        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=10)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=10)
            else:
                return {}

            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error(f"Kalshi API error: {e}")
            return {}

    def get_weather_markets(self, status: str = "open") -> List[Dict]:
        """
        Fetch all active weather markets from Kalshi.

        Args:
            status: Market status filter ("open", "closed", "all")

        Returns:
            List of market dicts
        """
        markets = []

        for series in self.WEATHER_SERIES:
            try:
                path = f"/trade-api/v2/markets?series_ticker={series}&status={status}"
                result = self._kalshi_request("GET", path)

                for m in result.get("markets", []):
                    parsed = self._parse_market(m)
                    if parsed:
                        markets.append(parsed)

                # Rate limiting
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"Error fetching {series}: {e}")
                continue

        logger.info(f"Fetched {len(markets)} weather markets")
        return markets

    def _parse_market(self, market_raw: Dict) -> Optional[Dict]:
        """
        Parse raw Kalshi market into standardized format.

        Args:
            market_raw: Raw market dict from API

        Returns:
            Parsed market dict or None
        """
        try:
            ticker = market_raw["ticker"]
            title = market_raw.get("title", "")
            subtitle = market_raw.get("subtitle", "")

            # Parse temperature range from subtitle
            # Example: "Between 68-69°F" or "68 to 69 degrees"
            temp_range = self._extract_range(subtitle)

            # Parse times
            close_time = market_raw.get("close_time")
            expiration_time = market_raw.get("expiration_time")

            # Calculate hours to close
            hours_to_close = None
            if close_time:
                try:
                    close_dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    hours_to_close = (close_dt - now).total_seconds() / 3600
                except Exception:
                    pass

            return {
                "ticker": ticker,
                "title": title,
                "subtitle": subtitle,
                "range": temp_range,
                "close_time": close_time,
                "expiration_time": expiration_time,
                "hours_to_close": hours_to_close,
                "status": market_raw.get("status", "unknown"),
                "volume": market_raw.get("volume", 0),
                "open_interest": market_raw.get("open_interest", 0),
                "yes_ask": market_raw.get("yes_ask", 0) / 100 if market_raw.get("yes_ask") else None,
                "yes_bid": market_raw.get("yes_bid", 0) / 100 if market_raw.get("yes_bid") else None,
                "no_ask": market_raw.get("no_ask", 0) / 100 if market_raw.get("no_ask") else None,
                "no_bid": market_raw.get("no_bid", 0) / 100 if market_raw.get("no_bid") else None,
                "rules_primary": market_raw.get("rules_primary", ""),
            }

        except Exception as e:
            logger.error(f"Error parsing market: {e}")
            return None

    def _extract_range(self, subtitle: str) -> Optional[str]:
        """
        Extract temperature range from subtitle.

        Args:
            subtitle: Market subtitle text

        Returns:
            Range string like "68-69" or None
        """
        import re

        # Try various patterns
        patterns = [
            r"(\d+)\s*-\s*(\d+)",           # "68-69"
            r"(\d+)\s+to\s+(\d+)",          # "68 to 69"
            r"Between\s+(\d+)\s*-\s*(\d+)", # "Between 68-69"
            r"(\d+)\s*°[Ff]?\s*-\s*(\d+)",  # "68°F-69°F"
        ]

        for pattern in patterns:
            match = re.search(pattern, subtitle)
            if match:
                low = match.group(1)
                high = match.group(2)
                return f"{low}-{high}"

        return None

    def get_tradeable_markets(self, min_hours: float = 2.0, max_hours: float = 24.0) -> List[Dict]:
        """
        Filter for tradeable markets (closing within time window).

        Args:
            min_hours: Minimum hours until close (avoid last-minute trades)
            max_hours: Maximum hours until close (avoid far-future markets)

        Returns:
            List of tradeable markets
        """
        all_markets = self.get_weather_markets(status="open")

        tradeable = [
            m for m in all_markets
            if m.get("hours_to_close") is not None
            and min_hours <= m["hours_to_close"] <= max_hours
        ]

        logger.info(f"Found {len(tradeable)} tradeable markets (between {min_hours}-{max_hours} hours)")
        return tradeable

    def get_market_orderbook(self, ticker: str) -> Optional[Dict]:
        """
        Get current orderbook for a market.

        Args:
            ticker: Market ticker

        Returns:
            Orderbook dict with bids/asks
        """
        try:
            path = f"/trade-api/v2/markets/{ticker}"
            result = self._kalshi_request("GET", path)

            market = result.get("market", {})

            return {
                "ticker": ticker,
                "yes_ask": market.get("yes_ask", 0) / 100 if market.get("yes_ask") else None,
                "yes_bid": market.get("yes_bid", 0) / 100 if market.get("yes_bid") else None,
                "no_ask": market.get("no_ask", 0) / 100 if market.get("no_ask") else None,
                "no_bid": market.get("no_bid", 0) / 100 if market.get("no_bid") else None,
                "volume": market.get("volume", 0),
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"Error fetching orderbook for {ticker}: {e}")
            return None
