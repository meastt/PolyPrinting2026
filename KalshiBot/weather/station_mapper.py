#!/usr/bin/env python3
"""
Station Mapper - Maps Kalshi market tickers to NWS weather stations

Critical for accurate resolution: Each Kalshi weather market specifies
the exact NWS station that will be used for settlement.

Example:
- KXHIGHNY markets resolve using Central Park NWS station (KNYC)
- KXHIGHBOS markets resolve using Boston Logan Airport (KBOS)
"""

import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Station mapping based on Kalshi market rules
# Each city's weather markets resolve to specific NWS observation stations
KALSHI_STATION_MAP = {
    "KXHIGHNY": {
        "city": "New York City",
        "nws_station": "KNYC",  # Central Park
        "display_name": "Central Park NWS",
        "lat": 40.7829,
        "lon": -73.9654,
        "accuweather_key": "349727",  # NYC location key
        "timezone": "America/New_York"
    },
    "KXLOWNY": {
        "city": "New York City",
        "nws_station": "KNYC",
        "display_name": "Central Park NWS",
        "lat": 40.7829,
        "lon": -73.9654,
        "accuweather_key": "349727",
        "timezone": "America/New_York"
    },
    "KXHIGHBOS": {
        "city": "Boston",
        "nws_station": "KBOS",  # Logan Airport
        "display_name": "Boston Logan Airport NWS",
        "lat": 42.3656,
        "lon": -71.0096,
        "accuweather_key": "348735",
        "timezone": "America/New_York"
    },
    "KXLOWBOS": {
        "city": "Boston",
        "nws_station": "KBOS",
        "display_name": "Boston Logan Airport NWS",
        "lat": 42.3656,
        "lon": -71.0096,
        "accuweather_key": "348735",
        "timezone": "America/New_York"
    },
    "KXHIGHCHI": {
        "city": "Chicago",
        "nws_station": "KORD",  # O'Hare Airport
        "display_name": "Chicago O'Hare Airport NWS",
        "lat": 41.9742,
        "lon": -87.9073,
        "accuweather_key": "348308",
        "timezone": "America/Chicago"
    },
    "KXLOWCHI": {
        "city": "Chicago",
        "nws_station": "KORD",
        "display_name": "Chicago O'Hare Airport NWS",
        "lat": 41.9742,
        "lon": -87.9073,
        "accuweather_key": "348308",
        "timezone": "America/Chicago"
    },
    "KXHIGHLA": {
        "city": "Los Angeles",
        "nws_station": "KLAX",  # LAX Airport
        "display_name": "Los Angeles Airport NWS",
        "lat": 33.9425,
        "lon": -118.4081,
        "accuweather_key": "347625",
        "timezone": "America/Los_Angeles"
    },
    "KXLOWLA": {
        "city": "Los Angeles",
        "nws_station": "KLAX",
        "display_name": "Los Angeles Airport NWS",
        "lat": 33.9425,
        "lon": -118.4081,
        "accuweather_key": "347625",
        "timezone": "America/Los_Angeles"
    },
    "KXHIGHSF": {
        "city": "San Francisco",
        "nws_station": "KSFO",  # SFO Airport
        "display_name": "San Francisco Airport NWS",
        "lat": 37.6213,
        "lon": -122.3790,
        "accuweather_key": "347629",
        "timezone": "America/Los_Angeles"
    },
    "KXLOWSF": {
        "city": "San Francisco",
        "nws_station": "KSFO",
        "display_name": "San Francisco Airport NWS",
        "lat": 37.6213,
        "lon": -122.3790,
        "accuweather_key": "347629",
        "timezone": "America/Los_Angeles"
    },
    "KXHIGHAUS": {
        "city": "Austin",
        "nws_station": "KAUS",  # Austin-Bergstrom Airport
        "display_name": "Austin Airport NWS",
        "lat": 30.1945,
        "lon": -97.6699,
        "accuweather_key": "351193",
        "timezone": "America/Chicago"
    },
    "KXLOWAUS": {
        "city": "Austin",
        "nws_station": "KAUS",
        "display_name": "Austin Airport NWS",
        "lat": 30.1945,
        "lon": -97.6699,
        "accuweather_key": "351193",
        "timezone": "America/Chicago"
    },
    "KXHIGHDEN": {
        "city": "Denver",
        "nws_station": "KDEN",  # Denver Airport
        "display_name": "Denver Airport NWS",
        "lat": 39.8617,
        "lon": -104.6731,
        "accuweather_key": "347810",
        "timezone": "America/Denver"
    },
    "KXLOWDEN": {
        "city": "Denver",
        "nws_station": "KDEN",
        "display_name": "Denver Airport NWS",
        "lat": 39.8617,
        "lon": -104.6731,
        "accuweather_key": "347810",
        "timezone": "America/Denver"
    },
    "KXHIGHMIA": {
        "city": "Miami",
        "nws_station": "KMIA",  # Miami Airport
        "display_name": "Miami Airport NWS",
        "lat": 25.7959,
        "lon": -80.2870,
        "accuweather_key": "347936",
        "timezone": "America/New_York"
    },
    "KXLOWMIA": {
        "city": "Miami",
        "nws_station": "KMIA",
        "display_name": "Miami Airport NWS",
        "lat": 25.7959,
        "lon": -80.2870,
        "accuweather_key": "347936",
        "timezone": "America/New_York"
    },
}


def get_station_info(kalshi_ticker: str) -> Optional[Dict]:
    """
    Extract station information from Kalshi ticker.

    Args:
        kalshi_ticker: Full ticker like "KXHIGHNY-26FEB06-B68" or series like "KXHIGHNY"

    Returns:
        Dict with station info or None if not found
    """
    # Extract series (remove date and contract suffix)
    series = kalshi_ticker.split('-')[0] if '-' in kalshi_ticker else kalshi_ticker

    station_info = KALSHI_STATION_MAP.get(series)

    if not station_info:
        logger.warning(f"No station mapping found for ticker: {kalshi_ticker} (series: {series})")
        return None

    logger.debug(f"Mapped {kalshi_ticker} to {station_info['display_name']}")
    return station_info


def get_all_stations() -> Dict[str, Dict]:
    """
    Get all station mappings.

    Returns:
        Dict of all station configurations
    """
    return KALSHI_STATION_MAP


def is_weather_ticker(ticker: str) -> bool:
    """
    Check if ticker is a weather market.

    Args:
        ticker: Kalshi ticker

    Returns:
        True if weather market
    """
    series = ticker.split('-')[0] if '-' in ticker else ticker
    return series in KALSHI_STATION_MAP


def get_market_type(ticker: str) -> Optional[str]:
    """
    Determine if market is for HIGH or LOW temperature.

    Args:
        ticker: Kalshi ticker

    Returns:
        "high" or "low" or None
    """
    series = ticker.split('-')[0] if '-' in ticker else ticker

    if "HIGH" in series:
        return "high"
    elif "LOW" in series:
        return "low"
    else:
        return None
