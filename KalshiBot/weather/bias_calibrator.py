#!/usr/bin/env python3
"""
Bias Calibrator Service - Daily forecast bias updates

Schedule:
- 6:00 AM: Fetch yesterday's actuals and update biases
- 12:00 PM: Archive today's forecasts for future comparison
"""

import os
import sys
import json
import time
import logging
import schedule
from pathlib import Path
from datetime import datetime, timedelta

from dotenv import load_dotenv

# Import weather modules
sys.path.append("/app/weather")
from weather_api_client import WeatherAPIClient
from bias_calculator import BiasCalculator
from station_mapper import get_all_stations

load_dotenv()

LOG_PATH = Path("/app/logs/bias_calibrator.log")
FORECAST_ARCHIVE_DIR = Path("/app/config/forecast_archive")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s [BIAS_CALIBRATOR] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH) if LOG_PATH.parent.exists() else logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BiasCalibrator:
    """Daily bias calibration service."""

    def __init__(self):
        self.api_client = WeatherAPIClient()
        self.bias_calc = BiasCalculator()
        self.archive_dir = FORECAST_ARCHIVE_DIR

        # Ensure archive directory exists
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        logger.info("BiasCalibrator initialized")

    def run_daily_calibration(self):
        """
        Daily calibration task.
        Fetches yesterday's actuals and updates biases.
        """
        logger.info("=" * 60)
        logger.info("Starting daily bias calibration...")

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        stations = get_all_stations()

        calibrated_count = 0
        error_count = 0

        for series, station_info in stations.items():
            station_id = station_info["nws_station"]

            try:
                # Fetch NWS actuals
                actuals = self.api_client.get_nws_actual(station_id, yesterday)

                if not actuals:
                    logger.warning(f"No actuals for {station_id} on {yesterday}")
                    error_count += 1
                    continue

                # Load archived forecasts
                archived = self._load_archived_forecasts(yesterday)

                if not archived or station_id not in archived:
                    logger.warning(f"No archived forecasts for {station_id} on {yesterday}")
                    error_count += 1
                    continue

                # Update biases for each source
                for source, forecast_data in archived[station_id].items():
                    if source == "NWS_ACTUAL":
                        continue  # Skip actual data

                    # High temperature
                    if forecast_data.get("high") and actuals.get("temperature_high"):
                        self.bias_calc.record_forecast_error(
                            station_id,
                            source,
                            "high",
                            forecast_data["high"],
                            actuals["temperature_high"],
                            yesterday
                        )

                    # Low temperature
                    if forecast_data.get("low") and actuals.get("temperature_low"):
                        self.bias_calc.record_forecast_error(
                            station_id,
                            source,
                            "low",
                            forecast_data["low"],
                            actuals["temperature_low"],
                            yesterday
                        )

                calibrated_count += 1
                logger.info(f"✓ Calibrated {station_id}")

            except Exception as e:
                logger.error(f"Error calibrating {station_id}: {e}")
                error_count += 1
                continue

        logger.info(f"Daily calibration complete: {calibrated_count} stations, {error_count} errors")
        logger.info("=" * 60)

    def archive_todays_forecasts(self):
        """
        Archive today's forecasts for future comparison.
        Runs at noon daily.
        """
        logger.info("=" * 60)
        logger.info("Archiving today's forecasts...")

        today = datetime.now().strftime("%Y-%m-%d")
        stations = get_all_stations()

        archive_data = {}
        archived_count = 0

        for series, station_info in stations.items():
            station_id = station_info["nws_station"]

            # Skip if already archived today (avoid duplicates)
            if station_id in archive_data:
                continue

            try:
                # Fetch all current forecasts
                forecasts = self.api_client.get_all_forecasts(station_info)

                if not forecasts:
                    logger.warning(f"No forecasts available for {station_id}")
                    continue

                # Store by source
                archive_data[station_id] = {}

                for forecast in forecasts:
                    source = forecast["source"]
                    archive_data[station_id][source] = {
                        "high": forecast.get("temperature_high"),
                        "low": forecast.get("temperature_low"),
                        "forecast_time": forecast["forecast_time"]
                    }

                archived_count += 1
                logger.info(f"✓ Archived {station_id} ({len(forecasts)} sources)")

            except Exception as e:
                logger.error(f"Error archiving {station_id}: {e}")
                continue

        # Save to archive file
        archive_file = self.archive_dir / f"{today}.json"
        try:
            with open(archive_file, 'w') as f:
                json.dump({
                    "date": today,
                    "archived_at": datetime.now().isoformat(),
                    "stations": archive_data
                }, f, indent=2)

            logger.info(f"Forecast archive saved: {archive_file}")
            logger.info(f"Archived {archived_count} stations")

        except Exception as e:
            logger.error(f"Error saving archive: {e}")

        logger.info("=" * 60)

    def _load_archived_forecasts(self, date: str) -> dict:
        """
        Load archived forecasts for a given date.

        Args:
            date: Date string (YYYY-MM-DD)

        Returns:
            Dict of archived forecasts or empty dict
        """
        archive_file = self.archive_dir / f"{date}.json"

        if not archive_file.exists():
            logger.debug(f"No archive file for {date}")
            return {}

        try:
            with open(archive_file, 'r') as f:
                data = json.load(f)
                return data.get("stations", {})

        except Exception as e:
            logger.error(f"Error loading archive for {date}: {e}")
            return {}

    def run_scheduler(self):
        """Run scheduled tasks."""
        logger.info("Starting bias calibrator scheduler...")
        logger.info("Schedule:")
        logger.info("  - 06:00: Daily bias calibration")
        logger.info("  - 12:00: Archive forecasts")

        # Schedule tasks
        schedule.every().day.at("06:00").do(self.run_daily_calibration)
        schedule.every().day.at("12:00").do(self.archive_todays_forecasts)

        # Run archive immediately on startup (if not already done today)
        today = datetime.now().strftime("%Y-%m-%d")
        archive_file = self.archive_dir / f"{today}.json"
        if not archive_file.exists():
            logger.info("No archive for today, running archive task now...")
            self.archive_todays_forecasts()

        # Main loop
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)  # Check every minute

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(60)


def main():
    """Main entry point."""
    logger.info("📊 Bias Calibrator Service starting...")

    calibrator = BiasCalibrator()
    calibrator.run_scheduler()


if __name__ == "__main__":
    main()
