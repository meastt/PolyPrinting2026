"""
Helper Functions

Utility functions used across the trading bot including:
- Configuration loading/saving
- Time utilities
- Data formatting
- Safe operations
"""

import os
import time
import json
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime, timedelta
import yaml

from src.utils.logger import get_logger

logger = get_logger(__name__)


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """
    Load configuration from YAML file.

    Args:
        config_path: Path to configuration file

    Returns:
        Configuration dictionary
    """
    config_file = Path(config_path)

    if not config_file.exists():
        logger.warning(f"Config file not found: {config_path}, using defaults")
        return get_default_config()

    try:
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)

        logger.info(f"Configuration loaded from {config_path}")
        return config or get_default_config()

    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return get_default_config()


def save_config(config: Dict[str, Any], config_path: str = "config/config.yaml") -> bool:
    """
    Save configuration to YAML file.

    Args:
        config: Configuration dictionary
        config_path: Path to save to

    Returns:
        True if saved successfully
    """
    try:
        config_file = Path(config_path)
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Configuration saved to {config_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False


def get_default_config() -> Dict[str, Any]:
    """Get default configuration."""
    return {
        "general": {
            "mode": "simulation",
            "starting_balance": 50.0,
            "poll_interval_seconds": 2,
            "log_level": "INFO",
        },
        "strategies": {
            "arbitrage": {"enabled": True},
            "market_making": {"enabled": True},
            "spike_reversion": {"enabled": True},
            "copy_trading": {"enabled": False},
        },
        "risk": {
            "max_position_percent": 2.0,
            "daily_drawdown_limit": 0.05,
            "max_open_positions": 10,
        },
    }


def get_env_or_config(
    env_var: str,
    config: Dict[str, Any],
    config_keys: List[str],
    default: Any = None,
) -> Any:
    """
    Get value from environment variable or config, with fallback.

    Args:
        env_var: Environment variable name
        config: Configuration dictionary
        config_keys: List of nested keys in config
        default: Default value if not found

    Returns:
        Value from env, config, or default
    """
    # Try environment first
    env_value = os.getenv(env_var)
    if env_value is not None:
        return env_value

    # Try config
    value = config
    for key in config_keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default

    return value if value is not None else default


def format_usdc(amount: float, include_sign: bool = False) -> str:
    """
    Format USDC amount for display.

    Args:
        amount: Amount in USDC
        include_sign: Include + for positive amounts

    Returns:
        Formatted string (e.g., "$50.00" or "+$5.25")
    """
    sign = ""
    if include_sign and amount > 0:
        sign = "+"

    return f"{sign}${amount:,.2f}"


def format_percent(value: float, include_sign: bool = False) -> str:
    """
    Format percentage for display.

    Args:
        value: Percentage value (0.05 = 5%)
        include_sign: Include + for positive values

    Returns:
        Formatted string (e.g., "5.00%" or "+2.50%")
    """
    sign = ""
    if include_sign and value > 0:
        sign = "+"

    return f"{sign}{value * 100:.2f}%"


def format_timestamp(timestamp: float, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """
    Format Unix timestamp for display.

    Args:
        timestamp: Unix timestamp
        fmt: strftime format string

    Returns:
        Formatted datetime string
    """
    return datetime.fromtimestamp(timestamp).strftime(fmt)


def time_until(target_time: float) -> str:
    """
    Get human-readable time until a target.

    Args:
        target_time: Unix timestamp

    Returns:
        String like "2h 30m" or "45s"
    """
    diff = target_time - time.time()

    if diff <= 0:
        return "now"

    hours = int(diff // 3600)
    minutes = int((diff % 3600) // 60)
    seconds = int(diff % 60)

    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"


def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
):
    """
    Retry a function with exponential backoff.

    Args:
        func: Function to call
        max_retries: Maximum retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exceptions: Exceptions to catch and retry

    Returns:
        Function result or raises last exception
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s: {e}"
                )
                time.sleep(delay)

    raise last_exception


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division with default for zero denominator."""
    if denominator == 0:
        return default
    return numerator / denominator


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to range [min_val, max_val]."""
    return max(min_val, min(value, max_val))


def truncate_string(s: str, max_length: int = 50, suffix: str = "...") -> str:
    """Truncate string to maximum length with suffix."""
    if len(s) <= max_length:
        return s
    return s[:max_length - len(suffix)] + suffix


def parse_market_question(question: str) -> Dict[str, Any]:
    """
    Parse a market question to extract asset and direction.

    Args:
        question: Market question text

    Returns:
        Dict with extracted info
    """
    question_lower = question.lower()

    result = {
        "asset": None,
        "direction": None,
        "threshold": None,
        "timeframe": None,
    }

    # Detect asset
    if "btc" in question_lower or "bitcoin" in question_lower:
        result["asset"] = "BTC"
    elif "eth" in question_lower or "ethereum" in question_lower:
        result["asset"] = "ETH"
    elif "sol" in question_lower or "solana" in question_lower:
        result["asset"] = "SOL"

    # Detect direction
    if "above" in question_lower or "higher" in question_lower or "up" in question_lower:
        result["direction"] = "up"
    elif "below" in question_lower or "lower" in question_lower or "down" in question_lower:
        result["direction"] = "down"

    return result


def calculate_annualized_return(
    return_pct: float,
    days: int,
) -> float:
    """
    Calculate annualized return from period return.

    Args:
        return_pct: Period return (e.g., 0.50 for 50%)
        days: Number of days in period

    Returns:
        Annualized return
    """
    if days <= 0:
        return 0

    # (1 + r)^(365/days) - 1
    return ((1 + return_pct) ** (365 / days)) - 1


def validate_usdc_amount(amount: float, min_amount: float = 0.01) -> bool:
    """Validate USDC amount is valid for trading."""
    return amount >= min_amount and amount < 1_000_000  # Reasonable upper bound


def mask_address(address: str, visible_chars: int = 6) -> str:
    """
    Mask an Ethereum address for logging.

    Args:
        address: Full address
        visible_chars: Number of chars to show at start and end

    Returns:
        Masked address like "0x1234...abcd"
    """
    if not address or len(address) < visible_chars * 2:
        return address

    return f"{address[:visible_chars]}...{address[-visible_chars:]}"


def get_market_category(question: str) -> str:
    """
    Determine market category from question.

    Args:
        question: Market question

    Returns:
        Category string
    """
    question_lower = question.lower()

    crypto_keywords = ["bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol"]
    for keyword in crypto_keywords:
        if keyword in question_lower:
            return "Crypto"

    return "Other"


class RateLimiter:
    """
    Simple rate limiter for API calls.
    """

    def __init__(self, max_calls: int, period_seconds: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            max_calls: Maximum calls per period
            period_seconds: Period length in seconds
        """
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._calls: List[float] = []

    def acquire(self) -> bool:
        """
        Try to acquire a rate limit slot.

        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        cutoff = now - self.period_seconds

        # Remove old calls
        self._calls = [t for t in self._calls if t > cutoff]

        if len(self._calls) >= self.max_calls:
            return False

        self._calls.append(now)
        return True

    def wait(self) -> None:
        """Wait until a slot is available."""
        while not self.acquire():
            time.sleep(0.1)
