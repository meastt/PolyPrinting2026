#!/usr/bin/env python3
"""
Weather Strategist - AI-powered weather trading decisions

Main loop:
1. Scan for tradeable weather markets
2. Get weather predictions for each
3. Use AI to validate trades
4. Generate signals for trader
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from groq import Groq
from dotenv import load_dotenv

# Import weather modules
sys.path.append("/app/weather")
from weather_analyzer import WeatherAnalyzer
from weather_market_scanner import WeatherMarketScanner
from station_mapper import get_station_info

load_dotenv()

# Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEATHER_SIGNALS_PATH = Path("/app/config/weather_signals.json")
WEATHER_STATE_PATH = Path("/app/config/weather_state.json")
LOG_PATH = Path("/app/logs/weather_strategist.log")

# Trading parameters
MIN_CONFIDENCE = float(os.getenv("WEATHER_MIN_CONFIDENCE", "0.75"))
MIN_EDGE = float(os.getenv("WEATHER_MIN_EDGE", "0.10"))
MAX_POSITION_SIZE = int(os.getenv("WEATHER_MAX_POSITION_SIZE", "2"))
SCAN_INTERVAL = int(os.getenv("WEATHER_SCAN_INTERVAL", "1800"))  # 30 minutes

# Logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [WEATHER_STRATEGIST] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class WeatherStrategist:
    """AI-powered weather trading strategist."""

    def __init__(self):
        self.analyzer = WeatherAnalyzer()
        self.scanner = WeatherMarketScanner()
        self.groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

        logger.info("WeatherStrategist initialized")
        logger.info(f"Min Confidence: {MIN_CONFIDENCE:.0%}, Min Edge: {MIN_EDGE:.0%}")

        if not self.groq_client:
            logger.warning("GROQ_API_KEY not set - AI validation disabled")

    def generate_signals(self) -> List[Dict]:
        """
        Main signal generation loop.

        Returns:
            List of trade signal dicts
        """
        logger.info("Starting signal generation...")

        # Check if trading is paused
        if self._is_paused():
            logger.info("Trading paused, skipping signal generation")
            return []

        # Fetch tradeable markets
        markets = self.scanner.get_tradeable_markets(min_hours=2.0, max_hours=24.0)
        logger.info(f"Found {len(markets)} tradeable markets")

        signals = []

        for market in markets:
            try:
                # Generate prediction
                prediction = self.analyzer.analyze_market({
                    "ticker": market["ticker"],
                    "ranges": [market["range"]] if market["range"] else []
                })

                if not prediction:
                    logger.warning(f"No prediction for {market['ticker']}")
                    continue

                # Check confidence threshold
                if prediction["confidence"] < MIN_CONFIDENCE:
                    logger.debug(
                        f"Skipping {market['ticker']}: confidence {prediction['confidence']:.0%} "
                        f"< threshold {MIN_CONFIDENCE:.0%}"
                    )
                    continue

                # Get market price
                orderbook = self.scanner.get_market_orderbook(market["ticker"])
                if not orderbook or orderbook["yes_ask"] is None:
                    logger.warning(f"No orderbook for {market['ticker']}")
                    continue

                market_price = orderbook["yes_ask"]

                # Calculate edge
                # Our prediction confidence vs market price
                edge = prediction["confidence"] - market_price

                if edge < MIN_EDGE:
                    logger.debug(
                        f"Skipping {market['ticker']}: edge {edge:.0%} < threshold {MIN_EDGE:.0%}"
                    )
                    continue

                # AI validation
                should_trade, reasoning = self._ai_validate_trade(prediction, market, market_price)

                if not should_trade:
                    logger.info(f"AI rejected {market['ticker']}: {reasoning}")
                    continue

                # Generate signal
                signal = {
                    "ticker": market["ticker"],
                    "action": "BUY_YES",
                    "range": prediction["predicted_range"],
                    "predicted_temp": prediction["predicted_temp"],
                    "confidence": prediction["confidence"],
                    "market_price": market_price,
                    "edge": edge,
                    "size": MAX_POSITION_SIZE,
                    "reasoning": reasoning,
                    "ensemble": prediction["ensemble"],
                    "timestamp": int(time.time()),
                    "status": "PENDING"
                }

                signals.append(signal)
                logger.info(
                    f"✅ Signal generated: {market['ticker']} @ ${market_price:.2f} "
                    f"(confidence: {prediction['confidence']:.0%}, edge: {edge:.0%})"
                )

            except Exception as e:
                logger.error(f"Error processing {market.get('ticker', 'unknown')}: {e}")
                continue

        # Save signals
        self._save_signals(signals)

        logger.info(f"Generated {len(signals)} trading signals")
        return signals

    def _ai_validate_trade(self, prediction: Dict, market: Dict, market_price: float) -> tuple:
        """
        Use Groq/Llama to validate trade logic.

        Args:
            prediction: Weather prediction dict
            market: Market info dict
            market_price: Current market price

        Returns:
            Tuple of (should_trade: bool, reasoning: str)
        """
        if not self.groq_client:
            return True, "AI validation disabled"

        try:
            prompt = f"""You are a weather prediction trading analyst.

Market: {market['title']}
City: {prediction['city']}
Station: {prediction['station']}

Weather Ensemble Prediction:
"""
            for source, data in prediction['ensemble'].items():
                prompt += f"- {source}: {data['adjusted']:.1f}°F"
                if data['bias_available']:
                    prompt += f" (bias: {data['bias_applied']:+.1f}°F)"
                prompt += "\n"

            prompt += f"""
Final Prediction: {prediction['predicted_temp']:.1f}°F
Predicted Range: {prediction['predicted_range']}
Ensemble Std Dev: {prediction['ensemble_std_dev']:.1f}°F
Confidence: {prediction['confidence']:.0%}

Market Price: ${market_price:.2f} (implies {market_price:.0%} probability)
Our Edge: {(prediction['confidence'] - market_price):.0%}

Should we trade this? Consider:
1. Ensemble agreement (low std dev = high reliability)
2. Number of sources with bias adjustments
3. Market price vs our confidence
4. Edge magnitude
5. Any obvious red flags

Respond in JSON:
{{
    "should_trade": true/false,
    "reasoning": "Brief explanation (max 2 sentences)",
    "risk_factors": ["list any concerns"]
}}
"""

            response = self.groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)
            return result.get("should_trade", False), result.get("reasoning", "")

        except Exception as e:
            logger.error(f"AI validation error: {e}")
            return False, f"AI error: {e}"

    def _save_signals(self, signals: List[Dict]):
        """Save signals to JSON file for trader."""
        try:
            # Load existing signals
            existing = []
            if WEATHER_SIGNALS_PATH.exists():
                with open(WEATHER_SIGNALS_PATH, 'r') as f:
                    data = json.load(f)
                    existing = data.get("signals", [])

            # Add new signals with IDs
            for signal in signals:
                signal["id"] = f"{signal['ticker']}_{signal['timestamp']}"
                existing.append(signal)

            # Keep last 100 signals
            if len(existing) > 100:
                existing = existing[-100:]

            # Write back
            with open(WEATHER_SIGNALS_PATH, 'w') as f:
                json.dump({
                    "signals": existing,
                    "updated_at": int(time.time())
                }, f, indent=2)

            logger.info(f"Saved {len(signals)} signals to {WEATHER_SIGNALS_PATH}")

        except Exception as e:
            logger.error(f"Error saving signals: {e}")

    def _update_state(self):
        """Update strategist state file."""
        try:
            state = {
                "last_scan": int(time.time()),
                "status": "active",
                "updated_at": int(time.time())
            }

            with open(WEATHER_STATE_PATH, 'w') as f:
                json.dump(state, f, indent=2)

        except Exception as e:
            logger.error(f"Error updating state: {e}")

    def _is_paused(self) -> bool:
        """Check if trading is paused."""
        try:
            if not WEATHER_STATE_PATH.exists():
                return False

            with open(WEATHER_STATE_PATH, 'r') as f:
                state = json.load(f)
                return state.get("paused", False) or state.get("halted", False)

        except Exception:
            return False


def main():
    """Main entry point."""
    logger.info("🌤️  Weather Strategist starting...")

    strategist = WeatherStrategist()

    # Main loop
    while True:
        try:
            # Generate signals
            signals = strategist.generate_signals()

            # Update state
            strategist._update_state()

            # Sleep until next scan
            logger.info(f"Sleeping {SCAN_INTERVAL}s until next scan...")
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
