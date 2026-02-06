#!/usr/bin/env python3
"""
Weather API Client - Unified interface for multiple weather data sources

Supports:
- NWS (National Weather Service) - Primary resolver for Kalshi markets
- AccuWeather
- WeatherAPI.com
- OpenWeatherMap

All methods return standardized format for easy ensemble calculation.
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class WeatherAPIClient:
    """Unified client for fetching weather data from multiple sources."""

    def __init__(self):
        # API Configuration
        self.nws_base = "https://api.weather.gov"
        self.accuweather_key = os.getenv("ACCUWEATHER_API_KEY")
        self.weatherapi_key = os.getenv("WEATHERAPI_KEY")
        self.openweather_key = os.getenv("OPENWEATHER_KEY")

        # User agent required by NWS API
        self.nws_headers = {
            "User-Agent": "(KalshiBot Weather Trader, contact@example.com)"
        }

        logger.info("WeatherAPIClient initialized")
        if not self.accuweather_key:
            logger.warning("ACCUWEATHER_API_KEY not set")
        if not self.weatherapi_key:
            logger.warning("WEATHERAPI_KEY not set")
        if not self.openweather_key:
            logger.warning("OPENWEATHER_KEY not set")

    def get_nws_forecast(self, station_id: str, lat: float, lon: float) -> Optional[Dict]:
        """
        Fetch NWS forecast for specific coordinates.

        Args:
            station_id: NWS station identifier (e.g., "KNYC")
            lat: Latitude
            lon: Longitude

        Returns:
            Standardized forecast dict or None if error
        """
        try:
            # Step 1: Get grid point for coordinates
            point_url = f"{self.nws_base}/points/{lat},{lon}"
            point_res = requests.get(point_url, headers=self.nws_headers, timeout=10)
            point_res.raise_for_status()
            point_data = point_res.json()

            # Step 2: Get forecast URL from point data
            forecast_url = point_data["properties"]["forecast"]
            forecast_res = requests.get(forecast_url, headers=self.nws_headers, timeout=10)
            forecast_res.raise_for_status()
            forecast_data = forecast_res.json()

            # Extract today's high and tonight's low
            periods = forecast_data["properties"]["periods"]

            today_high = None
            tonight_low = None

            for period in periods[:4]:  # Check first few periods
                if period["isDaytime"]:
                    if today_high is None:
                        today_high = period["temperature"]
                else:
                    if tonight_low is None:
                        tonight_low = period["temperature"]

                if today_high and tonight_low:
                    break

            return {
                "source": "NWS",
                "station_id": station_id,
                "forecast_date": datetime.now().strftime("%Y-%m-%d"),
                "forecast_time": datetime.now().isoformat(),
                "temperature_high": float(today_high) if today_high else None,
                "temperature_low": float(tonight_low) if tonight_low else None,
                "confidence": 0.90  # NWS is generally very reliable
            }

        except Exception as e:
            logger.error(f"NWS forecast error for {station_id}: {e}")
            return None

    def get_accuweather_forecast(self, location_key: str, station_id: str) -> Optional[Dict]:
        """
        Fetch AccuWeather forecast for location.

        Args:
            location_key: AccuWeather location identifier
            station_id: Corresponding NWS station for tracking

        Returns:
            Standardized forecast dict or None
        """
        if not self.accuweather_key:
            return None

        try:
            url = f"http://dataservice.accuweather.com/forecasts/v1/daily/1day/{location_key}"
            params = {
                "apikey": self.accuweather_key,
                "details": "true"
            }

            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            daily = data["DailyForecasts"][0]
            temp_high = daily["Temperature"]["Maximum"]["Value"]
            temp_low = daily["Temperature"]["Minimum"]["Value"]

            # Convert Fahrenheit if needed
            if daily["Temperature"]["Maximum"]["Unit"] == "C":
                temp_high = (temp_high * 9/5) + 32
                temp_low = (temp_low * 9/5) + 32

            return {
                "source": "AccuWeather",
                "station_id": station_id,
                "forecast_date": datetime.now().strftime("%Y-%m-%d"),
                "forecast_time": datetime.now().isoformat(),
                "temperature_high": float(temp_high),
                "temperature_low": float(temp_low),
                "confidence": 0.85
            }

        except Exception as e:
            logger.error(f"AccuWeather forecast error for {location_key}: {e}")
            return None

    def get_weatherapi_forecast(self, lat: float, lon: float, station_id: str) -> Optional[Dict]:
        """
        Fetch WeatherAPI.com forecast.

        Args:
            lat: Latitude
            lon: Longitude
            station_id: Corresponding NWS station

        Returns:
            Standardized forecast dict or None
        """
        if not self.weatherapi_key:
            return None

        try:
            url = "http://api.weatherapi.com/v1/forecast.json"
            params = {
                "key": self.weatherapi_key,
                "q": f"{lat},{lon}",
                "days": 1
            }

            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            forecast_day = data["forecast"]["forecastday"][0]
            temp_high = forecast_day["day"]["maxtemp_f"]
            temp_low = forecast_day["day"]["mintemp_f"]

            return {
                "source": "WeatherAPI",
                "station_id": station_id,
                "forecast_date": datetime.now().strftime("%Y-%m-%d"),
                "forecast_time": datetime.now().isoformat(),
                "temperature_high": float(temp_high),
                "temperature_low": float(temp_low),
                "confidence": 0.80
            }

        except Exception as e:
            logger.error(f"WeatherAPI forecast error for {lat},{lon}: {e}")
            return None

    def get_openweather_forecast(self, lat: float, lon: float, station_id: str) -> Optional[Dict]:
        """
        Fetch OpenWeatherMap forecast.

        Args:
            lat: Latitude
            lon: Longitude
            station_id: Corresponding NWS station

        Returns:
            Standardized forecast dict or None
        """
        if not self.openweather_key:
            return None

        try:
            # Use One Call API 3.0 for daily forecast
            url = "https://api.openweathermap.org/data/3.0/onecall"
            params = {
                "lat": lat,
                "lon": lon,
                "exclude": "minutely,hourly,alerts",
                "units": "imperial",  # Fahrenheit
                "appid": self.openweather_key
            }

            res = requests.get(url, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            # Today's forecast
            today = data["daily"][0]
            temp_high = today["temp"]["max"]
            temp_low = today["temp"]["min"]

            return {
                "source": "OpenWeather",
                "station_id": station_id,
                "forecast_date": datetime.now().strftime("%Y-%m-%d"),
                "forecast_time": datetime.now().isoformat(),
                "temperature_high": float(temp_high),
                "temperature_low": float(temp_low),
                "confidence": 0.80
            }

        except Exception as e:
            logger.error(f"OpenWeather forecast error for {lat},{lon}: {e}")
            return None

    def get_nws_actual(self, station_id: str, date: str) -> Optional[Dict]:
        """
        Fetch NWS actual observed temperatures for a given date.
        Uses the Daily Climate Report (CLI) from the station.

        Args:
            station_id: NWS station identifier (e.g., "KNYC")
            date: Date string in YYYY-MM-DD format

        Returns:
            Dict with actual high/low or None
        """
        try:
            # NWS Observations API
            # Get observations for the entire day
            url = f"{self.nws_base}/stations/{station_id}/observations"

            # Parse date and construct time range
            target_date = datetime.strptime(date, "%Y-%m-%d")
            start = target_date.replace(hour=0, minute=0, second=0)
            end = target_date.replace(hour=23, minute=59, second=59)

            params = {
                "start": start.isoformat() + "Z",
                "end": end.isoformat() + "Z"
            }

            res = requests.get(url, headers=self.nws_headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json()

            observations = data.get("features", [])

            if not observations:
                logger.warning(f"No observations found for {station_id} on {date}")
                return None

            # Extract all temperatures and find min/max
            temps = []
            for obs in observations:
                temp_c = obs["properties"].get("temperature", {}).get("value")
                if temp_c is not None:
                    temp_f = (temp_c * 9/5) + 32
                    temps.append(temp_f)

            if not temps:
                logger.warning(f"No valid temperatures for {station_id} on {date}")
                return None

            return {
                "source": "NWS_ACTUAL",
                "station_id": station_id,
                "date": date,
                "temperature_high": max(temps),
                "temperature_low": min(temps),
                "observation_count": len(temps)
            }

        except Exception as e:
            logger.error(f"NWS actual temperature error for {station_id} on {date}: {e}")
            return None

    def get_all_forecasts(self, station_info: Dict) -> List[Dict]:
        """
        Fetch forecasts from all available sources for a station.

        Args:
            station_info: Dict containing station details (nws_station, lat, lon, accuweather_key)

        Returns:
            List of forecast dicts (may be empty if all sources fail)
        """
        forecasts = []

        # NWS (always attempt - it's the resolver)
        nws = self.get_nws_forecast(
            station_info["nws_station"],
            station_info["lat"],
            station_info["lon"]
        )
        if nws:
            forecasts.append(nws)

        # AccuWeather
        if station_info.get("accuweather_key"):
            accuweather = self.get_accuweather_forecast(
                station_info["accuweather_key"],
                station_info["nws_station"]
            )
            if accuweather:
                forecasts.append(accuweather)

        # WeatherAPI
        weatherapi = self.get_weatherapi_forecast(
            station_info["lat"],
            station_info["lon"],
            station_info["nws_station"]
        )
        if weatherapi:
            forecasts.append(weatherapi)

        # OpenWeather
        openweather = self.get_openweather_forecast(
            station_info["lat"],
            station_info["lon"],
            station_info["nws_station"]
        )
        if openweather:
            forecasts.append(openweather)

        logger.info(f"Fetched {len(forecasts)} forecasts for {station_info['nws_station']}")
        return forecasts
