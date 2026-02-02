"""
PolyPrinting2026 - Automated Polymarket Trading Bot

A modular trading bot for Polymarket prediction markets with multiple
low-risk strategies optimized for small capital and compounding returns.

Strategies implemented:
- YES/NO Arbitrage: Risk-free profits when YES + NO < $1
- Maker Market Making: Earn rebates by providing liquidity
- Volatility Spike Reversion: Bet on mean reversion after sharp moves
- Selective Copy Trading: Mirror top performers with edge filter

See README.md for setup and usage instructions.
"""

__version__ = "1.0.0"
__author__ = "PolyPrinting2026"
