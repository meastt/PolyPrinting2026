# KalshiBot Weather Prediction Trading System

## Overview

This system transforms the crypto arbitrage bot into an AI-powered weather prediction trader that exploits systematic forecast biases between major weather providers and NWS station data.

## Core Strategy

1. **Historical Bias Calibration**: Track forecast errors over 180 days by weather provider and location
2. **Ensemble Prediction**: Combine multiple forecasts with bias adjustments
3. **AI Validation**: Use Groq/Llama to validate trading logic
4. **Statistical Edge**: Capture market inefficiencies through superior predictions

## Architecture

```
Weather Analyzer → Weather Strategist → Trader → Kalshi Markets
       ↓                    ↓
Bias Calibrator    →    Agent (Telegram)
```

### Components

1. **Weather API Client** (`weather/weather_api_client.py`)
   - Fetches forecasts from NWS, AccuWeather, WeatherAPI, OpenWeather
   - Fetches historical actuals from NWS
   - Standardized data format

2. **Station Mapper** (`weather/station_mapper.py`)
   - Maps Kalshi tickers to NWS stations
   - 8 cities: NYC, Boston, Chicago, LA, SF, Austin, Denver, Miami

3. **Bias Calculator** (`weather/bias_calculator.py`)
   - Tracks forecast errors over 180-day rolling window
   - Calculates bias adjustments by source/station/metric
   - Persists bias data in JSON

4. **Weather Analyzer** (`weather/weather_analyzer.py`)
   - Main prediction engine
   - Applies bias adjustments to forecasts
   - Calculates ensemble predictions
   - Maps to Kalshi temperature ranges
   - Outputs confidence scores

5. **Weather Market Scanner** (`weather/weather_market_scanner.py`)
   - Fetches KXHIGH* and KXLOW* markets from Kalshi
   - Parses temperature ranges
   - Filters tradeable markets (2-24 hours to close)

6. **Weather Strategist** (`weather/weather_strategist.py`)
   - AI-powered decision making
   - Scans markets every 30 minutes
   - Validates trades with Groq/Llama
   - Generates trade signals

7. **Bias Calibrator** (`weather/bias_calibrator.py`)
   - Daily task at 6 AM: Update biases from yesterday's actuals
   - Daily task at noon: Archive today's forecasts

8. **Telegram Agent** (`agent/agent_brain.py`)
   - New commands: `/weather`, `/bias`, `/markets`
   - Displays predictions, bias stats, active positions

## Setup Instructions

### 1. Weather API Keys (Required)

Add to `.env`:

```bash
# AccuWeather (https://developer.accuweather.com/)
ACCUWEATHER_API_KEY=your_key_here

# WeatherAPI (https://www.weatherapi.com/)
WEATHERAPI_KEY=your_key_here

# OpenWeather (https://openweathermap.org/api)
OPENWEATHER_KEY=your_key_here
```

**Free Tier Options:**
- AccuWeather: 50 calls/day (trial)
- WeatherAPI: 1M calls/month free
- OpenWeather: 1000 calls/day free

### 2. Trading Parameters

Already configured in `.env`:

```bash
WEATHER_STARTING_BALANCE=20.00      # Starting capital
WEATHER_MAX_POSITION_SIZE=2          # Contracts per trade
WEATHER_MIN_CONFIDENCE=0.75          # 75% confidence threshold
WEATHER_MIN_EDGE=0.10                # 10% edge minimum
WEATHER_SCAN_INTERVAL=1800           # 30 minutes between scans
```

### 3. Build and Deploy

```bash
# Build weather containers
docker-compose build weather-strategist bias-calibrator

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f weather-strategist
docker-compose logs -f bias-calibrator
```

### 4. Initial Setup (First 30 Days)

The system needs historical bias data for optimal performance:

**Week 1:**
- Bias calibrator archives forecasts daily
- No bias adjustments yet (insufficient data)
- System operates on raw ensemble predictions

**Week 2-4:**
- Bias data accumulates (30+ samples per source)
- System begins applying bias adjustments
- Confidence scores improve

**Month 2+:**
- Full 180-day bias window (gold standard)
- Maximum prediction accuracy
- Optimal trading edge

## Telegram Commands

```
/weather   - View weather predictions and positions
/bias      - View forecast bias statistics
/markets   - View active weather markets
/status    - Overall bot status
/pause     - Pause all trading
/resume    - Resume trading
/kill      - Emergency halt
```

## Trading Logic

### Signal Generation Flow

1. **Market Scan**: Find weather markets closing in 2-24 hours
2. **Fetch Forecasts**: Get predictions from all sources
3. **Apply Bias**: Adjust forecasts using historical data
4. **Ensemble**: Weighted average (NWS 40%, others 20%)
5. **Confidence**: Calculate based on ensemble agreement
6. **Edge Calculation**: Confidence vs market price
7. **AI Validation**: Groq/Llama validates logic
8. **Signal Output**: Write to `weather_signals.json`

### Trade Filters

Only trades that meet ALL criteria:
- ✅ Confidence ≥ 75%
- ✅ Edge ≥ 10%
- ✅ Ensemble std dev < 3°F
- ✅ Bias data available for ≥50% of sources
- ✅ 2-24 hours until market close
- ✅ AI validation passes

### Risk Management

```python
MAX_POSITION_SIZE = 2 contracts
MAX_CONCURRENT_POSITIONS = 4 markets
MAX_EXPOSURE_PER_CITY = 3 contracts/day
DAILY_LOSS_LIMIT = $5
WEEKLY_LOSS_LIMIT = $10
```

## File Structure

```
KalshiBot/
├── weather/
│   ├── __init__.py
│   ├── weather_api_client.py       # Multi-source API client
│   ├── station_mapper.py           # Ticker → NWS station mapping
│   ├── bias_calculator.py          # Bias tracking and adjustment
│   ├── weather_analyzer.py         # Main prediction engine
│   ├── weather_market_scanner.py   # Kalshi market scanner
│   ├── weather_strategist.py       # AI decision making
│   ├── bias_calibrator.py          # Daily bias updates
│   ├── Dockerfile                  # Strategist container
│   └── Dockerfile.calibrator       # Calibrator container
├── config/
│   ├── forecast_bias.json          # Rolling bias averages
│   ├── bias_history.json           # Historical error records
│   ├── weather_signals.json        # Trade signals
│   ├── weather_state.json          # Trader state
│   └── forecast_archive/           # Daily forecast snapshots
│       └── YYYY-MM-DD.json
├── agent/
│   └── agent_brain.py              # Updated with weather commands
├── trader/
│   └── market_maker.py             # Updated to process weather signals
└── docker-compose.yml              # Added weather services
```

## Configuration Files

### forecast_bias.json
```json
{
  "KNYC": {
    "AccuWeather": {
      "high_bias": 1.8,
      "low_bias": -0.5,
      "high_samples": 180,
      "low_samples": 180,
      "last_updated": "2026-02-05T12:00:00Z"
    }
  }
}
```

### weather_signals.json
```json
{
  "signals": [
    {
      "id": "KXHIGHNY-26FEB06-B68_1738752000",
      "ticker": "KXHIGHNY-26FEB06-B68",
      "action": "BUY_YES",
      "predicted_temp": 69.3,
      "confidence": 0.82,
      "market_price": 0.65,
      "edge": 0.17,
      "size": 2,
      "status": "PENDING"
    }
  ]
}
```

## Monitoring

### Check System Health

```bash
# Weather strategist logs
docker-compose logs -f weather-strategist

# Bias calibrator logs
docker-compose logs -f bias-calibrator

# Check signal generation
cat config/weather_signals.json | jq '.signals | length'

# Check bias data
cat config/forecast_bias.json | jq 'keys'
```

### Telegram Monitoring

Use `/weather` command to see:
- Current balance
- Daily P&L
- Recent signals
- Win rate

Use `/bias` to verify:
- Bias data collection progress
- Sample counts per source
- Bias magnitudes

## Expected Performance

### Week 1 (No Bias Data)
- Win rate: 60-65%
- Accuracy limited to ensemble predictions
- Conservative position sizing

### Month 1 (30-60 Days Bias Data)
- Win rate: 65-70%
- Bias adjustments improving accuracy
- Increased confidence scores

### Month 2+ (180 Days Bias Data)
- Win rate: 70-75%
- Full statistical edge captured
- Optimal trading performance

### Target Goals

- **Starting Capital**: $20
- **3-Month Target**: $50 (2.5x)
- **6-Month Target**: $100 (5x)
- **Win Rate**: 70%+
- **Daily Trades**: 2-4

## Troubleshooting

### No Signals Generated

1. Check weather API keys in `.env`
2. Verify Kalshi API connection
3. Check strategist logs: `docker-compose logs weather-strategist`
4. Ensure markets are available (check `/markets` command)

### Low Confidence Scores

- Normal in first 30 days (no bias data)
- Ensemble disagreement (check std dev)
- Weather volatility (cold fronts, heat waves)

### Bias Calibrator Not Running

1. Check logs: `docker-compose logs bias-calibrator`
2. Verify schedule (6 AM, 12 PM daily)
3. Check NWS API access (no auth required)

### Trader Not Executing Signals

1. Check trader logs: `docker-compose logs trader`
2. Verify `weather_signals.json` has PENDING signals
3. Check Kalshi API connectivity
4. Verify PAPER_TRADING mode in `.env`

## Paper Trading

For initial testing, keep:

```bash
PAPER_TRADING=true
```

This simulates trades without real money. Monitor for 1-2 weeks before going live.

## Going Live

1. **Collect 180 days of bias data** (critical!)
2. **Verify positive P&L in paper mode** (1+ weeks)
3. **Start small**: $5 allocated, 1 contract max
4. **Scale gradually**: Increase to $10, then $20
5. **Monitor daily**: Use `/weather` command

## Rollback Plan

If system underperforms:

1. Use `/kill` to halt trading
2. Analyze losing trades (ensemble disagreement? timing?)
3. Adjust parameters:
   - Increase `WEATHER_MIN_CONFIDENCE` to 0.85
   - Increase `WEATHER_MIN_EDGE` to 0.15
   - Reduce `WEATHER_MAX_POSITION_SIZE` to 1
4. Return to paper trading
5. Collect more bias data (extend window)

## Data Requirements

### Weather APIs (Daily)
- 8 cities × 4 sources × 2 metrics = 64 API calls/day
- AccuWeather: Need paid tier ($300/year) or rotate free trials
- WeatherAPI: Free tier covers it (1M calls/month)
- OpenWeather: Free tier sufficient

### Kalshi API (Daily)
- Market scans: ~50 calls/day
- Order placements: ~5-10 calls/day
- Well within free tier limits

## Success Metrics

Track these via Telegram `/weather`:

- **Win Rate**: Target 70%+
- **Average Edge**: Target 15%+
- **Daily P&L**: Positive trend
- **Bias Quality**: 100+ samples per source
- **Ensemble Agreement**: Std dev < 2°F

## Support

Questions? Check:
- Plan document: `WEATHER_TRADING_PLAN.md`
- Logs: `docker-compose logs -f <service>`
- Telegram: `/help`, `/logs`, `/status`

## Next Steps

1. ✅ Obtain weather API keys
2. ✅ Update `.env` with keys
3. ✅ Build and deploy: `docker-compose up -d`
4. ✅ Monitor Telegram: `/weather`, `/bias`
5. ✅ Wait 30 days for bias data
6. ✅ Validate in paper mode
7. ✅ Go live with small capital

Good luck! 🌤️📊
