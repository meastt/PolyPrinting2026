"""
Historical Data Loader

Fetches and caches historical market data for backtesting.
Supports both API-based data fetching and CSV file loading.
"""

import os
import json
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta
import csv

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class HistoricalMarket:
    """Represents a historical market snapshot."""
    condition_id: str
    question: str
    yes_price: float
    no_price: float
    liquidity: float
    volume: float
    timestamp: float
    resolved: bool
    resolution_outcome: Optional[str] = None  # "Yes" or "No"


@dataclass
class HistoricalPrice:
    """Represents a historical price point."""
    symbol: str
    price: float
    timestamp: float


class DataLoader:
    """
    Loads historical data for backtesting.

    Data sources:
    - Polymarket API (if historical endpoints available)
    - Cached CSV files
    - Simulated data (for testing)
    """

    def __init__(
        self,
        data_dir: str = "data/historical",
        cache_enabled: bool = True,
    ):
        """
        Initialize data loader.

        Args:
            data_dir: Directory for cached data
            cache_enabled: Whether to cache data
        """
        self.data_dir = Path(data_dir)
        self.cache_enabled = cache_enabled

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Cache
        self._market_cache: Dict[str, List[HistoricalMarket]] = {}
        self._price_cache: Dict[str, List[HistoricalPrice]] = {}

        logger.info(f"DataLoader initialized (data_dir={data_dir})")

    def load_market_history(
        self,
        days: int = 30,
        category: Optional[str] = None,
    ) -> List[HistoricalMarket]:
        """
        Load historical market data.

        Args:
            days: Number of days of history
            category: Filter by category

        Returns:
            List of HistoricalMarket objects
        """
        cache_key = f"markets_{days}d_{category or 'all'}"

        # Check cache
        if cache_key in self._market_cache:
            return self._market_cache[cache_key]

        # Try to load from file
        cache_file = self.data_dir / f"{cache_key}.json"
        if cache_file.exists():
            markets = self._load_markets_from_file(cache_file)
            if markets:
                self._market_cache[cache_key] = markets
                return markets

        # Generate simulated data if no real data available
        logger.info("No historical data found, generating simulated data")
        markets = self._generate_simulated_markets(days, category)

        # Cache for future use
        if self.cache_enabled:
            self._save_markets_to_file(markets, cache_file)
            self._market_cache[cache_key] = markets

        return markets

    def load_price_history(
        self,
        symbol: str,
        days: int = 30,
        interval_minutes: int = 1,
    ) -> List[HistoricalPrice]:
        """
        Load historical price data for a crypto asset.

        Args:
            symbol: Asset symbol (e.g., "BTC")
            days: Number of days of history
            interval_minutes: Price interval

        Returns:
            List of HistoricalPrice objects
        """
        cache_key = f"prices_{symbol}_{days}d_{interval_minutes}m"

        # Check cache
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        # Try to load from file
        cache_file = self.data_dir / f"{cache_key}.csv"
        if cache_file.exists():
            prices = self._load_prices_from_file(cache_file)
            if prices:
                self._price_cache[cache_key] = prices
                return prices

        # Generate simulated data
        logger.info(f"Generating simulated price history for {symbol}")
        prices = self._generate_simulated_prices(symbol, days, interval_minutes)

        # Cache
        if self.cache_enabled:
            self._save_prices_to_file(prices, cache_file)
            self._price_cache[cache_key] = prices

        return prices

    def _load_markets_from_file(
        self,
        filepath: Path,
    ) -> List[HistoricalMarket]:
        """Load markets from JSON file."""
        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            return [
                HistoricalMarket(
                    condition_id=m["condition_id"],
                    question=m["question"],
                    yes_price=m["yes_price"],
                    no_price=m["no_price"],
                    liquidity=m["liquidity"],
                    volume=m["volume"],
                    timestamp=m["timestamp"],
                    resolved=m["resolved"],
                    resolution_outcome=m.get("resolution_outcome"),
                )
                for m in data
            ]

        except Exception as e:
            logger.error(f"Failed to load markets from {filepath}: {e}")
            return []

    def _save_markets_to_file(
        self,
        markets: List[HistoricalMarket],
        filepath: Path,
    ) -> None:
        """Save markets to JSON file."""
        try:
            data = [
                {
                    "condition_id": m.condition_id,
                    "question": m.question,
                    "yes_price": m.yes_price,
                    "no_price": m.no_price,
                    "liquidity": m.liquidity,
                    "volume": m.volume,
                    "timestamp": m.timestamp,
                    "resolved": m.resolved,
                    "resolution_outcome": m.resolution_outcome,
                }
                for m in markets
            ]

            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved {len(markets)} markets to {filepath}")

        except Exception as e:
            logger.error(f"Failed to save markets: {e}")

    def _load_prices_from_file(
        self,
        filepath: Path,
    ) -> List[HistoricalPrice]:
        """Load prices from CSV file."""
        try:
            prices = []
            with open(filepath, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    prices.append(HistoricalPrice(
                        symbol=row["symbol"],
                        price=float(row["price"]),
                        timestamp=float(row["timestamp"]),
                    ))
            return prices

        except Exception as e:
            logger.error(f"Failed to load prices from {filepath}: {e}")
            return []

    def _save_prices_to_file(
        self,
        prices: List[HistoricalPrice],
        filepath: Path,
    ) -> None:
        """Save prices to CSV file."""
        try:
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["symbol", "price", "timestamp"])
                writer.writeheader()
                for p in prices:
                    writer.writerow({
                        "symbol": p.symbol,
                        "price": p.price,
                        "timestamp": p.timestamp,
                    })

            logger.info(f"Saved {len(prices)} prices to {filepath}")

        except Exception as e:
            logger.error(f"Failed to save prices: {e}")

    def _generate_simulated_markets(
        self,
        days: int,
        category: Optional[str],
    ) -> List[HistoricalMarket]:
        """
        Generate simulated market data for testing.

        Creates realistic market scenarios including:
        - Arbitrage opportunities (YES + NO < 1)
        - Resolved markets with outcomes
        - Various liquidity levels
        """
        import random
        import uuid

        markets = []
        now = time.time()
        day_seconds = 86400

        # Generate markets across the time range
        num_markets = days * 10  # ~10 markets per day

        for i in range(num_markets):
            # Random timestamp within range
            timestamp = now - random.uniform(0, days * day_seconds)

            # Random prices with some arbitrage opportunities
            if random.random() < 0.1:  # 10% have arb opportunity
                yes_price = random.uniform(0.40, 0.55)
                no_price = random.uniform(0.40, 0.55)
                # Ensure YES + NO < 0.99
                total = yes_price + no_price
                if total >= 0.99:
                    yes_price *= 0.98 / total
                    no_price *= 0.98 / total
            else:
                yes_price = random.uniform(0.20, 0.80)
                no_price = 1 - yes_price + random.uniform(-0.02, 0.02)
                no_price = max(0.01, min(0.99, no_price))

            # Resolution (older markets more likely resolved)
            age_days = (now - timestamp) / day_seconds
            resolved = random.random() < (age_days / days * 0.8)
            resolution = None
            if resolved:
                # Resolution tends toward initial price direction
                resolution = "Yes" if yes_price > 0.5 and random.random() < 0.6 else "No"

            markets.append(HistoricalMarket(
                condition_id=str(uuid.uuid4()),
                question=f"Will BTC reach ${random.randint(40, 100)}k? (Simulated {i})",
                yes_price=round(yes_price, 4),
                no_price=round(no_price, 4),
                liquidity=random.uniform(100, 10000),
                volume=random.uniform(50, 5000),
                timestamp=timestamp,
                resolved=resolved,
                resolution_outcome=resolution,
            ))

        # Sort by timestamp
        markets.sort(key=lambda m: m.timestamp)

        logger.info(f"Generated {len(markets)} simulated markets")
        return markets

    def _generate_simulated_prices(
        self,
        symbol: str,
        days: int,
        interval_minutes: int,
    ) -> List[HistoricalPrice]:
        """
        Generate simulated price data with realistic volatility.

        Uses random walk with mean reversion to simulate
        crypto price movements including spikes.
        """
        import random
        import math

        prices = []
        now = time.time()

        # Starting prices
        start_prices = {
            "BTC": 65000,
            "ETH": 3500,
            "SOL": 150,
            "MATIC": 0.80,
        }

        current_price = start_prices.get(symbol, 100)
        volatility = 0.001  # Per-interval volatility

        # Generate prices
        total_intervals = days * 24 * 60 // interval_minutes
        interval_seconds = interval_minutes * 60

        for i in range(total_intervals):
            timestamp = now - (total_intervals - i) * interval_seconds

            # Random walk with occasional spikes
            if random.random() < 0.01:  # 1% chance of spike
                change = random.uniform(-0.05, 0.05)  # 5% spike
            else:
                change = random.gauss(0, volatility)

            # Mean reversion
            mean_price = start_prices.get(symbol, 100)
            reversion = 0.001 * (mean_price - current_price) / mean_price

            current_price *= (1 + change + reversion)
            current_price = max(current_price * 0.5, current_price)  # Floor

            prices.append(HistoricalPrice(
                symbol=symbol,
                price=round(current_price, 2),
                timestamp=timestamp,
            ))

        logger.info(f"Generated {len(prices)} simulated prices for {symbol}")
        return prices

    def get_market_snapshots_at(
        self,
        timestamp: float,
    ) -> List[HistoricalMarket]:
        """
        Get market state at a specific timestamp.

        Useful for step-by-step backtesting.
        """
        all_markets = self.load_market_history(days=30)

        return [
            m for m in all_markets
            if m.timestamp <= timestamp and not m.resolved
        ]

    def get_price_at(
        self,
        symbol: str,
        timestamp: float,
    ) -> Optional[float]:
        """Get price at specific timestamp."""
        prices = self.load_price_history(symbol)

        # Find closest price
        closest = None
        min_diff = float('inf')

        for p in prices:
            diff = abs(p.timestamp - timestamp)
            if diff < min_diff:
                min_diff = diff
                closest = p

        return closest.price if closest else None
