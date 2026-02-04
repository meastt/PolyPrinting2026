#!/usr/bin/env python3
"""
Position Manager - Track Portfolio State

Responsible for:
1. Tracking active positions (wallet state)
2. Calculating Realized and Unrealized P&L
3. Logging trade history
"""

import json
import logging
import time
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class PositionManager:
    def __init__(self, state_file: str = "/app/config/portfolio_state.json"):
        self.state_file = Path(state_file)
        self.positions = {}      # ticker -> {side: 'yes'/'no', count: int, avg_price: float}
        self.cash_balance = 0.0  # Available cash (from Kalshi API)
        self.realized_pnl = 0.0
        self.trade_history = []
        
        self.load_state()

    def load_state(self):
        """Load portfolio state from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.positions = data.get("positions", {})
                    self.realized_pnl = data.get("realized_pnl", 0.0)
                    self.trade_history = data.get("trade_history", [])
                    logger.info(f"Loaded portfolio: {len(self.positions)} active positions")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save_state(self):
        """Save portfolio state to disk."""
        data = {
            "updated_at": int(time.time()),
            "positions": self.positions,
            "realized_pnl": self.realized_pnl,
            "trade_history": self.trade_history[-50:] # Keep last 50 trades in hot state
        }
        try:
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def update_balance(self, balance: float):
        """Update current cash balance from API."""
        self.cash_balance = balance

    def record_trade(self, ticker: str, side: str, price: float, count: int, action: str):
        """
        Record a trade execution.
        action: 'BUY' or 'SELL'
        side: 'yes' or 'no'
        """
        trade = {
            "timestamp": int(time.time()),
            "date": datetime.now().isoformat(),
            "ticker": ticker,
            "side": side,
            "action": action,
            "price": price,
            "count": count,
            "cost": price * count
        }
        self.trade_history.append(trade)
        
        # Update position tracking
        pos_key = f"{ticker}"
        current_pos = self.positions.get(pos_key, {"yes": 0, "no": 0, "cost_basis": 0.0})
        
        if action == "BUY":
            # Increase position
            # Simply avg cost basis if adding to same side
            # Taking opposite side reduces exposure (Kalshi nets out? Usually yes for many markets, 
            # but simplest model is independent sides or just net inventory if strictly binary)
            # For simplicity, treating 'yes' and 'no' inventory separately but checking offsetting
             
            # Simplified: Just track net contracts. + for Yes, - for No? 
            # Kalshi API has distinct positions for Yes/No.
            pass 
            
            # NOTE: For this MVP, we will rely on Kalshi API for the master source of truth for POSITIONS
            # via the /portfolio/positions endpoint. 
            # This class will focused on logging and P&L estimation locally.
        
        self.save_state()
        logger.info(f"Trade recorded: {action} {count} {ticker} {side} @ {price}")

    def get_summary(self):
        """Get text summary for Telegram."""
        return (
            f"💰 *Portfolio Summary*\n"
            f"Cash: ${self.cash_balance:.2f}\n"
            f"Realized P&L: ${self.realized_pnl:.2f}\n"
            f"Active Trades: {len(self.positions)}"
        )
