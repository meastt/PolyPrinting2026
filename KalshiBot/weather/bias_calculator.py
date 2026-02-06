#!/usr/bin/env python3
"""
Bias Calculator - Tracks and applies forecast biases

Core Strategy:
- Track historical errors: Forecast - Actual
- Maintain rolling 180-day averages by source and station
- Apply bias adjustments to new forecasts

Example:
If AccuWeather has historically been 2°F too warm for Central Park,
we subtract 2°F from today's AccuWeather forecast.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)

BIAS_FILE = Path("/app/config/forecast_bias.json")
HISTORY_FILE = Path("/app/config/bias_history.json")

# Rolling window for bias calculation (days)
BIAS_WINDOW_DAYS = 180


class BiasCalculator:
    """Manages forecast bias calculation and adjustment."""

    def __init__(self, bias_file: Path = BIAS_FILE, history_file: Path = HISTORY_FILE):
        self.bias_file = bias_file
        self.history_file = history_file

        # Initialize files if they don't exist
        self._initialize_files()

    def _initialize_files(self):
        """Create empty bias files if they don't exist."""
        if not self.bias_file.exists():
            self.bias_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.bias_file, 'w') as f:
                json.dump({}, f, indent=2)
            logger.info(f"Created bias file: {self.bias_file}")

        if not self.history_file.exists():
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_file, 'w') as f:
                json.dump({"records": []}, f, indent=2)
            logger.info(f"Created history file: {self.history_file}")

    def load_bias_data(self) -> Dict:
        """
        Load current bias averages.

        Returns:
            Dict with structure:
            {
                "KNYC": {
                    "AccuWeather": {"high_bias": 1.8, "low_bias": -0.5, "samples": 180},
                    "WeatherAPI": {...},
                    ...
                },
                ...
            }
        """
        try:
            with open(self.bias_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading bias data: {e}")
            return {}

    def save_bias_data(self, bias_data: Dict):
        """Save bias data to file."""
        try:
            with open(self.bias_file, 'w') as f:
                json.dump(bias_data, f, indent=2)
            logger.debug("Bias data saved")
        except Exception as e:
            logger.error(f"Error saving bias data: {e}")

    def load_history(self) -> list:
        """Load historical forecast error records."""
        try:
            with open(self.history_file, 'r') as f:
                data = json.load(f)
                return data.get("records", [])
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def save_history(self, records: list):
        """Save historical records."""
        try:
            with open(self.history_file, 'w') as f:
                json.dump({
                    "records": records,
                    "updated_at": datetime.now().isoformat()
                }, f, indent=2)
            logger.debug("History saved")
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def record_forecast_error(self, station: str, source: str, metric: str,
                             forecast: float, actual: float, date: str):
        """
        Record a forecast error and update rolling bias.

        Args:
            station: NWS station ID (e.g., "KNYC")
            source: Forecast source (e.g., "AccuWeather")
            metric: "high" or "low"
            forecast: Forecasted temperature
            actual: Actual observed temperature
            date: Date string (YYYY-MM-DD)
        """
        error = forecast - actual  # Positive = over-forecast, Negative = under-forecast

        # Add to history
        history = self.load_history()
        history.append({
            "date": date,
            "station": station,
            "source": source,
            "metric": metric,
            "forecast": forecast,
            "actual": actual,
            "error": error,
            "timestamp": datetime.now().isoformat()
        })
        self.save_history(history)

        logger.info(f"Recorded error: {station} {source} {metric} = {error:+.2f}°F ({forecast:.1f} vs {actual:.1f})")

        # Update rolling bias
        self._recalculate_bias()

    def _recalculate_bias(self):
        """Recalculate rolling bias averages from history."""
        history = self.load_history()

        # Filter to last N days
        cutoff = (datetime.now() - timedelta(days=BIAS_WINDOW_DAYS)).strftime("%Y-%m-%d")
        recent = [r for r in history if r["date"] >= cutoff]

        # Group by station, source, metric
        grouped = defaultdict(lambda: defaultdict(lambda: {"high": [], "low": []}))

        for record in recent:
            station = record["station"]
            source = record["source"]
            metric = record["metric"]
            error = record["error"]

            grouped[station][source][metric].append(error)

        # Calculate averages
        bias_data = {}
        for station, sources in grouped.items():
            bias_data[station] = {}
            for source, metrics in sources.items():
                high_errors = metrics["high"]
                low_errors = metrics["low"]

                bias_data[station][source] = {
                    "high_bias": sum(high_errors) / len(high_errors) if high_errors else 0.0,
                    "low_bias": sum(low_errors) / len(low_errors) if low_errors else 0.0,
                    "high_samples": len(high_errors),
                    "low_samples": len(low_errors),
                    "last_updated": datetime.now().isoformat()
                }

        self.save_bias_data(bias_data)
        logger.info(f"Bias recalculated: {len(bias_data)} stations")

    def get_bias(self, station: str, source: str, metric: str) -> Optional[float]:
        """
        Get current bias for a station/source/metric.

        Args:
            station: NWS station ID
            source: Forecast source
            metric: "high" or "low"

        Returns:
            Bias value in °F or None if insufficient data
        """
        bias_data = self.load_bias_data()

        try:
            bias = bias_data[station][source][f"{metric}_bias"]
            samples = bias_data[station][source][f"{metric}_samples"]

            # Require at least 30 samples for confidence
            if samples < 30:
                logger.debug(f"Insufficient samples for {station} {source} {metric}: {samples}")
                return None

            return bias

        except KeyError:
            logger.debug(f"No bias data for {station} {source} {metric}")
            return None

    def get_adjusted_forecast(self, station: str, source: str, metric: str,
                             raw_forecast: float) -> Dict:
        """
        Apply bias adjustment to a forecast.

        Args:
            station: NWS station ID
            source: Forecast source
            metric: "high" or "low"
            raw_forecast: Raw forecast temperature

        Returns:
            Dict with adjusted forecast and metadata
        """
        bias = self.get_bias(station, source, metric)

        if bias is None:
            return {
                "adjusted_forecast": raw_forecast,
                "raw_forecast": raw_forecast,
                "bias_applied": 0.0,
                "bias_available": False,
                "source": source
            }

        adjusted = raw_forecast - bias  # Subtract bias to correct

        return {
            "adjusted_forecast": adjusted,
            "raw_forecast": raw_forecast,
            "bias_applied": bias,
            "bias_available": True,
            "source": source,
            "adjustment": -bias  # Show as adjustment applied
        }

    def get_bias_summary(self, station: str) -> Dict:
        """
        Get summary of all biases for a station.

        Args:
            station: NWS station ID

        Returns:
            Dict with all source biases
        """
        bias_data = self.load_bias_data()
        return bias_data.get(station, {})

    def get_all_bias_summary(self) -> Dict:
        """Get complete bias summary for all stations."""
        return self.load_bias_data()
