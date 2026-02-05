#!/bin/bash
# Redeploy Agent (Telegram Bot) with updated code

set -e

echo "🧠 Redeploying Agent..."

# Build the agent image
cd agent
docker build -t kalshi-agent:latest .

# Stop and remove old container
docker stop kalshi-agent 2>/dev/null || true
docker rm kalshi-agent 2>/dev/null || true

# Run the agent
docker run -d \
  --name kalshi-agent \
  --restart unless-stopped \
  --env-file ../.env \
  -v $(pwd)/../config:/app/config \
  -v $(pwd)/../logs:/app/logs \
  kalshi-agent:latest

echo "✅ Agent redeployed!"
echo ""
echo "Check status with:"
echo "  docker logs kalshi-agent"
