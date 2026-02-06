#!/bin/bash
# Weather Trading System Deployment Script

set -e

echo "=========================================="
echo "KalshiBot Weather Trading System Deployment"
echo "=========================================="
echo ""

# Check environment file
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    exit 1
fi

# Check weather API keys
echo "Checking weather API keys..."
source .env

if [ -z "$ACCUWEATHER_API_KEY" ] || [ "$ACCUWEATHER_API_KEY" = "" ]; then
    echo "⚠️  Warning: ACCUWEATHER_API_KEY not set"
fi

if [ -z "$WEATHERAPI_KEY" ] || [ "$WEATHERAPI_KEY" = "" ]; then
    echo "⚠️  Warning: WEATHERAPI_KEY not set"
fi

if [ -z "$OPENWEATHER_KEY" ] || [ "$OPENWEATHER_KEY" = "" ]; then
    echo "⚠️  Warning: OPENWEATHER_KEY not set"
fi

# Check Kalshi credentials
if [ -z "$KALSHI_API_KEY_ID" ]; then
    echo "❌ Error: KALSHI_API_KEY_ID not set"
    exit 1
fi

if [ ! -f "keys/private_key.pem" ]; then
    echo "❌ Error: keys/private_key.pem not found"
    exit 1
fi

echo "✅ Environment configured"
echo ""

# Create necessary directories
echo "Creating directories..."
mkdir -p config/forecast_archive
mkdir -p logs
echo "✅ Directories ready"
echo ""

# Build containers
echo "Building weather containers..."
docker-compose build weather-strategist bias-calibrator
echo "✅ Containers built"
echo ""

# Start services
echo "Starting services..."
docker-compose up -d weather-strategist bias-calibrator
echo "✅ Services started"
echo ""

# Check status
echo "Checking service status..."
sleep 3
docker-compose ps
echo ""

# Show logs
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "Monitor with:"
echo "  docker-compose logs -f weather-strategist"
echo "  docker-compose logs -f bias-calibrator"
echo ""
echo "Telegram commands:"
echo "  /weather - View predictions"
echo "  /bias    - View bias stats"
echo "  /markets - View active markets"
echo ""
echo "Configuration files:"
echo "  config/weather_signals.json"
echo "  config/forecast_bias.json"
echo "  config/weather_state.json"
echo ""
echo "Note: System needs 30+ days to collect bias data for optimal performance."
echo ""
