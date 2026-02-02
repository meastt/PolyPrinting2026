# Operations Guide: Running Your Trading Bot

This guide explains what happens when you start the bot, how to monitor it, and how to get alerts.

---

## 🚀 What Happens When You Start the Bot

### Startup Sequence

When you run `python main.py --exchange kalshi`, here's what happens:

```
┌─────────────────────────────────────────────────────────────┐
│  1. INITIALIZATION (5-10 seconds)                           │
├─────────────────────────────────────────────────────────────┤
│  • Load configuration from config/config.yaml               │
│  • Initialize Kalshi API client                             │
│  • Authenticate with your API credentials                   │
│  • Connect to price feeds (Binance WebSocket)               │
│  • Initialize risk manager with your starting balance       │
│  • Register enabled strategies                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  2. HEALTH CHECK                                            │
├─────────────────────────────────────────────────────────────┤
│  • Verify Kalshi API connection                             │
│  • Fetch current account balance                            │
│  • Test price feed connections                              │
│  • If any fail → Error logged, bot may pause                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  3. MAIN TRADING LOOP (runs every 2 seconds)                │
├─────────────────────────────────────────────────────────────┤
│  │                                                          │
│  ├─→ Fetch crypto markets from Kalshi                       │
│  │   (hourly BTC/ETH price prediction markets)              │
│  │                                                          │
│  ├─→ Update positions & check for resolved markets          │
│  │                                                          │
│  ├─→ Run each enabled strategy:                             │
│  │   • Crypto TA: Analyze RSI/MACD/VWAP signals             │
│  │   • Spike Reversion: Detect >3% price moves              │
│  │   • Arbitrage: Find YES+NO < $1 opportunities            │
│  │   • Market Maker: Post limit orders for spread           │
│  │                                                          │
│  ├─→ Filter signals through risk manager                    │
│  │   • Check position limits                                │
│  │   • Verify daily drawdown OK                             │
│  │   • Apply EV threshold (>2% expected value)              │
│  │                                                          │
│  ├─→ Execute approved trades                                │
│  │   • Place limit orders (zero fees!)                      │
│  │   • Log trade details                                    │
│  │                                                          │
│  └─→ Sleep for poll_interval (default: 2 seconds)           │
│                                                             │
│  [Repeats 24/7 until stopped]                               │
└─────────────────────────────────────────────────────────────┘
```

### Console Output Example

When running, you'll see output like this:

```
============================================================
STARTING KALSHI TRADING BOT
US-Legal CFTC-Regulated Trading
============================================================
Mode: SIMULATION
Starting balance: $50.00
Strategies: ['kalshi_crypto_ta', 'kalshi_spike_reversion']
============================================================

2026-02-02 10:00:01 INFO  KalshiClient initialized (env=demo, auth=rsa)
2026-02-02 10:00:02 INFO  WebSocket price feeds started
2026-02-02 10:00:02 INFO  Found 12 BTC hourly markets
2026-02-02 10:00:02 INFO  Found 8 ETH hourly markets

2026-02-02 10:00:05 INFO  TA signal: BTC up (prob=0.62, edge=7.2%, regime=range)
2026-02-02 10:00:05 INFO  Trade executed: YES 3x @ 0.55 on BTCUSD-26FEB02-B101500

----------------------------------------
KALSHI BOT STATUS
Uptime: 0.1 hours
Iterations: 30
Trades: 1 (maker: 1)
Balance: $50.00
Daily P&L: $0.00 (0.0%)
Open positions: 1
Win rate: 0.0%
Fees paid: $0.00
----------------------------------------
```

---

## 📊 How to Monitor the Bot

### Option 1: Console Logs (Simplest)

Just watch the terminal! The bot logs:
- Every trade executed
- Status updates every ~60 seconds
- Errors and warnings
- Strategy signals (in DEBUG mode)

**Run with more detail:**
```bash
# Edit config.yaml
log_level: "DEBUG"  # Shows all signals, even rejected ones
```

### Option 2: Log Files

Logs are saved to `logs/` directory:

```
logs/
├── trading.log      # Main activity log
├── trades.csv       # All trades in CSV format
├── daily_pnl.csv    # Daily profit/loss summary
└── errors.log       # Errors only
```

**View live logs:**
```bash
# Follow main log
tail -f logs/trading.log

# Watch for trades only
tail -f logs/trading.log | grep "Trade executed"

# Watch for errors
tail -f logs/errors.log
```

### Option 3: Prometheus Metrics (Advanced)

The bot exposes metrics on port 9090:

```bash
# Check if metrics are running
curl http://localhost:9090/metrics
```

**Key metrics:**
```
# Current balance
trading_balance_usd{exchange="kalshi"} 52.35

# Trades executed
trading_trades_total{strategy="kalshi_crypto_ta"} 15

# Win rate
trading_win_rate{exchange="kalshi"} 0.67

# Daily P&L
trading_daily_pnl_usd{exchange="kalshi"} 2.35

# Open positions
trading_open_positions{exchange="kalshi"} 2
```

**Connect to Grafana for dashboards** (optional, see README).

---

## 🔔 How to Get Alerts

### Option 1: Discord Webhook (Recommended)

Add to your `config/config.yaml`:

```yaml
alerts:
  discord:
    enabled: true
    webhook_url: "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"

    # What to alert on:
    events:
      - trade_executed      # Every trade
      - daily_summary       # End of day report
      - error              # Errors
      - drawdown_warning   # Approaching loss limit
      - big_win            # Profit > $5
```

**To get a Discord webhook:**
1. Go to your Discord server
2. Edit a channel → Integrations → Webhooks
3. Create webhook and copy URL

### Option 2: Telegram Bot

```yaml
alerts:
  telegram:
    enabled: true
    bot_token: "YOUR_BOT_TOKEN"
    chat_id: "YOUR_CHAT_ID"

    events:
      - trade_executed
      - daily_summary
      - error
```

**To set up Telegram:**
1. Message @BotFather on Telegram
2. Create new bot, get token
3. Message your bot, then get chat_id from:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`

### Option 3: Email Alerts

```yaml
alerts:
  email:
    enabled: true
    smtp_server: "smtp.gmail.com"
    smtp_port: 587
    username: "your@gmail.com"
    password: "app_password"  # Use Gmail App Password
    to_address: "your@email.com"

    events:
      - daily_summary
      - error
      - emergency_stop
```

### Option 4: Simple Script Alert (DIY)

Create a monitoring script `monitor.sh`:

```bash
#!/bin/bash
# Run every 5 minutes via cron

LOG_FILE="/home/user/PolyPrinting2026/logs/trading.log"
WEBHOOK="https://discord.com/api/webhooks/YOUR_URL"

# Check if bot is running
if ! pgrep -f "main.py" > /dev/null; then
    curl -H "Content-Type: application/json" \
         -d '{"content":"⚠️ Trading bot is NOT running!"}' \
         $WEBHOOK
fi

# Check for errors in last 5 minutes
ERRORS=$(grep "ERROR\|CRITICAL" $LOG_FILE | tail -5)
if [ ! -z "$ERRORS" ]; then
    curl -H "Content-Type: application/json" \
         -d "{\"content\":\"🚨 Bot Errors:\\n\`\`\`$ERRORS\`\`\`\"}" \
         $WEBHOOK
fi

# Get latest balance
BALANCE=$(grep "Balance:" $LOG_FILE | tail -1)
echo "Current: $BALANCE"
```

Add to crontab:
```bash
crontab -e
# Add line:
*/5 * * * * /home/user/PolyPrinting2026/monitor.sh
```

---

## 📈 Key Things to Watch

### Daily Checklist

| Check | What to Look For | Action |
|-------|------------------|--------|
| **Balance** | Should be stable or growing | If dropping fast, check errors |
| **Win Rate** | Target: >55% | Below 50% = review strategy |
| **Open Positions** | Usually 0-5 | >10 = something's stuck |
| **Fees Paid** | Should be ~$0 | >$0 = taker orders happening |
| **Errors** | Should be rare | Many errors = investigate |

### Warning Signs

🟡 **Yellow Flags:**
- Win rate dropping below 50%
- More taker orders than maker orders
- Balance not changing for hours
- Many "Signal rejected" messages

🔴 **Red Flags:**
- "EMERGENCY STOP" in logs
- Balance dropping >5% in a day
- "Failed to connect" errors
- No trades for 24+ hours (in active markets)

---

## 🛑 Emergency Controls

### Pause Trading
```bash
# Method 1: Send signal
kill -SIGUSR1 $(pgrep -f "main.py")

# Method 2: Create pause file
touch /home/user/PolyPrinting2026/.pause
```

### Stop Bot Gracefully
```bash
# Ctrl+C in terminal, or:
kill -SIGTERM $(pgrep -f "main.py")

# Bot will:
# 1. Cancel all open orders
# 2. Save position state
# 3. Log final summary
# 4. Exit cleanly
```

### Force Stop (Emergency)
```bash
kill -9 $(pgrep -f "main.py")
# Warning: May leave orders open on exchange!
```

### Cancel All Orders Manually
```python
# Quick script to cancel everything
from src.api import KalshiClient
client = KalshiClient()
orders = client.get_open_orders()
for order in orders:
    client.cancel_order(order.order_id)
    print(f"Cancelled: {order.order_id}")
```

---

## 📱 Quick Status Check Commands

```bash
# Is bot running?
pgrep -f "main.py" && echo "✅ Running" || echo "❌ Stopped"

# Current balance (from logs)
grep "Balance:" logs/trading.log | tail -1

# Today's trades
grep "Trade executed" logs/trading.log | grep "$(date +%Y-%m-%d)" | wc -l

# Recent errors
grep "ERROR\|CRITICAL" logs/trading.log | tail -5

# Win/loss count today
grep "Position closed" logs/trading.log | grep "$(date +%Y-%m-%d)"
```

---

## 📊 Understanding the Status Output

Every ~60 seconds, you'll see:

```
----------------------------------------
KALSHI BOT STATUS
Uptime: 2.5 hours              ← How long bot has been running
Iterations: 4500               ← Main loop cycles (every 2s)
Trades: 12 (maker: 11)         ← Total trades, maker=no fees!
Balance: $53.25                ← Current account balance
Daily P&L: $3.25 (6.5%)        ← Today's profit/loss
Open positions: 2              ← Active bets waiting to resolve
Win rate: 66.7%                ← Historical win percentage
Fees paid: $0.15               ← Should be near $0!
----------------------------------------
```

**Good signs:**
- maker count ≈ total trades (zero fees)
- Positive daily P&L
- Win rate > 55%
- Fees near $0

---

## 🔄 Typical Trading Day

| Time | What Happens |
|------|--------------|
| **Morning** | Bot scans hourly markets, looks for signals |
| **When spike detected** | Places reversion bet if TA confirms |
| **When TA aligns** | Places directional bet on crypto price |
| **Market expires** | Position resolves, P&L recorded |
| **End of day** | Daily summary logged |

Most activity happens during:
- Crypto volatility (market opens, news events)
- Near hourly market expiries
- When RSI hits extremes (<30 or >70)

Quiet periods are normal - the bot waits for high-confidence opportunities!

---

## Need Help?

- Check `logs/errors.log` for issues
- Join [Kalshi Discord](https://discord.gg/kalshi) #dev channel
- Review strategy stats: `grep "get_stats" logs/trading.log`
