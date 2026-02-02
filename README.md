# PolyPrinting2026 - Automated Polymarket Trading Bot

An automated trading bot for Polymarket prediction markets, designed to run 24/7 on Oracle Cloud Infrastructure (OCI). Implements low-risk, high-potential strategies optimized for small capital ($50 USDC start) with compounding micro-trades.

## ⚠️ Important Disclaimers

- **Risk Warning**: Trading prediction markets involves significant risk. You can lose your entire investment.
- **Legal Compliance**: Ensure Polymarket is legal in your jurisdiction. Review [Polymarket Terms of Service](https://polymarket.com/tos).
- **Fee Awareness**: Post-2026 fee updates may affect profitability. This bot prioritizes maker orders (rebates) over taker orders (3% fees).
- **No Guarantees**: Past performance does not guarantee future results. Start in simulation mode.
- **API Limits**: Respect Polymarket rate limits. Excessive requests may result in bans.

## 🎯 Strategies Overview

| Strategy | Risk Level | Description | Fee Impact |
|----------|-----------|-------------|------------|
| YES/NO Arbitrage | Very Low | Exploit pricing inefficiencies where YES + NO < $0.99 | Risk-free when sum < $1 |
| Maker Market Making | Low | Earn rebates by providing liquidity | Up to 100% rebate (early phase) |
| Volatility Spike Reversion | Medium | Bet on mean reversion after sharp moves | Maker orders preferred |
| Copy Trading | Medium | Mirror top traders' positions | Filter for +EV only |

## 📋 Table of Contents

1. [Oracle Cloud Setup](#oracle-cloud-setup)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Running the Bot](#running-the-bot)
5. [Monitoring](#monitoring)
6. [Backtesting](#backtesting)
7. [Architecture](#architecture)
8. [Troubleshooting](#troubleshooting)

---

## Oracle Cloud Setup

### Step 1: Create OCI Account (Free Tier)

1. Go to [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)
2. Click "Start for free" and create an account
3. Verify your email and complete identity verification
4. You'll receive $300 in credits + Always Free resources

### Step 2: Provision Compute Instance

1. Log into [OCI Console](https://cloud.oracle.com/)
2. Navigate to **Compute → Instances → Create Instance**

3. Configure the instance:
   ```
   Name: polymarket-bot
   Compartment: (your root compartment)
   Availability Domain: Choose any available

   Image: Canonical Ubuntu 22.04 (Always Free eligible)
   Shape: VM.Standard.A1.Flex (Ampere ARM - Always Free)
     - OCPUs: 1 (can use up to 4 free)
     - Memory: 6 GB (can use up to 24 GB free)

   Networking:
     - Create new VCN or use existing
     - Assign public IPv4 address: Yes

   SSH Keys:
     - Generate or paste your public key
     - SAVE the private key if generating!
   ```

4. Click **Create** and wait for instance to be RUNNING

### Step 3: Configure Security Rules

1. Go to **Networking → Virtual Cloud Networks**
2. Click your VCN → Security Lists → Default Security List
3. Add Ingress Rule (optional, for Prometheus):
   ```
   Source CIDR: 0.0.0.0/0 (or your IP for security)
   Protocol: TCP
   Destination Port: 9090
   ```

### Step 4: Connect to Instance

```bash
# Get public IP from OCI Console
ssh -i /path/to/private-key ubuntu@<PUBLIC_IP>

# Update system
sudo apt update && sudo apt upgrade -y
```

### Step 5: Install Dependencies

```bash
# Install Python 3.10+ and essentials
sudo apt install -y python3.10 python3.10-venv python3-pip git tmux htop

# Create project directory
mkdir -p ~/polymarket-bot
cd ~/polymarket-bot

# Clone the repository (or upload files)
git clone https://github.com/YOUR_USERNAME/PolyPrinting2026.git .
# OR use scp to upload files

# Create virtual environment
python3.10 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 6: Configure Environment Variables

```bash
# Create environment file (NEVER commit this!)
nano ~/.polymarket_env

# Add these variables:
export POLYMARKET_API_KEY="your_api_key_here"
export POLYMARKET_API_SECRET="your_api_secret_here"
export POLYMARKET_API_PASSPHRASE="your_passphrase_here"
export POLYMARKET_PRIVATE_KEY="your_wallet_private_key"
export POLYMARKET_FUNDER="your_wallet_address"

# Optional: For enhanced price feeds
export BINANCE_API_KEY="optional_binance_key"
export COINBASE_API_KEY="optional_coinbase_key"

# Load environment
chmod 600 ~/.polymarket_env
echo "source ~/.polymarket_env" >> ~/.bashrc
source ~/.bashrc
```

---

## Installation

### Local Development

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/PolyPrinting2026.git
cd PolyPrinting2026

# Create virtual environment
python3.10 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy example config
cp config/config.example.yaml config/config.yaml

# Edit configuration
nano config/config.yaml
```

### Getting Polymarket API Keys

1. Go to [Polymarket](https://polymarket.com) and connect your wallet
2. Navigate to Settings → API Keys
3. Create new API key with required permissions:
   - Read markets
   - Place orders
   - Cancel orders
   - View positions
4. Store credentials securely (see environment setup above)

**References:**
- [py-clob-client Documentation](https://github.com/Polymarket/py-clob-client)
- [Polymarket API Docs](https://docs.polymarket.com/)

---

## Configuration

Edit `config/config.yaml` to customize bot behavior:

```yaml
# Core settings
general:
  mode: "simulation"  # "simulation" or "live" - START WITH SIMULATION!
  starting_balance: 50.0  # USDC
  poll_interval_seconds: 2  # Main loop interval
  log_level: "INFO"

# Strategy toggles (enable/disable each)
strategies:
  arbitrage:
    enabled: true
    min_spread: 0.01  # Minimum 1% inefficiency
    max_position_size: 5.0  # Max $5 per arb

  market_making:
    enabled: true
    spread_offset: 0.02  # 2% from fair value
    order_size: 2.0  # $2 per side

  spike_reversion:
    enabled: true
    threshold_percent: 3.0  # 3% move triggers
    lookback_seconds: 60

  copy_trading:
    enabled: false  # Requires Gamma API access
    min_edge: 0.04  # 4% EV threshold

# Risk management
risk:
  max_position_percent: 2.0  # Max 2% per trade
  daily_drawdown_limit: 0.05  # 5% daily loss limit
  max_open_positions: 10
```

---

## Running the Bot

### Option 1: Direct Execution (Development)

```bash
cd ~/polymarket-bot
source venv/bin/activate
python -m src.main
```

### Option 2: Using tmux (Persistent Session)

```bash
# Start tmux session
tmux new -s polybot

# Run bot
cd ~/polymarket-bot
source venv/bin/activate
python -m src.main

# Detach: Ctrl+B, then D
# Reattach: tmux attach -t polybot
```

### Option 3: systemd Service (Production - Recommended)

```bash
# Copy service file
sudo cp scripts/polybot.service /etc/systemd/system/

# Edit paths if needed
sudo nano /etc/systemd/system/polybot.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable polybot
sudo systemctl start polybot

# Check status
sudo systemctl status polybot

# View logs
sudo journalctl -u polybot -f
```

---

## Monitoring

### Log Files

```bash
# Trade logs (CSV format)
tail -f logs/trades.csv

# Application logs
tail -f logs/polybot.log

# Daily P&L summary
cat logs/daily_pnl.csv
```

### Prometheus Metrics (Optional)

The bot exposes metrics on port 9090:

```bash
# Install Prometheus (optional)
sudo apt install prometheus

# Access metrics
curl http://localhost:9090/metrics
```

Key metrics:
- `polybot_balance_usdc` - Current wallet balance
- `polybot_trades_total` - Total trades executed
- `polybot_pnl_total` - Cumulative P&L
- `polybot_active_positions` - Open positions count

### Health Checks

```bash
# Check if bot is running
pgrep -f "python -m src.main"

# Check systemd status
sudo systemctl status polybot

# Monitor resource usage
htop
```

---

## Backtesting

Run backtests before going live:

```bash
# Run 30-day backtest with $50 starting balance
python -m src.backtest.backtester --days 30 --balance 50

# Backtest specific strategy
python -m src.backtest.backtester --strategy arbitrage --days 30

# Output includes:
# - Total return %
# - Sharpe ratio
# - Max drawdown
# - Win rate
# - Trade-by-trade log
```

---

## Architecture

```
PolyPrinting2026/
├── src/
│   ├── main.py              # Entry point
│   ├── api/
│   │   ├── polymarket_client.py  # Polymarket CLOB wrapper
│   │   ├── price_feeds.py        # CCXT multi-exchange prices
│   │   └── gamma_api.py          # Leaderboard/copy trading
│   ├── strategies/
│   │   ├── base_strategy.py      # Strategy interface
│   │   ├── arbitrage.py          # YES/NO arb scanner
│   │   ├── market_maker.py       # Maker rebate strategy
│   │   ├── spike_reversion.py    # Volatility detector
│   │   └── copy_trader.py        # Top trader mirroring
│   ├── core/
│   │   ├── trading_loop.py       # Main event loop
│   │   ├── risk_manager.py       # Position/drawdown limits
│   │   ├── position_manager.py   # Track open positions
│   │   └── order_manager.py      # Order lifecycle
│   ├── utils/
│   │   ├── logger.py             # Structured logging
│   │   ├── metrics.py            # Prometheus exporter
│   │   └── helpers.py            # Utility functions
│   └── backtest/
│       ├── backtester.py         # Simulation engine
│       └── data_loader.py        # Historical data
├── config/
│   └── config.yaml           # Bot configuration
├── scripts/
│   ├── setup_oci.sh          # OCI automation
│   └── polybot.service       # systemd unit
├── logs/                     # Trade logs, P&L
└── data/                     # Market data cache
```

---

## Troubleshooting

### Common Issues

**1. API Authentication Failed**
```bash
# Verify environment variables are set
echo $POLYMARKET_API_KEY
# Should not be empty

# Re-source environment
source ~/.polymarket_env
```

**2. Rate Limiting**
```
Error: 429 Too Many Requests
```
- Increase `poll_interval_seconds` in config
- The bot auto-retries with exponential backoff

**3. Insufficient Balance**
```
Error: Insufficient funds for order
```
- Check wallet balance on Polymarket
- Reduce `max_position_size` in config
- Ensure USDC is deposited (not MATIC)

**4. Connection Issues**
```bash
# Test connectivity
curl https://clob.polymarket.com/health

# Check DNS
nslookup clob.polymarket.com
```

**5. Bot Stops Unexpectedly**
```bash
# Check systemd logs
sudo journalctl -u polybot -n 100

# Check application logs
tail -100 logs/polybot.log
```

### Recovery Procedures

```bash
# Restart bot
sudo systemctl restart polybot

# Cancel all open orders (emergency)
python -m src.utils.cancel_all_orders

# Export trade history
python -m src.utils.export_trades --output trades_backup.csv
```

---

## Strategy Details

### YES/NO Arbitrage
*Inspired by Moltbot community tactics turning small stakes into significant profits*

Scans short-term binary markets (15-min BTC/ETH up/down) for pricing inefficiencies:
- If YES price + NO price < $0.99 (accounting for fees), there's risk-free profit
- Places equal-sized maker limit orders on both sides
- Holds until market resolution
- Example: YES at $0.48, NO at $0.50 = $0.98 total → $0.02 guaranteed profit per $1

### Maker Market Making
*Earn rebates by providing liquidity*

- Posts resting limit orders at offsets from fair value
- Earns maker rebates (up to 100% in promotional periods)
- Avoids taking liquidity (3% taker fee)
- Tracks daily rebate earnings

### Volatility Spike Reversion
*Bet on mean reversion after sharp price moves*

- Monitors spot crypto prices every 1-2 seconds
- Detects >3-5% moves within 1 minute
- Places maker bets on reversion in Polymarket binary markets
- Sizes at 1-2% of balance ($0.50-$1 starting)

### Selective Copy Trading
*Mirror successful traders (when alpha-positive)*

- Monitors top 5-10 Polymarket traders via leaderboard
- Mirrors positions proportionally (0.1% of their size, min $1)
- Only copies when strategy sees +EV edge >4%
- Filters for crypto-related markets only

---

## Expected Returns

**Conservative Targets (with proper risk management):**

| Timeframe | Target Return | Strategy Mix |
|-----------|---------------|--------------|
| Daily | 0.3-1% | Arb + MM |
| Weekly | 2-5% | All strategies |
| Monthly | 8-20% | Compounding |
| Annual | 100-300% | Full automation |

*These are targets, not guarantees. Actual results depend on market conditions.*

---

## Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/new-strategy`
3. Commit changes: `git commit -am 'Add new strategy'`
4. Push to branch: `git push origin feature/new-strategy`
5. Submit Pull Request

---

## License

MIT License - See LICENSE file for details.

---

## Resources

- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)
- [Polymarket API Documentation](https://docs.polymarket.com/)
- [CCXT Exchange Library](https://github.com/ccxt/ccxt)
- [Polymarket Community Discord](https://discord.gg/polymarket)

---

*Built with insights from the Polymarket trading community. Not financial advice.*
