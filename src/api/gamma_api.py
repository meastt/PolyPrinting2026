"""
Gamma API Client for Polymarket Analytics

Provides access to:
- Leaderboard data for copy trading
- Trader portfolio analysis
- Historical performance metrics

Note: The Gamma API endpoints may require authentication or may
have changed. This module provides a framework for integration.

Reference: https://docs.polymarket.com/ (check for Gamma API docs)
"""

import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import requests

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TraderProfile:
    """Represents a trader's profile and performance."""
    address: str
    username: Optional[str]
    total_volume: float
    total_pnl: float
    win_rate: float
    num_trades: int
    rank: int
    active_positions: int
    recent_roi: float  # Last 30 days


@dataclass
class TraderPosition:
    """Represents a position held by a trader."""
    trader_address: str
    market_id: str
    market_question: str
    outcome: str
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    timestamp: str


class GammaAPIClient:
    """
    Client for Polymarket Gamma API (analytics and leaderboards).

    Used by the copy trading strategy to:
    - Identify top performing traders
    - Monitor their positions
    - Analyze their trading patterns

    Inspired by portfolio analysis techniques from openclaw/polyskills.

    Note: API endpoints are subject to change. Check Polymarket docs
    for the latest API specifications.
    """

    # Gamma API base URL (placeholder - verify with actual docs)
    DEFAULT_HOST = "https://gamma-api.polymarket.com"

    # Alternative endpoints that may be available
    CLOB_HOST = "https://clob.polymarket.com"
    STRAPI_HOST = "https://strapi-matic.poly.market"

    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
    ):
        """
        Initialize the Gamma API client.

        Args:
            host: API host URL
            api_key: API key for authentication (if required)
            timeout: Request timeout in seconds
        """
        self.host = host or self.DEFAULT_HOST
        self.api_key = api_key
        self.timeout = timeout

        self._session = requests.Session()
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"
        self._session.headers["Content-Type"] = "application/json"

        # Cache
        self._leaderboard_cache: List[TraderProfile] = []
        self._cache_timestamp: float = 0
        self._cache_ttl: float = 300  # 5 minutes

        logger.info(f"GammaAPIClient initialized (host={self.host})")

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """Make an API request."""
        url = f"{self.host}{endpoint}"

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=data,
                timeout=self.timeout,
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(
                    f"API request failed: {response.status_code} - {response.text}"
                )
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error for {endpoint}: {e}")
            return None

    def get_leaderboard(
        self,
        limit: int = 100,
        period: str = "all",  # "day", "week", "month", "all"
        force_refresh: bool = False,
    ) -> List[TraderProfile]:
        """
        Fetch the trader leaderboard.

        Args:
            limit: Maximum number of traders to return
            period: Time period for rankings
            force_refresh: Bypass cache

        Returns:
            List of TraderProfile objects
        """
        # Check cache
        if not force_refresh and self._leaderboard_cache:
            if time.time() - self._cache_timestamp < self._cache_ttl:
                return self._leaderboard_cache[:limit]

        # Try multiple potential endpoints
        endpoints_to_try = [
            "/leaderboard",
            "/api/leaderboard",
            "/v1/leaderboard",
        ]

        for endpoint in endpoints_to_try:
            response = self._make_request(
                "GET",
                endpoint,
                params={"limit": limit, "period": period},
            )

            if response:
                traders = self._parse_leaderboard(response)
                if traders:
                    self._leaderboard_cache = traders
                    self._cache_timestamp = time.time()
                    logger.info(f"Fetched {len(traders)} traders from leaderboard")
                    return traders[:limit]

        logger.warning("Could not fetch leaderboard from any endpoint")
        return self._get_mock_leaderboard(limit)

    def _parse_leaderboard(self, response: Dict[str, Any]) -> List[TraderProfile]:
        """Parse leaderboard response."""
        traders = []

        # Handle various response formats
        data = response.get("data", response.get("traders", response))

        if isinstance(data, list):
            for i, trader_data in enumerate(data):
                try:
                    trader = TraderProfile(
                        address=trader_data.get("address", trader_data.get("user", "")),
                        username=trader_data.get("username", trader_data.get("name")),
                        total_volume=float(trader_data.get("volume", 0)),
                        total_pnl=float(trader_data.get("pnl", trader_data.get("profit", 0))),
                        win_rate=float(trader_data.get("win_rate", 0.5)),
                        num_trades=int(trader_data.get("trades", trader_data.get("num_trades", 0))),
                        rank=i + 1,
                        active_positions=int(trader_data.get("positions", 0)),
                        recent_roi=float(trader_data.get("roi_30d", 0)),
                    )
                    traders.append(trader)
                except (ValueError, KeyError) as e:
                    logger.debug(f"Failed to parse trader data: {e}")

        return traders

    def _get_mock_leaderboard(self, limit: int) -> List[TraderProfile]:
        """
        Return mock leaderboard data for testing.

        In production, this should be replaced with actual API data.
        """
        logger.info("Using mock leaderboard data (API unavailable)")

        # Generate realistic mock data
        mock_traders = []
        for i in range(min(limit, 10)):
            mock_traders.append(TraderProfile(
                address=f"0x{'0' * 38}{i:02d}",
                username=f"trader_{i+1}",
                total_volume=100000 - (i * 5000),
                total_pnl=10000 - (i * 800),
                win_rate=0.65 - (i * 0.02),
                num_trades=500 - (i * 30),
                rank=i + 1,
                active_positions=10 - i,
                recent_roi=0.30 - (i * 0.03),
            ))

        return mock_traders

    def get_trader_positions(
        self,
        trader_address: str,
        active_only: bool = True,
    ) -> List[TraderPosition]:
        """
        Get positions for a specific trader.

        Args:
            trader_address: Ethereum address of the trader
            active_only: Only return active (open) positions

        Returns:
            List of TraderPosition objects
        """
        # Try to fetch from API
        endpoints_to_try = [
            f"/traders/{trader_address}/positions",
            f"/api/users/{trader_address}/positions",
            f"/v1/positions/{trader_address}",
        ]

        for endpoint in endpoints_to_try:
            response = self._make_request(
                "GET",
                endpoint,
                params={"active": active_only},
            )

            if response:
                return self._parse_positions(trader_address, response)

        # Return empty list if API unavailable
        logger.debug(f"Could not fetch positions for {trader_address}")
        return []

    def _parse_positions(
        self,
        trader_address: str,
        response: Dict[str, Any],
    ) -> List[TraderPosition]:
        """Parse positions response."""
        positions = []
        data = response.get("data", response.get("positions", response))

        if isinstance(data, list):
            for pos_data in data:
                try:
                    positions.append(TraderPosition(
                        trader_address=trader_address,
                        market_id=pos_data.get("market_id", pos_data.get("condition_id", "")),
                        market_question=pos_data.get("question", pos_data.get("market", "")),
                        outcome=pos_data.get("outcome", "Unknown"),
                        size=float(pos_data.get("size", pos_data.get("shares", 0))),
                        entry_price=float(pos_data.get("entry_price", pos_data.get("avg_price", 0))),
                        current_price=float(pos_data.get("current_price", pos_data.get("price", 0))),
                        unrealized_pnl=float(pos_data.get("pnl", 0)),
                        timestamp=pos_data.get("timestamp", pos_data.get("created_at", "")),
                    ))
                except (ValueError, KeyError) as e:
                    logger.debug(f"Failed to parse position data: {e}")

        return positions

    def get_trader_stats(self, trader_address: str) -> Optional[TraderProfile]:
        """
        Get detailed stats for a specific trader.

        Args:
            trader_address: Ethereum address of the trader

        Returns:
            TraderProfile or None
        """
        endpoints_to_try = [
            f"/traders/{trader_address}",
            f"/api/users/{trader_address}",
            f"/v1/users/{trader_address}/stats",
        ]

        for endpoint in endpoints_to_try:
            response = self._make_request("GET", endpoint)

            if response:
                traders = self._parse_leaderboard({"data": [response]})
                if traders:
                    return traders[0]

        return None

    def get_top_traders(
        self,
        num_traders: int = 10,
        min_win_rate: float = 0.55,
        min_volume: float = 1000,
        category: Optional[str] = None,
    ) -> List[TraderProfile]:
        """
        Get top traders filtered by performance criteria.

        Used by copy trading strategy to select traders to mirror.

        Args:
            num_traders: Number of traders to return
            min_win_rate: Minimum win rate threshold
            min_volume: Minimum trading volume
            category: Filter by market category (e.g., "Crypto")

        Returns:
            List of qualifying TraderProfile objects
        """
        # Fetch full leaderboard
        all_traders = self.get_leaderboard(limit=100)

        # Apply filters
        filtered = [
            t for t in all_traders
            if t.win_rate >= min_win_rate
            and t.total_volume >= min_volume
        ]

        # Sort by a composite score (PnL * win_rate)
        filtered.sort(
            key=lambda t: t.total_pnl * t.win_rate,
            reverse=True,
        )

        top_traders = filtered[:num_traders]

        logger.info(
            f"Selected {len(top_traders)} top traders "
            f"(win_rate >= {min_win_rate}, volume >= ${min_volume})"
        )

        return top_traders

    def get_recent_trades(
        self,
        trader_address: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Get recent trades for a trader.

        Args:
            trader_address: Ethereum address
            limit: Number of trades to return

        Returns:
            List of trade dicts
        """
        endpoints_to_try = [
            f"/traders/{trader_address}/trades",
            f"/api/users/{trader_address}/trades",
        ]

        for endpoint in endpoints_to_try:
            response = self._make_request(
                "GET",
                endpoint,
                params={"limit": limit},
            )

            if response:
                return response.get("data", response.get("trades", []))

        return []

    def detect_new_positions(
        self,
        trader_address: str,
        known_positions: List[str],
    ) -> List[TraderPosition]:
        """
        Detect new positions that we haven't seen before.

        Used to trigger copy trades when top traders enter new positions.

        Args:
            trader_address: Trader to monitor
            known_positions: List of market IDs we already know about

        Returns:
            List of new positions
        """
        current_positions = self.get_trader_positions(trader_address)

        new_positions = [
            pos for pos in current_positions
            if pos.market_id not in known_positions
        ]

        if new_positions:
            logger.info(
                f"Detected {len(new_positions)} new positions for {trader_address[:10]}..."
            )

        return new_positions

    def analyze_trader_style(
        self,
        trader_address: str,
    ) -> Dict[str, Any]:
        """
        Analyze a trader's trading style and patterns.

        Useful for understanding if a trader's strategy aligns
        with our bot's approach (e.g., crypto focus, short-term trades).

        Args:
            trader_address: Trader to analyze

        Returns:
            Analysis dict with style metrics
        """
        stats = self.get_trader_stats(trader_address)
        positions = self.get_trader_positions(trader_address)
        recent_trades = self.get_recent_trades(trader_address)

        if not stats:
            return {"error": "Could not fetch trader stats"}

        # Calculate style metrics
        analysis = {
            "address": trader_address,
            "win_rate": stats.win_rate,
            "avg_position_size": sum(p.size for p in positions) / len(positions) if positions else 0,
            "active_positions": len(positions),
            "total_pnl": stats.total_pnl,
            "recent_roi": stats.recent_roi,
            "trade_frequency": len(recent_trades),
            "style": "unknown",
        }

        # Classify style
        if stats.num_trades > 100 and stats.win_rate > 0.55:
            analysis["style"] = "high_frequency"
        elif stats.recent_roi > 0.2:
            analysis["style"] = "momentum"
        elif stats.win_rate > 0.6:
            analysis["style"] = "selective"
        else:
            analysis["style"] = "diversified"

        return analysis

    def health_check(self) -> bool:
        """
        Check if the Gamma API is reachable.

        Returns:
            True if healthy
        """
        try:
            response = self._session.get(
                f"{self.host}/health",
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            # Try alternative health check
            try:
                self.get_leaderboard(limit=1)
                return True
            except Exception:
                return False
