#!/bin/bash
# Restart Telegram Agent with Weather Updates

set -e

echo "=========================================="
echo "Restarting Telegram Agent with Weather Updates"
echo "=========================================="
echo ""

# Stop agent
echo "Stopping agent..."
docker-compose stop agent

# Rebuild agent
echo "Rebuilding agent with weather commands..."
docker-compose build agent

# Start agent
echo "Starting agent..."
docker-compose up -d agent

# Wait for startup
echo "Waiting for agent to start..."
sleep 3

# Check status
echo ""
echo "Agent status:"
docker-compose ps agent

echo ""
echo "Recent logs:"
docker-compose logs --tail=20 agent

echo ""
echo "=========================================="
echo "Agent restarted successfully!"
echo "=========================================="
echo ""
echo "New Telegram commands available:"
echo "  /weather - View weather predictions"
echo "  /bias    - View forecast bias stats"
echo "  /markets - View active weather markets"
echo ""
echo "Test with /help to see updated command list"
echo ""
