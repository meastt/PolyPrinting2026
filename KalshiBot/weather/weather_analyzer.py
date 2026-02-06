#!/usr/bin/env python3
"""
Weather Analyzer - Main prediction engine

Responsibilities:
1. Fetch multi-source forecasts
2. Apply bias adjustments
3. Calculate ensemble predictions
4. Map predictions to Kalshi temperature ranges
5. Calculate confidence scores
"""

import logging
import statistics
from typing import Dict, List, Optional
from datetime import datetime

from weather_api_client import WeatherAPIClient
from bias_calculator import BiasCalculator
from station_mapper import get_station_info, get_market_type

logger = logging.getLogger(__name__)


class WeatherAnalyzer:
    """Main weather prediction engine."""

    def __init__(self):
        self.api_client = WeatherAPIClient()
        self.bias_calc = BiasCalculator()
        logger.info("WeatherAnalyzer initialized")

    def analyze_market(self, market: Dict) -> Optional[Dict]:
        """
        Analyze a Kalshi weather market and generate prediction.

        Args:
            market: Dict with Kalshi market info (ticker, ranges, etc.)

        Returns:
            Dict with prediction and confidence or None if analysis fails
        """
        ticker = market.get("ticker", "")

        # Get station info
        station_info = get_station_info(ticker)
        if not station_info:
            logger.error(f"No station mapping for {ticker}")
            return None

        # Determine if HIGH or LOW market
        metric = get_market_type(ticker)
        if not metric:
            logger.error(f"Cannot determine market type for {ticker}")
            return None

        logger.info(f"Analyzing {ticker} ({metric.upper()}) for {station_info['display_name']}")

        # Fetch all forecasts
        forecasts = self.api_client.get_all_forecasts(station_info)

        if not forecasts:
            logger.error(f"No forecasts available for {ticker}")
            return None

        # Apply bias adjustments and collect predictions
        predictions = []
        ensemble_details = {}

        for forecast in forecasts:
            source = forecast["source"]
            raw_temp = forecast.get(f"temperature_{metric}")

            if raw_temp is None:
                logger.warning(f"No {metric} forecast from {source}")
                continue

            # Apply bias adjustment
            adjusted = self.bias_calc.get_adjusted_forecast(
                station_info["nws_station"],
                source,
                metric,
                raw_temp
            )

            predictions.append(adjusted["adjusted_forecast"])
            ensemble_details[source] = {
                "raw": raw_temp,
                "adjusted": adjusted["adjusted_forecast"],
                "bias_applied": adjusted.get("bias_applied", 0.0),
                "bias_available": adjusted["bias_available"]
            }

        if not predictions:
            logger.error(f"No valid predictions for {ticker}")
            return None

        # Calculate ensemble prediction (weighted average)
        # Give NWS higher weight since it's the resolver
        weights = self._calculate_weights(ensemble_details)
        predicted_temp = sum(p * w for p, w in zip(predictions, weights.values()))

        # Calculate confidence based on ensemble agreement
        std_dev = statistics.stdev(predictions) if len(predictions) > 1 else 0.0
        confidence = self._calculate_confidence(std_dev, ensemble_details)

        # Map to Kalshi range
        predicted_range = self._map_to_range(predicted_temp, market)

        result = {
            "ticker": ticker,
            "station": station_info["nws_station"],
            "city": station_info["city"],
            "metric": metric,
            "predicted_temp": round(predicted_temp, 2),
            "predicted_range": predicted_range,
            "confidence": round(confidence, 3),
            "ensemble": ensemble_details,
            "ensemble_std_dev": round(std_dev, 2),
            "num_sources": len(predictions),
            "timestamp": datetime.now().isoformat()
        }

        logger.info(
            f"Prediction for {ticker}: {predicted_temp:.1f}°F "
            f"(range: {predicted_range}, confidence: {confidence:.0%})"
        )

        return result

    def _calculate_weights(self, ensemble: Dict) -> Dict:
        """
        Calculate source weights for ensemble averaging.
        NWS gets highest weight (it's the resolver).
        """
        weights = {}
        total_weight = 0

        for source in ensemble.keys():
            if source == "NWS":
                weights[source] = 0.40  # 40% weight
            else:
                weights[source] = 0.20  # 20% each for others

            total_weight += weights[source]

        # Normalize to sum to 1.0
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}

        return weights

    def _calculate_confidence(self, std_dev: float, ensemble: Dict) -> float:
        """
        Calculate confidence score based on ensemble agreement and bias availability.

        Args:
            std_dev: Standard deviation of predictions
            ensemble: Ensemble details dict

        Returns:
            Confidence score between 0 and 1
        """
        # Base confidence from ensemble agreement
        # Lower std_dev = higher confidence
        # Typical weather forecast variation is 2-5°F
        if std_dev < 1.0:
            agreement_score = 0.95
        elif std_dev < 2.0:
            agreement_score = 0.85
        elif std_dev < 3.0:
            agreement_score = 0.75
        elif std_dev < 5.0:
            agreement_score = 0.60
        else:
            agreement_score = 0.40

        # Bonus for having bias data
        bias_available_count = sum(1 for e in ensemble.values() if e["bias_available"])
        bias_score = bias_available_count / len(ensemble) if ensemble else 0

        # Weighted combination
        confidence = (agreement_score * 0.7) + (bias_score * 0.3)

        return max(0.0, min(1.0, confidence))

    def _map_to_range(self, predicted_temp: float, market: Dict) -> Optional[str]:
        """
        Map predicted temperature to closest Kalshi range.

        Args:
            predicted_temp: Predicted temperature in °F
            market: Market dict with range info

        Returns:
            Range string like "69-70" or None if can't determine
        """
        # Extract ranges from market
        # Kalshi markets have ranges in subtitle or as separate contracts
        ranges = market.get("ranges", [])

        if not ranges:
            # Try parsing from subtitle
            subtitle = market.get("subtitle", "")
            # Example subtitle: "Between 68-69°F"
            # This is simplified - real implementation would parse various formats
            logger.warning(f"No ranges provided for {market.get('ticker')}, attempting to parse")
            return None

        # Find closest range
        best_range = None
        min_distance = float('inf')

        for range_str in ranges:
            # Parse range like "68-69" or "70-71"
            try:
                parts = range_str.replace("°F", "").replace("°", "").split("-")
                low = float(parts[0])
                high = float(parts[1])

                # Check if prediction falls within range
                if low <= predicted_temp <= high:
                    return range_str

                # Calculate distance to range midpoint
                midpoint = (low + high) / 2
                distance = abs(predicted_temp - midpoint)

                if distance < min_distance:
                    min_distance = distance
                    best_range = range_str

            except (ValueError, IndexError) as e:
                logger.warning(f"Could not parse range: {range_str}")
                continue

        return best_range

    def get_prediction_summary(self, ticker: str) -> Optional[Dict]:
        """
        Get a simplified prediction summary for display.

        Args:
            ticker: Kalshi ticker

        Returns:
            Simplified summary dict
        """
        # This would call analyze_market with cached market data
        # For now, returns None (would need market data cache)
        logger.warning("get_prediction_summary not yet implemented with caching")
        return None
