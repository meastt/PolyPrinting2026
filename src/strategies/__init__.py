"""
Trading strategies for prediction markets.

Supports both Kalshi (US legal) and Polymarket (non-US).

Kalshi Strategies (US Legal):
- KalshiCryptoTAStrategy: TA-enhanced hourly crypto trading
- KalshiSpikeReversionStrategy: Spike reversion with TA confirmation
- KalshiArbitrageStrategy: YES/NO arbitrage
- KalshiMarketMakerStrategy: Liquidity provision (zero maker fees!)

Polymarket Strategies (Non-US):
- ArbitrageStrategy: YES/NO pricing inefficiency exploitation
- MarketMakerStrategy: Liquidity provision for rebates
- SpikeReversionStrategy: Volatility spike mean reversion
- CopyTraderStrategy: Top trader position mirroring
- BTC15mTAStrategy: Technical analysis enhanced 15-minute trading
"""

from src.strategies.base_strategy import BaseStrategy

# Kalshi strategies (US legal)
from src.strategies.kalshi_crypto_ta import KalshiCryptoTAStrategy
from src.strategies.kalshi_spike_reversion import KalshiSpikeReversionStrategy
from src.strategies.kalshi_arbitrage import KalshiArbitrageStrategy
from src.strategies.kalshi_market_maker import KalshiMarketMakerStrategy

# Polymarket strategies (non-US)
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.market_maker import MarketMakerStrategy
from src.strategies.spike_reversion import SpikeReversionStrategy
from src.strategies.copy_trader import CopyTraderStrategy
from src.strategies.btc_15m_ta import BTC15mTAStrategy

__all__ = [
    "BaseStrategy",
    # Kalshi (US legal)
    "KalshiCryptoTAStrategy",
    "KalshiSpikeReversionStrategy",
    "KalshiArbitrageStrategy",
    "KalshiMarketMakerStrategy",
    # Polymarket (non-US)
    "ArbitrageStrategy",
    "MarketMakerStrategy",
    "SpikeReversionStrategy",
    "CopyTraderStrategy",
    "BTC15mTAStrategy",
]
