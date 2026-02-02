"""
Logging Configuration

Provides structured logging for the trading bot with:
- Console output with colors
- File logging with rotation
- Trade-specific logging to CSV
- Sensitive data masking
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
from typing import Optional

# Try to import colorama for colored output
try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    COLORS_AVAILABLE = True
except ImportError:
    COLORS_AVAILABLE = False


class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to log levels."""

    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        # Add color to levelname
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"

        return super().format(record)


class SensitiveDataFilter(logging.Filter):
    """Filter that masks sensitive data in log messages."""

    SENSITIVE_PATTERNS = [
        "api_key",
        "api_secret",
        "private_key",
        "passphrase",
        "password",
        "secret",
    ]

    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            msg = record.msg
            for pattern in self.SENSITIVE_PATTERNS:
                # Mask values that look like secrets
                if pattern.lower() in msg.lower():
                    # Simple masking - replace values after = or :
                    import re
                    msg = re.sub(
                        rf'({pattern}["\']?\s*[:=]\s*["\']?)([^"\'\s]+)',
                        r'\1***MASKED***',
                        msg,
                        flags=re.IGNORECASE
                    )
            record.msg = msg
        return True


# Global logger cache
_loggers = {}


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = "logs/trading.log",
    max_size_mb: int = 10,
    backup_count: int = 5,
    enable_console: bool = True,
    enable_colors: bool = True,
    mask_sensitive: bool = True,
) -> logging.Logger:
    """
    Set up logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Path to log file (None to disable file logging)
        max_size_mb: Maximum log file size before rotation
        backup_count: Number of backup files to keep
        enable_console: Enable console output
        enable_colors: Enable colored console output
        mask_sensitive: Mask sensitive data in logs

    Returns:
        Root logger
    """
    # Get root logger
    root_logger = logging.getLogger("polybot")
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers = []

    # Create formatters
    detailed_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    simple_format = "%(asctime)s | %(levelname)-8s | %(message)s"

    # Console handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)

        if enable_colors and COLORS_AVAILABLE:
            console_handler.setFormatter(ColoredFormatter(simple_format))
        else:
            console_handler.setFormatter(logging.Formatter(simple_format))

        root_logger.addHandler(console_handler)

    # File handler
    if log_file:
        # Ensure directory exists
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(detailed_format))

        root_logger.addHandler(file_handler)

    # Add sensitive data filter
    if mask_sensitive:
        root_logger.addFilter(SensitiveDataFilter())

    root_logger.info(f"Logging initialized (level={log_level}, file={log_file})")

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance
    """
    # Normalize name
    if name.startswith("src."):
        name = name[4:]  # Remove 'src.' prefix

    logger_name = f"polybot.{name}"

    if logger_name not in _loggers:
        logger = logging.getLogger(logger_name)
        _loggers[logger_name] = logger

        # Initialize root logger if not done
        if not logging.getLogger("polybot").handlers:
            setup_logging()

    return _loggers[logger_name]


class TradeLogger:
    """
    Specialized logger for trade events.

    Logs trades to CSV file for analysis.
    """

    def __init__(self, log_file: str = "logs/trades.csv"):
        """
        Initialize trade logger.

        Args:
            log_file: Path to CSV file
        """
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        # Write header if file doesn't exist
        if not self.log_file.exists():
            self._write_header()

    def _write_header(self):
        """Write CSV header."""
        with open(self.log_file, "w") as f:
            f.write(
                "timestamp,market_id,outcome,side,size,price,fees,rebates,"
                "strategy,order_type,status\n"
            )

    def log_trade(
        self,
        market_id: str,
        outcome: str,
        side: str,
        size: float,
        price: float,
        fees: float = 0.0,
        rebates: float = 0.0,
        strategy: str = "",
        order_type: str = "limit",
        status: str = "executed",
    ):
        """
        Log a trade to CSV.

        Args:
            market_id: Market condition ID
            outcome: "Yes" or "No"
            side: "buy" or "sell"
            size: Trade size
            price: Execution price
            fees: Fees paid
            rebates: Rebates earned
            strategy: Strategy name
            order_type: "limit" or "market"
            status: Trade status
        """
        timestamp = datetime.now().isoformat()

        with open(self.log_file, "a") as f:
            f.write(
                f"{timestamp},{market_id},{outcome},{side},{size:.4f},"
                f"{price:.4f},{fees:.4f},{rebates:.4f},{strategy},"
                f"{order_type},{status}\n"
            )


class PnLLogger:
    """
    Logger for daily P&L tracking.
    """

    def __init__(self, log_file: str = "logs/daily_pnl.csv"):
        """Initialize P&L logger."""
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.log_file.exists():
            self._write_header()

    def _write_header(self):
        """Write CSV header."""
        with open(self.log_file, "w") as f:
            f.write(
                "date,starting_balance,ending_balance,pnl,pnl_pct,"
                "trades,wins,losses,rebates_earned\n"
            )

    def log_daily(
        self,
        date: str,
        starting_balance: float,
        ending_balance: float,
        trades: int,
        wins: int,
        losses: int,
        rebates_earned: float,
    ):
        """Log daily P&L summary."""
        pnl = ending_balance - starting_balance
        pnl_pct = (pnl / starting_balance * 100) if starting_balance > 0 else 0

        with open(self.log_file, "a") as f:
            f.write(
                f"{date},{starting_balance:.2f},{ending_balance:.2f},"
                f"{pnl:.2f},{pnl_pct:.2f},{trades},{wins},{losses},"
                f"{rebates_earned:.2f}\n"
            )
