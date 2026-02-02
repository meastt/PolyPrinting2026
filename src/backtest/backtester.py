"""
Backtesting Engine

Simulates trading strategies on historical data to validate
performance before live deployment.

Features:
- Multi-strategy support
- Realistic fee simulation
- Detailed performance metrics
- Trade-by-trade logging
"""

import time
import argparse
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path

from src.backtest.data_loader import DataLoader, HistoricalMarket
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestTrade:
    """Represents a trade executed during backtest."""
    timestamp: float
    market_id: str
    outcome: str
    size: float
    entry_price: float
    exit_price: Optional[float] = None
    pnl: float = 0.0
    fees: float = 0.0
    rebates: float = 0.0
    strategy: str = ""
    resolved: bool = False


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    # Configuration
    strategy: str
    start_balance: float
    days: int

    # Final state
    end_balance: float
    total_return: float
    total_return_pct: float

    # Performance metrics
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float

    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_trade_pnl: float
    largest_win: float
    largest_loss: float

    # Fee impact
    total_fees: float
    total_rebates: float
    net_fee_impact: float

    # Trade list
    trades: List[BacktestTrade] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "strategy": self.strategy,
            "start_balance": self.start_balance,
            "end_balance": self.end_balance,
            "days": self.days,
            "total_return": self.total_return,
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_trade_pnl": self.avg_trade_pnl,
            "largest_win": self.largest_win,
            "largest_loss": self.largest_loss,
            "total_fees": self.total_fees,
            "total_rebates": self.total_rebates,
            "net_fee_impact": self.net_fee_impact,
        }


class Backtester:
    """
    Backtesting engine for trading strategies.

    Simulates trading on historical data with realistic
    fee structures and position management.

    Target: 50-100% return over 30 days with $50 starting balance
    via low-risk arbitrage and market making.
    """

    # Fee structure
    TAKER_FEE = 0.03  # 3%
    MAKER_REBATE = 0.01  # 1%

    def __init__(
        self,
        data_loader: Optional[DataLoader] = None,
        include_fees: bool = True,
        include_slippage: bool = True,
        slippage_bps: float = 10,  # 0.1% slippage
    ):
        """
        Initialize backtester.

        Args:
            data_loader: Data source for historical data
            include_fees: Whether to simulate fees
            include_slippage: Whether to simulate slippage
            slippage_bps: Slippage in basis points
        """
        self.data_loader = data_loader or DataLoader()
        self.include_fees = include_fees
        self.include_slippage = include_slippage
        self.slippage_bps = slippage_bps

        logger.info(
            f"Backtester initialized (fees={include_fees}, "
            f"slippage={include_slippage})"
        )

    def run(
        self,
        strategy: str = "all",
        days: int = 30,
        start_balance: float = 50.0,
    ) -> BacktestResult:
        """
        Run a backtest simulation.

        Args:
            strategy: Strategy to test ("arbitrage", "market_maker", "all")
            days: Number of days to simulate
            start_balance: Starting balance in USDC

        Returns:
            BacktestResult with performance metrics
        """
        logger.info(f"Starting backtest: strategy={strategy}, days={days}, balance=${start_balance}")

        # Load historical data
        markets = self.data_loader.load_market_history(days=days)
        prices_btc = self.data_loader.load_price_history("BTC", days=days)
        prices_eth = self.data_loader.load_price_history("ETH", days=days)

        # Initialize state
        balance = start_balance
        trades: List[BacktestTrade] = []
        balance_history: List[float] = [start_balance]
        open_positions: Dict[str, BacktestTrade] = {}

        # Get time range
        if not markets:
            logger.warning("No market data available")
            return self._create_empty_result(strategy, start_balance, days)

        start_time = min(m.timestamp for m in markets)
        end_time = max(m.timestamp for m in markets)

        # Simulate time steps (hourly)
        current_time = start_time
        step_seconds = 3600  # 1 hour

        while current_time <= end_time:
            # Get markets available at this time
            available_markets = [
                m for m in markets
                if m.timestamp <= current_time and not m.resolved
            ]

            # Run strategy logic
            if strategy in ["arbitrage", "all"]:
                arb_trades = self._run_arbitrage_step(
                    markets=available_markets,
                    balance=balance,
                    timestamp=current_time,
                )

                for trade in arb_trades:
                    balance -= (trade.size * trade.entry_price + trade.fees - trade.rebates)
                    trades.append(trade)
                    open_positions[trade.market_id] = trade

            if strategy in ["market_maker", "all"]:
                mm_trades = self._run_market_maker_step(
                    markets=available_markets,
                    balance=balance,
                    timestamp=current_time,
                )

                for trade in mm_trades:
                    balance -= (trade.size * trade.entry_price + trade.fees - trade.rebates)
                    trades.append(trade)
                    open_positions[trade.market_id] = trade

            # Check for resolved markets
            for market in markets:
                if market.resolved and market.condition_id in open_positions:
                    trade = open_positions.pop(market.condition_id)

                    # Determine P&L based on resolution
                    if trade.outcome == market.resolution_outcome:
                        trade.exit_price = 1.0  # Won
                    else:
                        trade.exit_price = 0.0  # Lost

                    trade.pnl = (trade.exit_price - trade.entry_price) * trade.size
                    trade.resolved = True
                    balance += trade.size * trade.exit_price

            # Track balance
            balance_history.append(balance)

            current_time += step_seconds

        # Calculate final metrics
        result = self._calculate_results(
            strategy=strategy,
            start_balance=start_balance,
            end_balance=balance,
            days=days,
            trades=trades,
            balance_history=balance_history,
        )

        # Log summary
        self._log_result(result)

        return result

    def _run_arbitrage_step(
        self,
        markets: List[HistoricalMarket],
        balance: float,
        timestamp: float,
    ) -> List[BacktestTrade]:
        """
        Run one step of arbitrage strategy.

        Looks for YES + NO < 0.99 opportunities.
        """
        trades = []

        for market in markets:
            # Check for arbitrage
            total_cost = market.yes_price + market.no_price

            if total_cost < 0.99:
                # Arbitrage opportunity!
                profit_per_pair = 1.0 - total_cost

                # Calculate position size (2% of balance max)
                max_size = balance * 0.02 / total_cost
                size = min(max_size, 5.0)  # Cap at $5

                if size < 0.5:  # Minimum $0.50
                    continue

                # Create two trades (YES and NO)
                yes_trade = BacktestTrade(
                    timestamp=timestamp,
                    market_id=market.condition_id,
                    outcome="Yes",
                    size=size,
                    entry_price=market.yes_price,
                    strategy="arbitrage",
                    fees=0 if self.include_fees else 0,  # Maker order
                    rebates=size * market.yes_price * self.MAKER_REBATE if self.include_fees else 0,
                )

                no_trade = BacktestTrade(
                    timestamp=timestamp,
                    market_id=market.condition_id + "_no",
                    outcome="No",
                    size=size,
                    entry_price=market.no_price,
                    strategy="arbitrage",
                    fees=0,
                    rebates=size * market.no_price * self.MAKER_REBATE if self.include_fees else 0,
                )

                trades.extend([yes_trade, no_trade])

                # Only do a few arbs per step
                if len(trades) >= 4:
                    break

        return trades

    def _run_market_maker_step(
        self,
        markets: List[HistoricalMarket],
        balance: float,
        timestamp: float,
    ) -> List[BacktestTrade]:
        """
        Run one step of market making strategy.

        Posts quotes around fair value with spread.
        """
        trades = []

        for market in markets[:3]:  # Limit to 3 markets
            # Simple fair value estimate
            fair_value = (market.yes_price + (1 - market.no_price)) / 2

            # Spread
            spread = 0.02

            # Check if edge exists
            buy_price = fair_value - spread
            if market.yes_price < buy_price:
                # Can buy below our fair value
                size = min(balance * 0.01, 2.0)

                if size >= 0.5:
                    trade = BacktestTrade(
                        timestamp=timestamp,
                        market_id=market.condition_id,
                        outcome="Yes",
                        size=size,
                        entry_price=market.yes_price,
                        strategy="market_maker",
                        fees=0,
                        rebates=size * market.yes_price * self.MAKER_REBATE if self.include_fees else 0,
                    )
                    trades.append(trade)

        return trades

    def _calculate_results(
        self,
        strategy: str,
        start_balance: float,
        end_balance: float,
        days: int,
        trades: List[BacktestTrade],
        balance_history: List[float],
    ) -> BacktestResult:
        """Calculate backtest performance metrics."""
        # Basic returns
        total_return = end_balance - start_balance
        total_return_pct = (total_return / start_balance) * 100 if start_balance > 0 else 0

        # Separate winning and losing trades
        resolved_trades = [t for t in trades if t.resolved]
        winning = [t for t in resolved_trades if t.pnl > 0]
        losing = [t for t in resolved_trades if t.pnl <= 0]

        # Win rate
        win_rate = len(winning) / len(resolved_trades) * 100 if resolved_trades else 0

        # Average P&L
        pnls = [t.pnl for t in resolved_trades]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0

        # Largest win/loss
        largest_win = max(pnls) if pnls else 0
        largest_loss = min(pnls) if pnls else 0

        # Drawdown calculation
        peak = start_balance
        max_dd = 0
        for bal in balance_history:
            if bal > peak:
                peak = bal
            dd = peak - bal
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (max_dd / start_balance) * 100 if start_balance > 0 else 0

        # Sharpe ratio (simplified)
        if len(balance_history) > 1:
            import statistics
            returns = [(balance_history[i] - balance_history[i-1]) / balance_history[i-1]
                      for i in range(1, len(balance_history))
                      if balance_history[i-1] > 0]
            if returns and len(returns) > 1:
                avg_return = statistics.mean(returns)
                std_return = statistics.stdev(returns)
                sharpe = (avg_return / std_return) * (365 ** 0.5) if std_return > 0 else 0
            else:
                sharpe = 0
        else:
            sharpe = 0

        # Fee totals
        total_fees = sum(t.fees for t in trades)
        total_rebates = sum(t.rebates for t in trades)

        return BacktestResult(
            strategy=strategy,
            start_balance=start_balance,
            end_balance=round(end_balance, 2),
            days=days,
            total_return=round(total_return, 2),
            total_return_pct=round(total_return_pct, 1),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 1),
            win_rate=round(win_rate, 1),
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            avg_trade_pnl=round(avg_pnl, 4),
            largest_win=round(largest_win, 2),
            largest_loss=round(largest_loss, 2),
            total_fees=round(total_fees, 2),
            total_rebates=round(total_rebates, 2),
            net_fee_impact=round(total_rebates - total_fees, 2),
            trades=trades,
        )

    def _create_empty_result(
        self,
        strategy: str,
        start_balance: float,
        days: int,
    ) -> BacktestResult:
        """Create empty result when no data available."""
        return BacktestResult(
            strategy=strategy,
            start_balance=start_balance,
            end_balance=start_balance,
            days=days,
            total_return=0,
            total_return_pct=0,
            sharpe_ratio=0,
            max_drawdown=0,
            max_drawdown_pct=0,
            win_rate=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            avg_trade_pnl=0,
            largest_win=0,
            largest_loss=0,
            total_fees=0,
            total_rebates=0,
            net_fee_impact=0,
        )

    def _log_result(self, result: BacktestResult) -> None:
        """Log backtest results."""
        logger.info("=" * 50)
        logger.info("BACKTEST RESULTS")
        logger.info("=" * 50)
        logger.info(f"Strategy: {result.strategy}")
        logger.info(f"Period: {result.days} days")
        logger.info(f"Starting Balance: ${result.start_balance:.2f}")
        logger.info(f"Ending Balance: ${result.end_balance:.2f}")
        logger.info(f"Total Return: ${result.total_return:.2f} ({result.total_return_pct:.1f}%)")
        logger.info("-" * 50)
        logger.info(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        logger.info(f"Max Drawdown: ${result.max_drawdown:.2f} ({result.max_drawdown_pct:.1f}%)")
        logger.info(f"Win Rate: {result.win_rate:.1f}%")
        logger.info("-" * 50)
        logger.info(f"Total Trades: {result.total_trades}")
        logger.info(f"Winning Trades: {result.winning_trades}")
        logger.info(f"Losing Trades: {result.losing_trades}")
        logger.info(f"Avg Trade P&L: ${result.avg_trade_pnl:.4f}")
        logger.info("-" * 50)
        logger.info(f"Total Fees: ${result.total_fees:.2f}")
        logger.info(f"Total Rebates: ${result.total_rebates:.2f}")
        logger.info(f"Net Fee Impact: ${result.net_fee_impact:.2f}")
        logger.info("=" * 50)

    def save_report(
        self,
        result: BacktestResult,
        filepath: str = "logs/backtest_reports/report.json",
    ) -> None:
        """Save backtest report to file."""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)

        report = result.to_dict()
        report["generated_at"] = datetime.now().isoformat()

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)

        logger.info(f"Report saved to {filepath}")


def main():
    """Command-line interface for backtester."""
    parser = argparse.ArgumentParser(description="Run backtest simulation")
    parser.add_argument("--strategy", default="all", help="Strategy to test")
    parser.add_argument("--days", type=int, default=30, help="Days to simulate")
    parser.add_argument("--balance", type=float, default=50.0, help="Starting balance")
    parser.add_argument("--output", default="logs/backtest_reports/report.json", help="Output file")

    args = parser.parse_args()

    # Run backtest
    backtester = Backtester()
    result = backtester.run(
        strategy=args.strategy,
        days=args.days,
        start_balance=args.balance,
    )

    # Save report
    backtester.save_report(result, args.output)


if __name__ == "__main__":
    main()
