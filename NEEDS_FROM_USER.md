# Things Needed From You

This document lists everything you'll need to provide or configure to run the PolyPrinting2026 trading bot.

---

## 🇺🇸 Option A: Kalshi (US Legal - Recommended for US Users)

### 1. Kalshi API Credentials

You need to obtain API credentials from Kalshi:

```bash
# Store in ~/.kalshi_env or environment variables
KALSHI_API_KEY_ID=<your_api_key_id>
KALSHI_PRIVATE_KEY_PATH=/path/to/private_key.pem
# OR inline PEM (useful for cloud deployment):
# KALSHI_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n..."
```

### How to Get These:
1. Go to [Kalshi.com](https://kalshi.com) and create an account
2. **Verify your identity** (required - US government ID)
3. Navigate to **Settings → API**
4. Click "Create new API key"
5. Download and save the **private key PEM file** securely
6. Store the **API Key ID** shown on screen

### Security Best Practices:
```bash
# Secure your private key
chmod 600 ~/.kalshi/private_key.pem

# Never commit keys to git!
echo "*.pem" >> .gitignore
echo "*_env" >> .gitignore
```

### Resources:
- [Kalshi API Documentation](https://docs.kalshi.com)
- [Kalshi Help Center](https://help.kalshi.com/kalshi-api)
- [Kalshi Discord](https://discord.gg/kalshi) - #dev channel for API support

---

## 🌍 Option B: Polymarket (Non-US Only)

> ⚠️ **WARNING**: Polymarket is NOT available in the USA. US users must use Kalshi.

### 1. Polymarket API Credentials (Required for Live Trading)

You need to obtain API credentials from Polymarket:

```
POLYMARKET_API_KEY=<your_api_key>
POLYMARKET_API_SECRET=<your_api_secret>
POLYMARKET_API_PASSPHRASE=<your_passphrase>
POLYMARKET_PRIVATE_KEY=<your_ethereum_private_key>
POLYMARKET_FUNDER=<your_wallet_address>
```

### How to Get These:
1. Go to [Polymarket](https://polymarket.com)
2. Connect your wallet
3. Navigate to Settings → API Keys
4. Create a new API key with permissions:
   - Read markets
   - Place orders
   - Cancel orders
   - View positions
5. For the private key: Export from your wallet (MetaMask, etc.)
   - **WARNING**: This gives full control of your wallet. Use a dedicated trading wallet!

### Resources:
- [py-clob-client Documentation](https://github.com/Polymarket/py-clob-client)
- [Polymarket API Docs](https://docs.polymarket.com/)

---

## 2. Oracle Cloud Account (For 24/7 Operation)

If you want to run the bot continuously on a cloud server:

1. Create an Oracle Cloud account at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/)
2. Verify your identity
3. You'll receive:
   - $300 in free credits (expires in 30 days)
   - Always Free tier resources (never expires)

### Recommended Instance:
- **Shape**: VM.Standard.A1.Flex (Ampere ARM - Always Free)
- **CPUs**: 1 OCPU
- **Memory**: 6 GB
- **Image**: Ubuntu 22.04

---

## 3. Funding Your Account

### For Kalshi (US):

Kalshi accepts multiple deposit methods:

1. **USDC** (Recommended): Fast, low fees via ZeroHash
2. **Bank Transfer (ACH)**: Free, 1-3 business days
3. **Debit Card**: Higher fees, instant
4. **Bitcoin/Solana**: Via ZeroHash partner

**Starting Capital**: The bot is designed for $50 minimum

To deposit:
1. Log into Kalshi
2. Click "Deposit" in top right
3. Choose your deposit method
4. Follow instructions

### For Polymarket (Non-US):

To trade, you need USDC deposited in your Polymarket wallet:

1. **Starting Capital**: The bot is designed for $50 minimum
2. **Deposit Process**:
   - Bridge USDC to Polygon network
   - Deposit to Polymarket via their interface
   - Ensure sufficient MATIC for gas fees (~$1-2 worth)

### Where to Get USDC:
- Major exchanges (Coinbase, Binance, Kraken)
- Bridge from Ethereum using [Polygon Bridge](https://wallet.polygon.technology/)

---

## 4. Optional: Exchange API Keys (For Better Price Feeds)

For more accurate price data, you can add exchange API keys:

```
BINANCE_API_KEY=<optional>
BINANCE_API_SECRET=<optional>
COINBASE_API_KEY=<optional>
COINBASE_API_SECRET=<optional>
```

**Note**: Read-only API keys are sufficient (no trading permissions needed).

---

## 5. Configuration Decisions

Review and customize `config/config.yaml` for your preferences:

### Key Decisions:
1. **Trading Mode**: Start with `simulation` before `live`
2. **Starting Balance**: Set your actual USDC balance
3. **Risk Parameters**:
   - `max_position_percent`: How much to risk per trade (default 2%)
   - `daily_drawdown_limit`: Stop loss for the day (default 5%)
4. **Strategy Selection**: Which strategies to enable

---

## 6. Verification Checklist

Before going live, verify:

- [ ] API credentials are set in `~/.polymarket_env`
- [ ] USDC deposited to Polymarket wallet
- [ ] MATIC available for gas fees
- [ ] Configuration reviewed and customized
- [ ] Backtest completed successfully
- [ ] Simulation mode tested for 24+ hours
- [ ] Understood risk warnings in README

---

## 7. Questions to Consider

1. **Risk Tolerance**: How much can you afford to lose? Never trade more than you can lose.

2. **Monitoring**: Will you check the bot regularly? Set up alerts if needed.

3. **Legal**: Is Polymarket available and legal in your jurisdiction?

4. **Tax**: Track your trades for tax reporting purposes.

---

## 8. API Details I Couldn't Verify

Some API details may need verification with current Polymarket documentation:

1. **Gamma API Endpoints**: The leaderboard API endpoints are placeholders. Check if Polymarket has public trader leaderboard APIs.

2. **Fee Structure**: The current fee structure (3% taker, 1% maker rebate) should be verified with Polymarket's current terms.

3. **Rate Limits**: Verify current API rate limits at [docs.polymarket.com](https://docs.polymarket.com/).

4. **Market Categories**: The category filtering for "Crypto" markets assumes certain naming conventions. Verify market categorization.

---

## 9. Support & Resources

- **py-clob-client Issues**: [GitHub Issues](https://github.com/Polymarket/py-clob-client/issues)
- **Polymarket Discord**: [Join Community](https://discord.gg/polymarket)
- **This Bot Issues**: Create an issue in this repository

---

## Quick Start After Providing Credentials

### For Kalshi (US Users):

```bash
# 1. Set up environment
export KALSHI_API_KEY_ID="your-key-id"
export KALSHI_PRIVATE_KEY_PATH="~/.kalshi/private_key.pem"

# 2. Test connection
python -c "from src.api import KalshiClient; c = KalshiClient(use_demo=True); print(c.health_check())"

# 3. Test in simulation (uses demo API)
python main.py --exchange kalshi --simulation

# 4. When ready, go live
python main.py --exchange kalshi --live
```

### For Polymarket (Non-US Users):

```bash
# 1. Set up environment
source ~/.polymarket_env

# 2. Test in simulation
python main.py --exchange polymarket --simulation

# 3. When ready, go live
python main.py --exchange polymarket --live
```

**Remember**: Start small, test thoroughly, and never risk more than you can afford to lose!
