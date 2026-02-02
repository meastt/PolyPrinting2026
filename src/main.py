#!/usr/bin/env python3
"""
PolyPrinting2026 - Automated Polymarket Trading Bot

Main entry point for the trading bot. Run with:
    python -m src.main

Or for backtesting:
    python -m src.main --backtest

See README.md for full documentation.

Strategies implemented:
- YES/NO Arbitrage: Risk-free profits when YES + NO < $1
  (Inspired by Moltbot community tactics)
- Maker Market Making: Earn rebates by providing liquidity
- Volatility Spike Reversion: Bet on mean reversion after sharp moves
- Selective Copy Trading: Mirror top performers with edge filter
  (Using techniques from openclaw/polyskills)

Target: 100-300% annualized returns on small capital ($50 start)
through systematic, low-risk micro-trades with compounding.
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.logger import setup_logging, get_logger
from src.utils.helpers import load_config
from src.utils.metrics import init_metrics
from src.core.trading_loop import create_trading_loop

logger = get_logger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="PolyPrinting2026 - Automated Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.main                    # Run in simulation mode
  python -m src.main --live             # Run in live trading mode
  python -m src.main --backtest         # Run backtest simulation
  python -m src.main --config my.yaml   # Use custom config file

For more information, see README.md
        """,
    )

    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Path to configuration file (default: config/config.yaml)",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (default: simulation)",
    )

    parser.add_argument(
        "--simulation",
        action="store_true",
        help="Run in simulation mode (default)",
    )

    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run backtest simulation instead of live trading",
    )

    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days to backtest (default: 30)",
    )

    parser.add_argument(
        "--balance",
        type=float,
        default=50.0,
        help="Starting balance in USDC (default: 50)",
    )

    parser.add_argument(
        "--strategy",
        choices=["all", "arbitrage", "market_maker", "spike_reversion", "copy_trader"],
        default="all",
        help="Strategy to run or backtest (default: all)",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override log level from config",
    )

    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Disable Prometheus metrics exporter",
    )

    return parser.parse_args()


def run_backtest(args):
    """Run backtest simulation."""
    from src.backtest.backtester import Backtester

    logger.info("=" * 60)
    logger.info("STARTING BACKTEST SIMULATION")
    logger.info("=" * 60)

    backtester = Backtester()
    result = backtester.run(
        strategy=args.strategy,
        days=args.days,
        start_balance=args.balance,
    )

    # Save report
    report_path = f"logs/backtest_reports/backtest_{args.strategy}_{args.days}d.json"
    backtester.save_report(result, report_path)

    logger.info(f"Backtest complete. Report saved to {report_path}")

    return result


def run_trading(args, config):
    """Run live or simulation trading."""
    # Determine mode
    if args.live:
        simulation_mode = False
    elif args.simulation:
        simulation_mode = True
    else:
        # Use config setting
        simulation_mode = config.get("general", {}).get("mode", "simulation") == "simulation"

    # Create trading loop
    loop = create_trading_loop(config_path=args.config)

    # Override balance if specified
    if args.balance != 50.0:
        loop.risk_manager.current_balance = args.balance
        loop.risk_manager.starting_balance = args.balance

    # Start trading
    logger.info("=" * 60)
    logger.info("POLYPRINTING2026 TRADING BOT")
    logger.info("=" * 60)
    logger.info(f"Mode: {'SIMULATION' if simulation_mode else 'LIVE TRADING'}")
    logger.info(f"Starting Balance: ${args.balance}")
    logger.info(f"Config: {args.config}")
    logger.info("=" * 60)

    if not simulation_mode:
        logger.warning("=" * 60)
        logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
        logger.warning("Press Ctrl+C to stop at any time")
        logger.warning("=" * 60)

    try:
        loop.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        raise


def main():
    """Main entry point."""
    # Parse arguments
    args = parse_args()

    # Load configuration
    config = load_config(args.config)

    # Setup logging
    log_level = args.log_level or config.get("general", {}).get("log_level", "INFO")
    setup_logging(
        log_level=log_level,
        log_file=config.get("logging", {}).get("main_log", "logs/polybot.log"),
        enable_console=config.get("logging", {}).get("console_logging", True),
    )

    # Initialize metrics
    if not args.no_metrics:
        metrics_port = config.get("general", {}).get("metrics_port", 9090)
        metrics = init_metrics(port=metrics_port, enabled=True)
        metrics.start()
        metrics.set_info(version="1.0.0", mode="simulation" if args.simulation else "live")

    # Print startup banner
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                   POLYPRINTING2026                        ║
    ║           Automated Polymarket Trading Bot                ║
    ║                                                           ║
    ║  Strategies:                                              ║
    ║    • YES/NO Arbitrage (risk-free when sum < $1)          ║
    ║    • Maker Market Making (earn rebates)                   ║
    ║    • Volatility Spike Reversion                          ║
    ║    • Selective Copy Trading                               ║
    ║                                                           ║
    ║  Target: 100-300% annualized on $50 starting capital     ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    try:
        if args.backtest:
            run_backtest(args)
        else:
            run_trading(args, config)

    except Exception as e:
        logger.critical(f"Unhandled exception: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
