#!/bin/bash
# Deploy Arbitrage Trader to Oracle Cloud Server

set -e

echo "🎰 Deploying Arbitrage Trader..."

# Build the arbitrage trader image
cd trader
docker build -f Dockerfile.arbitrage -t kalshi-arbitrage:latest .

# Stop and remove old arbitrage container if it exists
docker stop kalshi-arbitrage 2>/dev/null || true
docker rm kalshi-arbitrage 2>/dev/null || true

# Run the arbitrage trader
docker run -d \
  --name kalshi-arbitrage \
  --restart unless-stopped \
  --env-file ../.env \
  -v $(pwd)/../config:/app/config \
  -v $(pwd)/../logs:/app/logs \
  -v $(pwd)/../keys:/app/keys:ro \
  kalshi-arbitrage:latest

echo "✅ Arbitrage trader deployed!"
echo ""
echo "Check status with:"
echo "  docker logs kalshi-arbitrage"
echo ""
echo "Or use Telegram:"
echo "  /status - View combined bot status"
echo "  /logs - System diagnostics"
