# KalshiBot - Split-Brain Trading System

Dockerized trading bot for Kalshi prediction markets with AI-enhanced decision making.

## Architecture

- **Agent (Slow Brain)**: Telegram commands, Groq/Llama sentiment, news analysis
- **Trader (Fast Brain)**: 10ms execution loop, Coinbase prices, Kalshi orders
- **Heartbeat**: Oracle anti-reclamation service

## Quick Start

```bash
# 1. Copy environment template
cp .env.example .env

# 2. Edit with your API keys
nano .env

# 3. Add your Kalshi private key
mkdir -p keys
cp /path/to/your/kalshi_private_key.pem keys/

# 4. Start all services
docker compose up -d

# 5. Check logs
docker compose logs -f
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/status` | Current P&L and settings |
| `/defensive` | Wide spreads, min size |
| `/aggressive` | Tight spreads, max size |
| `/analyze` | Run sentiment analysis |
| `/kill` | Emergency stop |

## File Structure

```
KalshiBot/
├── docker-compose.yml   # Container orchestration
├── .env                 # API keys (DO NOT COMMIT)
├── config/
│   └── strategy.json    # Shared state between brains
├── agent/               # Slow Brain (AI + Telegram)
├── trader/              # Fast Brain (Execution)
└── heartbeat/           # Oracle keep-alive
```

## Safety Features

- **Toxic Flow Guard**: Cancels orders if BTC moves >$50/sec
- **Mode HALT**: `/kill` command stops all trading
- **Atomic Config**: No corrupted reads between containers
