"""
Backtesting module for strategy validation.

Provides:
- Historical data loading
- Strategy simulation
- Performance metrics calculation
- Report generation
"""

from src.backtest.backtester import Backtester
from src.backtest.data_loader import DataLoader

__all__ = ["Backtester", "DataLoader"]
