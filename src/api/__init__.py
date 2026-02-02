"""
API client modules for external service integration.

- polymarket_client: Polymarket CLOB API wrapper
- price_feeds: Multi-exchange price aggregation via CCXT
- websocket_feeds: Real-time WebSocket streaming for low latency
- gamma_api: Gamma API for leaderboards and analytics
"""

from src.api.polymarket_client import PolymarketClient
from src.api.price_feeds import PriceFeedAggregator
from src.api.gamma_api import GammaAPIClient
from src.api.websocket_feeds import WebSocketPriceFeed, BinanceWebSocket

__all__ = [
    "PolymarketClient",
    "PriceFeedAggregator",
    "GammaAPIClient",
    "WebSocketPriceFeed",
    "BinanceWebSocket",
]
