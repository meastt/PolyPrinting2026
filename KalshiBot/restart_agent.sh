#!/bin/bash
# Quick script to restart the agent container with updated code

set -e

echo "🔄 Restarting Agent with updated code..."

# Option 1: Rebuild and restart just the agent
docker compose up -d --build agent

echo "✅ Agent restarted!"
echo ""
echo "📊 Checking status..."
docker ps | grep kalshi-agent

echo ""
echo "📝 Viewing recent logs..."
docker logs --tail 20 kalshi-agent

echo ""
echo "✅ Done! Try /logs in Telegram again."
