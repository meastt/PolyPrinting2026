"""
Kalshi Crypto Technical Analysis Strategy

Combines PolymarketBTC15mAssistant TA approach with Kalshi's hourly crypto markets.

Key features:
- RSI, MACD, VWAP, Heiken Ashi technical indicators
- Market regime detection (TREND_UP, TREND_DOWN, RANGE, CHOP)
- Phase-based edge thresholds (early/mid/late)
- Zero fees on maker orders (Kalshi advantage!)
- Hourly crypto markets for frequent opportunities

This strategy is optimized for Kalshi's hourly BTC/ETH price prediction markets.
"""

import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass

from src.api.kalshi_client import KalshiClient, KalshiMarket, OrderSide
from src.api.price_feeds import PriceFeedAggregator
from src.api.websocket_feeds import WebSocketPriceFeed
from src.analysis.indicators import TechnicalIndicators
from src.analysis.scoring import DirectionalScorer, ScoringResult
from src.analysis.regime import RegimeDetector, MarketRegime
from src.analysis.edge import EdgeCalculator
from src.strategies.base_strategy import BaseStrategy, TradingSignal
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TASignal:
    """Technical analysis signal for Kalshi."""
    ticker: str
    direction: str  # "yes" or "no"
    probability: float
    edge: float
    confidence: float
    regime: MarketRegime
    rsi_value: float
    macd_signal: str  # "bullish", "bearish", "neutral"
    time_to_expiry_minutes: float
    reason: str


class KalshiCryptoTAStrategy(BaseStrategy):
    """
    Technical Analysis strategy for Kalshi crypto markets.

    Uses RSI, MACD, VWAP, and Heiken Ashi indicators to generate
    directional probability scores, then trades when edge exceeds
    phase-based thresholds.

    Optimized for Kalshi's hourly crypto markets with zero maker fees.
    """

    def __init__(
        self,
        kalshi: KalshiClient,
        price_feeds: PriceFeedAggregator,
        ws_feeds: Optional[WebSocketPriceFeed] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize Kalshi Crypto TA Strategy.

        Args:
            kalshi: Kalshi API client
            price_feeds: Price feed aggregator for spot prices
            ws_feeds: Optional WebSocket feeds for real-time data
            config: Strategy configuration
        """
        super().__init__(name="kalshi_crypto_ta", config=config)

        self.kalshi = kalshi
        self.price_feeds = price_feeds
        self.ws_feeds = ws_feeds

        # TA components
        self.indicators = TechnicalIndicators()
        self.scorer = DirectionalScorer()
        self.regime_detector = RegimeDetector()
        self.edge_calculator = EdgeCalculator()

        # Configuration
        config = config or {}

        # Assets to trade
        self.assets = config.get("assets", ["BTC", "ETH"])

        # Scoring thresholds
        scoring_config = config.get("scoring", {})
        self.min_score = scoring_config.get("min_score", 0.3)
        self.min_probability = scoring_config.get("min_probability", 0.55)

        # Edge thresholds by phase
        edge_config = config.get("edge", {})
        self.early_threshold = edge_config.get("early_threshold", 0.05)
        self.mid_threshold = edge_config.get("mid_threshold", 0.10)
        self.late_threshold = edge_config.get("late_threshold", 0.20)

        # Regime configuration
        regime_config = config.get("regime", {})
        self.filter_strong_trends = regime_config.get("filter_strong_trends", True)
        self.trend_threshold = regime_config.get("trend_threshold", 0.7)
        self.prefer_range = regime_config.get("prefer_range", True)

        # Position sizing
        sizing_config = config.get("sizing", {})
        self.base_size_percent = sizing_config.get("base_size_percent", 1.5)
        self.max_position = sizing_config.get("max_position", 3.0)
        self.confidence_scaling = sizing_config.get("confidence_scaling", True)

        # Stats
        self._signals_generated = 0
        self._signals_filtered = 0
        self._regime_rejections = 0

        logger.info(
            f"KalshiCryptoTAStrategy initialized "
            f"(assets={self.assets}, min_prob={self.min_probability})"
        )

    def evaluate(
        self,
        markets: List[KalshiMarket],
        positions: List[Any],
        balance: float,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate Kalshi crypto markets for TA signals.

        Args:
            markets: List of KalshiMarket objects
            positions: Current positions
            balance: Available balance

        Returns:
            List of trading signals
        """
        self._last_evaluation = time.time()
        signals = []

        # Get price history for TA calculations
        price_data = self._get_price_data()

        for market in markets:
            # Skip if already have position
            if self._has_position(market.ticker, positions):
                continue

            # Determine which asset this market is for
            asset = self._get_asset_from_ticker(market.ticker)
            if not asset or asset not in self.assets:
                continue

            # Get TA data for this asset
            if asset not in price_data:
                continue

            ta_data = price_data[asset]

            # Generate TA signal
            ta_signal = self._generate_ta_signal(market, ta_data)

            if ta_signal:
                # Convert to trading signal format
                signal = self._convert_to_signal(ta_signal, balance)
                if signal:
                    signals.append(signal)

        return signals

    def _get_price_data(self) -> Dict[str, Dict[str, Any]]:
        """Get price history and TA calculations for all assets."""
        price_data = {}

        for asset in self.assets:
            try:
                # Get price history (prefer WebSocket data)
                if self.ws_feeds:
                    history = self.ws_feeds.get_price_history(asset, periods=50)
                else:
                    history = self._get_rest_price_history(asset)

                if not history or len(history) < 20:
                    continue

                prices = [p["close"] for p in history]
                volumes = [p.get("volume", 0) for p in history]
                highs = [p.get("high", p["close"]) for p in history]
                lows = [p.get("low", p["close"]) for p in history]

                # Calculate indicators
                rsi = self.indicators.calculate_rsi(prices, period=14)
                macd = self.indicators.calculate_macd(prices)
                vwap = self.indicators.calculate_vwap(prices, volumes)
                heiken = self.indicators.calculate_heiken_ashi(history)

                # Detect regime
                regime = self.regime_detector.detect(
                    prices=prices,
                    highs=highs,
                    lows=lows,
                    volumes=volumes,
                )

                # Get current price and momentum
                current_price = prices[-1]
                momentum = (prices[-1] - prices[-5]) / prices[-5] if len(prices) >= 5 else 0

                price_data[asset] = {
                    "prices": prices,
                    "current_price": current_price,
                    "rsi": rsi,
                    "macd": macd,
                    "vwap": vwap,
                    "heiken_ashi": heiken,
                    "regime": regime,
                    "momentum": momentum,
                }

            except Exception as e:
                logger.debug(f"Failed to get price data for {asset}: {e}")

        return price_data

    def _get_rest_price_history(self, asset: str) -> List[Dict[str, Any]]:
        """Get price history from REST API."""
        try:
            # This would need to be implemented with historical data
            # For now, use price feeds volatility data
            vol_data = self.price_feeds.get_volatility(asset, window_seconds=3600)
            if vol_data:
                # Create synthetic history from current data
                current_price = vol_data.get("current_price", 0)
                if current_price > 0:
                    # Create minimal history
                    return [{"close": current_price, "high": current_price, "low": current_price, "volume": 0}] * 20
        except Exception:
            pass
        return []

    def _generate_ta_signal(
        self,
        market: KalshiMarket,
        ta_data: Dict[str, Any],
    ) -> Optional[TASignal]:
        """
        Generate TA signal for a market.

        Args:
            market: Kalshi market
            ta_data: Technical analysis data

        Returns:
            TASignal or None
        """
        rsi = ta_data.get("rsi")
        macd = ta_data.get("macd")
        vwap = ta_data.get("vwap")
        heiken = ta_data.get("heiken_ashi")
        regime_result = ta_data.get("regime")
        current_price = ta_data.get("current_price", 0)
        momentum = ta_data.get("momentum", 0)

        # Get Heiken Ashi trend
        heiken_trend = "neutral"
        if heiken and len(heiken) >= 2:
            last_candle = heiken[-1]
            if last_candle.close > last_candle.open:
                heiken_trend = "bullish"
            elif last_candle.close < last_candle.open:
                heiken_trend = "bearish"

        # Score direction using all indicators
        scoring_result = self.scorer.score_direction(
            rsi=rsi,
            macd=macd,
            vwap=vwap,
            heiken_ashi_trend=heiken_trend,
            momentum=momentum,
            current_price=current_price,
        )

        # Apply time awareness
        time_to_expiry_min = market.time_to_expiry_seconds / 60
        scoring_result = self.scorer.apply_time_awareness(
            result=scoring_result,
            remaining_minutes=time_to_expiry_min,
            window_minutes=60,  # Hourly markets
        )

        # Check minimum probability
        if scoring_result.probability < self.min_probability:
            return None

        # Check regime filter
        regime = regime_result.regime if regime_result else MarketRegime.RANGE

        if self.filter_strong_trends and regime_result:
            if regime_result.strength > self.trend_threshold:
                # Strong trend - only trade with trend
                if regime == MarketRegime.TREND_UP and scoring_result.direction == "down":
                    self._regime_rejections += 1
                    return None
                if regime == MarketRegime.TREND_DOWN and scoring_result.direction == "up":
                    self._regime_rejections += 1
                    return None

        # Calculate edge vs market price
        market_price = market.mid_price

        # Our model probability vs market price
        if scoring_result.direction == "up":
            # We think YES (price goes up)
            model_prob = scoring_result.probability
            edge = model_prob - market_price
            direction = "yes"
        else:
            # We think NO (price goes down)
            model_prob = 1 - scoring_result.probability
            edge = model_prob - (1 - market_price)
            direction = "no"

        # Get phase-based threshold
        threshold = self._get_edge_threshold(time_to_expiry_min)

        # Check if edge exceeds threshold
        if edge < threshold:
            self._signals_filtered += 1
            return None

        # Generate signal
        self._signals_generated += 1

        # Determine MACD signal
        macd_signal = "neutral"
        if macd:
            if macd.histogram > 0 and macd.histogram_rising:
                macd_signal = "bullish"
            elif macd.histogram < 0 and not macd.histogram_rising:
                macd_signal = "bearish"

        return TASignal(
            ticker=market.ticker,
            direction=direction,
            probability=model_prob,
            edge=edge,
            confidence=scoring_result.confidence,
            regime=regime,
            rsi_value=rsi.value if rsi else 50,
            macd_signal=macd_signal,
            time_to_expiry_minutes=time_to_expiry_min,
            reason=(
                f"TA signal: {scoring_result.direction} "
                f"(prob={model_prob:.2f}, edge={edge:.2%}, "
                f"regime={regime.value}, RSI={rsi.value if rsi else 50:.0f})"
            ),
        )

    def _get_edge_threshold(self, time_to_expiry_min: float) -> float:
        """Get edge threshold based on time to expiry."""
        if time_to_expiry_min > 40:  # Early phase (>40 min for hourly)
            return self.early_threshold
        elif time_to_expiry_min > 20:  # Mid phase
            return self.mid_threshold
        else:  # Late phase (<20 min)
            return self.late_threshold

    def _convert_to_signal(
        self,
        ta_signal: TASignal,
        balance: float,
    ) -> Optional[Dict[str, Any]]:
        """Convert TASignal to trading signal dict."""
        # Calculate position size
        base_size = balance * (self.base_size_percent / 100)
        base_size = min(base_size, self.max_position)

        # Scale by confidence if enabled
        if self.confidence_scaling:
            # Scale 0.5x to 1.5x based on confidence
            scale = 0.5 + ta_signal.confidence
            base_size *= scale

        # Final size cap
        size = min(base_size, self.max_position)

        # Determine price
        # For maker orders, we want to post slightly better than market
        if ta_signal.direction == "yes":
            # Bid slightly below ask for YES
            price = max(0.01, ta_signal.probability - 0.02)
        else:
            # Bid slightly below ask for NO (1 - YES price)
            price = max(0.01, (1 - ta_signal.probability) - 0.02)

        return {
            "ticker": ta_signal.ticker,
            "market_id": ta_signal.ticker,
            "side": ta_signal.direction,
            "outcome": ta_signal.direction.upper(),
            "price": price,
            "size": size,
            "ev": ta_signal.edge,
            "confidence": ta_signal.confidence,
            "strategy": self.name,
            "reason": ta_signal.reason,
            "urgency": "high" if ta_signal.time_to_expiry_minutes < 20 else "normal",
            "time_horizon_seconds": int(ta_signal.time_to_expiry_minutes * 60),
            "ta_data": {
                "rsi": ta_signal.rsi_value,
                "macd_signal": ta_signal.macd_signal,
                "regime": ta_signal.regime.value,
            },
        }

    def _has_position(self, ticker: str, positions: List[Any]) -> bool:
        """Check if we already have a position in this market."""
        for pos in positions:
            pos_ticker = getattr(pos, "ticker", None) or pos.get("ticker", "")
            if pos_ticker == ticker:
                return True
        return False

    def _get_asset_from_ticker(self, ticker: str) -> Optional[str]:
        """Extract asset from Kalshi ticker."""
        ticker_upper = ticker.upper()
        if "BTCUSD" in ticker_upper or "BTC" in ticker_upper:
            return "BTC"
        elif "ETHUSD" in ticker_upper or "ETH" in ticker_upper:
            return "ETH"
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        stats = super().get_stats()
        stats.update({
            "signals_generated": self._signals_generated,
            "signals_filtered": self._signals_filtered,
            "regime_rejections": self._regime_rejections,
            "assets": self.assets,
            "min_probability": self.min_probability,
            "edge_thresholds": {
                "early": self.early_threshold,
                "mid": self.mid_threshold,
                "late": self.late_threshold,
            },
        })
        return stats
