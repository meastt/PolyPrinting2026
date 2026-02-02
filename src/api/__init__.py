"""
API client modules for external service integration.

Supported Exchanges:
- kalshi_client: Kalshi API (US legal, CFTC-regulated)
- polymarket_client: Polymarket CLOB API wrapper (non-US)

Price Data:
- price_feeds: Multi-exchange price aggregation via CCXT
- websocket_feeds: Real-time WebSocket streaming for low latency

Other:
- gamma_api: Gamma API for leaderboards and analytics (Polymarket)
"""

from src.api.kalshi_client import KalshiClient, KalshiMarket, KalshiOrder, KalshiPosition
from src.api.polymarket_client import PolymarketClient
from src.api.price_feeds import PriceFeedAggregator
from src.api.gamma_api import GammaAPIClient
from src.api.websocket_feeds import WebSocketPriceFeed, BinanceWebSocket

__all__ = [
    # Kalshi (US legal)
    "KalshiClient",
    "KalshiMarket",
    "KalshiOrder",
    "KalshiPosition",
    # Polymarket (non-US)
    "PolymarketClient",
    # Price feeds
    "PriceFeedAggregator",
    "WebSocketPriceFeed",
    "BinanceWebSocket",
    # Other
    "GammaAPIClient",
]
