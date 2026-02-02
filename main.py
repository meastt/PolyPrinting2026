#!/usr/bin/env python3
"""
PolyPrinting2026 - Automated Prediction Market Trading Bot

Supports:
- Kalshi (US legal, CFTC-regulated)
- Polymarket (non-US only)

Usage:
    python main.py                    # Run with config defaults
    python main.py --exchange kalshi  # Force Kalshi
    python main.py --simulation       # Force simulation mode
    python main.py --config path.yaml # Custom config file
"""

import argparse
import sys
import os

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.logger import get_logger
from src.utils.helpers import load_config

logger = get_logger(__name__)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Automated Prediction Market Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                          # Run with config.yaml settings
  python main.py --exchange kalshi        # Trade on Kalshi (US legal)
  python main.py --simulation             # Paper trading mode
  python main.py --exchange polymarket    # Trade on Polymarket (non-US)
        """
    )

    parser.add_argument(
        "--config", "-c",
        default="config/config.yaml",
        help="Path to configuration file (default: config/config.yaml)"
    )

    parser.add_argument(
        "--exchange", "-e",
        choices=["kalshi", "polymarket"],
        help="Exchange to trade on (overrides config)"
    )

    parser.add_argument(
        "--simulation", "-s",
        action="store_true",
        help="Run in simulation mode (paper trading)"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live mode (real money - use with caution!)"
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {args.config}")
        logger.info("Copy config/config.example.yaml to config/config.yaml and customize")
        sys.exit(1)

    # Determine exchange
    exchange = args.exchange or config.get("general", {}).get("exchange", "kalshi")

    # Determine mode
    if args.live:
        simulation_mode = False
    elif args.simulation:
        simulation_mode = True
    else:
        simulation_mode = config.get("general", {}).get("mode", "simulation") == "simulation"

    # Print banner
    print("=" * 60)
    print("PolyPrinting2026 - Prediction Market Trading Bot")
    print("=" * 60)
    print(f"Exchange: {exchange.upper()}")
    print(f"Mode: {'SIMULATION' if simulation_mode else '🔴 LIVE'}")
    print(f"Config: {args.config}")
    print("=" * 60)

    if not simulation_mode:
        print("\n⚠️  WARNING: LIVE TRADING MODE - REAL MONEY AT RISK!")
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            print("Cancelled.")
            sys.exit(0)

    # Start appropriate trading loop
    if exchange == "kalshi":
        start_kalshi_bot(args.config, simulation_mode)
    else:
        start_polymarket_bot(args.config, simulation_mode)


def start_kalshi_bot(config_path: str, simulation_mode: bool):
    """Start Kalshi trading bot."""
    logger.info("Starting Kalshi trading bot...")

    try:
        from src.core.kalshi_trading_loop import create_kalshi_trading_loop

        loop = create_kalshi_trading_loop(config_path)

        # Override simulation mode if specified
        loop.simulation_mode = simulation_mode

        # Start the loop
        loop.start()

    except ImportError as e:
        logger.error(f"Failed to import Kalshi modules: {e}")
        logger.info("Ensure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)


def start_polymarket_bot(config_path: str, simulation_mode: bool):
    """Start Polymarket trading bot."""
    logger.info("Starting Polymarket trading bot...")

    print("\n⚠️  WARNING: Polymarket is NOT available in the USA!")
    print("US users should use Kalshi instead (--exchange kalshi)\n")

    try:
        from src.core.trading_loop import create_trading_loop

        loop = create_trading_loop(config_path)

        # Override simulation mode if specified
        loop.simulation_mode = simulation_mode

        # Start the loop
        loop.start()

    except ImportError as e:
        logger.error(f"Failed to import Polymarket modules: {e}")
        logger.info("Ensure all dependencies are installed: pip install -r requirements.txt")
        sys.exit(1)


if __name__ == "__main__":
    main()
