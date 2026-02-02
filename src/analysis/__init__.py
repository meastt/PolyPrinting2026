"""
Technical Analysis Module

Advanced technical analysis for Polymarket trading, inspired by
PolymarketBTC15mAssistant's indicator suite.

Components:
- indicators: RSI, MACD, VWAP, Heiken Ashi calculations
- scoring: Directional probability scoring
- regime: Market regime detection (TREND, RANGE, CHOP)
- edge: Edge calculation vs market prices
"""

from src.analysis.indicators import TechnicalIndicators
from src.analysis.scoring import DirectionalScorer, SignalStrength
from src.analysis.regime import RegimeDetector, MarketRegime
from src.analysis.edge import EdgeCalculator

__all__ = [
    "TechnicalIndicators",
    "DirectionalScorer",
    "SignalStrength",
    "RegimeDetector",
    "MarketRegime",
    "EdgeCalculator",
]
