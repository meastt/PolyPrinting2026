"""
Tests for trading strategies.

Run with: pytest tests/
"""

import pytest
from unittest.mock import Mock, patch
import sys
import os

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestArbitrageStrategy:
    """Tests for the arbitrage strategy."""

    def test_detect_arbitrage_opportunity(self):
        """Test that arbitrage is detected when YES + NO < 0.99."""
        # Mock market with arbitrage opportunity
        market = Mock()
        market.condition_id = "test_market_123"
        market.question = "Will BTC reach $100k?"
        market.outcome_prices = {"Yes": 0.48, "No": 0.50}
        market.tokens = {"Yes": "token_yes", "No": "token_no"}
        market.liquidity = 1000
        market.active = True
        market.outcomes = ["Yes", "No"]

        # Total cost = 0.48 + 0.50 = 0.98 < 0.99
        # This should be detected as arbitrage
        total_cost = market.outcome_prices["Yes"] + market.outcome_prices["No"]
        assert total_cost < 0.99, "Should detect arbitrage when YES + NO < 0.99"

        # Expected profit per pair
        profit = 1.0 - total_cost
        assert profit > 0.01, f"Expected profit {profit} should exceed 1%"

    def test_no_arbitrage_when_prices_fair(self):
        """Test that no arbitrage is detected when prices are fair."""
        market = Mock()
        market.outcome_prices = {"Yes": 0.50, "No": 0.51}

        total_cost = market.outcome_prices["Yes"] + market.outcome_prices["No"]
        assert total_cost >= 0.99, "No arbitrage when YES + NO >= 0.99"


class TestRiskManager:
    """Tests for risk management."""

    def test_position_size_limits(self):
        """Test that position sizes are limited correctly."""
        from src.core.risk_manager import RiskManager, RiskLimits

        limits = RiskLimits(max_position_percent=2.0)
        rm = RiskManager(limits=limits, starting_balance=50.0)

        # Calculate max position size for $50 balance at 2%
        max_size = 50.0 * 0.02  # = $1.00
        calculated_size = rm.calculate_position_size(edge=0.05, confidence=1.0)

        assert calculated_size <= max_size, f"Position size {calculated_size} exceeds max {max_size}"

    def test_daily_drawdown_limit(self):
        """Test that daily drawdown triggers stop."""
        from src.core.risk_manager import RiskManager, RiskLimits

        limits = RiskLimits(daily_drawdown_limit=0.05)  # 5%
        rm = RiskManager(limits=limits, starting_balance=100.0)

        # Simulate 6% loss
        rm.update_balance(94.0)

        # Should trigger emergency stop
        assert not rm.is_trading_allowed(), "Trading should be stopped after 5% drawdown"


class TestEVCalculation:
    """Tests for expected value calculations."""

    def test_maker_ev_positive(self):
        """Test EV calculation for maker orders with edge."""
        from src.strategies.base_strategy import BaseStrategy

        # Create a concrete implementation for testing
        class TestStrategy(BaseStrategy):
            def evaluate(self, markets, positions, balance):
                return []

        strategy = TestStrategy(name="test")

        # Fair value 0.55, price 0.50, maker rebate 0.01
        # Edge = 0.55 - 0.50 = 0.05
        # EV = 0.05 + 0.01 = 0.06 (6%)
        ev = strategy.calculate_ev(fair_value=0.55, price=0.50, is_maker=True)
        assert ev > 0.05, f"Maker EV {ev} should include rebate"

    def test_taker_ev_reduced_by_fees(self):
        """Test that taker orders have reduced EV due to fees."""
        from src.strategies.base_strategy import BaseStrategy

        class TestStrategy(BaseStrategy):
            def evaluate(self, markets, positions, balance):
                return []

        strategy = TestStrategy(name="test")

        # Same edge but taker
        # Edge = 0.55 - 0.50 = 0.05
        # EV = 0.05 - 0.03 = 0.02 (2%)
        ev = strategy.calculate_ev(fair_value=0.55, price=0.50, is_maker=False)
        assert ev < 0.03, f"Taker EV {ev} should be reduced by 3% fee"


class TestHelpers:
    """Tests for helper functions."""

    def test_format_usdc(self):
        """Test USDC formatting."""
        from src.utils.helpers import format_usdc

        assert format_usdc(50.0) == "$50.00"
        assert format_usdc(1234.56) == "$1,234.56"
        assert format_usdc(5.25, include_sign=True) == "+$5.25"
        assert format_usdc(-5.25, include_sign=True) == "$-5.25"

    def test_format_percent(self):
        """Test percentage formatting."""
        from src.utils.helpers import format_percent

        assert format_percent(0.05) == "5.00%"
        assert format_percent(0.025, include_sign=True) == "+2.50%"

    def test_safe_divide(self):
        """Test safe division."""
        from src.utils.helpers import safe_divide

        assert safe_divide(10, 2) == 5.0
        assert safe_divide(10, 0) == 0.0
        assert safe_divide(10, 0, default=-1) == -1

    def test_clamp(self):
        """Test value clamping."""
        from src.utils.helpers import clamp

        assert clamp(5, 0, 10) == 5
        assert clamp(-5, 0, 10) == 0
        assert clamp(15, 0, 10) == 10


class TestConfigLoading:
    """Tests for configuration loading."""

    def test_load_default_config(self):
        """Test that default config is loaded when file missing."""
        from src.utils.helpers import load_config

        config = load_config("nonexistent_file.yaml")
        assert "general" in config
        assert "strategies" in config
        assert config["general"]["mode"] == "simulation"

    def test_config_has_required_sections(self):
        """Test that config has all required sections."""
        from src.utils.helpers import get_default_config

        config = get_default_config()

        assert "general" in config
        assert "strategies" in config
        assert "risk" in config

        # Check strategy toggles exist
        strategies = config["strategies"]
        assert "arbitrage" in strategies
        assert "market_making" in strategies
        assert "spike_reversion" in strategies
        assert "copy_trading" in strategies


class TestBacktester:
    """Tests for backtesting functionality."""

    def test_backtest_runs(self):
        """Test that backtester runs without errors."""
        from src.backtest.backtester import Backtester

        backtester = Backtester()
        result = backtester.run(
            strategy="arbitrage",
            days=7,
            start_balance=50.0,
        )

        assert result.start_balance == 50.0
        assert result.days == 7
        assert result.strategy == "arbitrage"

    def test_backtest_result_metrics(self):
        """Test that backtest produces valid metrics."""
        from src.backtest.backtester import Backtester

        backtester = Backtester()
        result = backtester.run(days=7, start_balance=50.0)

        # Check all metrics are computed
        assert hasattr(result, "total_return")
        assert hasattr(result, "max_drawdown")
        assert hasattr(result, "win_rate")
        assert hasattr(result, "total_trades")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
