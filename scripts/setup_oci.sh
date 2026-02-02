#!/bin/bash
# PolyPrinting2026 - OCI Setup Script
#
# This script automates the setup of the trading bot on Oracle Cloud Infrastructure.
# Run on a fresh Ubuntu 22.04 instance after SSH access is established.
#
# Usage:
#   chmod +x setup_oci.sh
#   ./setup_oci.sh
#
# After running this script, you still need to:
# 1. Configure your API keys in ~/.polymarket_env
# 2. Edit config/config.yaml as needed
# 3. Start the bot with: sudo systemctl start polybot

set -e

echo "=============================================="
echo "PolyPrinting2026 - OCI Setup Script"
echo "=============================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
BOT_DIR="$HOME/polymarket-bot"
PYTHON_VERSION="3.10"

echo -e "${GREEN}[1/8] Updating system packages...${NC}"
sudo apt update && sudo apt upgrade -y

echo -e "${GREEN}[2/8] Installing dependencies...${NC}"
sudo apt install -y \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python3-pip \
    git \
    tmux \
    htop \
    curl \
    wget \
    jq

echo -e "${GREEN}[3/8] Creating project directory...${NC}"
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

echo -e "${GREEN}[4/8] Setting up Python virtual environment...${NC}"
python${PYTHON_VERSION} -m venv venv
source venv/bin/activate

echo -e "${GREEN}[5/8] Upgrading pip...${NC}"
pip install --upgrade pip

echo -e "${GREEN}[6/8] Checking if repository files exist...${NC}"
if [ ! -f "requirements.txt" ]; then
    echo -e "${YELLOW}No requirements.txt found. You need to clone or copy the repository first.${NC}"
    echo -e "${YELLOW}After copying files, run: pip install -r requirements.txt${NC}"
else
    echo -e "${GREEN}Installing Python dependencies...${NC}"
    pip install -r requirements.txt
fi

echo -e "${GREEN}[7/8] Creating environment file template...${NC}"
if [ ! -f "$HOME/.polymarket_env" ]; then
    cat > "$HOME/.polymarket_env" << 'EOF'
# Polymarket API Credentials
# Fill in your actual values and keep this file secure!

export POLYMARKET_API_KEY="your_api_key_here"
export POLYMARKET_API_SECRET="your_api_secret_here"
export POLYMARKET_API_PASSPHRASE="your_passphrase_here"
export POLYMARKET_PRIVATE_KEY="your_wallet_private_key"
export POLYMARKET_FUNDER="your_wallet_address"

# Optional: Exchange API keys for price feeds
# export BINANCE_API_KEY=""
# export BINANCE_API_SECRET=""
# export COINBASE_API_KEY=""
# export COINBASE_API_SECRET=""
EOF
    chmod 600 "$HOME/.polymarket_env"
    echo -e "${YELLOW}Created ~/.polymarket_env - EDIT THIS FILE WITH YOUR CREDENTIALS${NC}"
else
    echo -e "${YELLOW}~/.polymarket_env already exists${NC}"
fi

# Add to bashrc if not already there
if ! grep -q "polymarket_env" "$HOME/.bashrc"; then
    echo 'source ~/.polymarket_env' >> "$HOME/.bashrc"
fi

echo -e "${GREEN}[8/8] Creating log directories...${NC}"
mkdir -p "$BOT_DIR/logs"
mkdir -p "$BOT_DIR/data/historical"

echo ""
echo "=============================================="
echo -e "${GREEN}Setup Complete!${NC}"
echo "=============================================="
echo ""
echo "Next steps:"
echo ""
echo "1. Copy your bot files to: $BOT_DIR"
echo "   (or clone from your repository)"
echo ""
echo "2. Edit your API credentials:"
echo "   ${YELLOW}nano ~/.polymarket_env${NC}"
echo ""
echo "3. Install Python dependencies (if not already):"
echo "   ${YELLOW}cd $BOT_DIR && source venv/bin/activate${NC}"
echo "   ${YELLOW}pip install -r requirements.txt${NC}"
echo ""
echo "4. Edit configuration:"
echo "   ${YELLOW}cp config/config.example.yaml config/config.yaml${NC}"
echo "   ${YELLOW}nano config/config.yaml${NC}"
echo ""
echo "5. Test in simulation mode:"
echo "   ${YELLOW}python -m src.main --simulation${NC}"
echo ""
echo "6. Set up systemd service for 24/7 operation:"
echo "   ${YELLOW}sudo cp scripts/polybot.service /etc/systemd/system/${NC}"
echo "   ${YELLOW}sudo systemctl daemon-reload${NC}"
echo "   ${YELLOW}sudo systemctl enable polybot${NC}"
echo "   ${YELLOW}sudo systemctl start polybot${NC}"
echo ""
echo "7. Monitor the bot:"
echo "   ${YELLOW}sudo journalctl -u polybot -f${NC}"
echo "   ${YELLOW}tail -f logs/polybot.log${NC}"
echo ""
echo "=============================================="
echo -e "${RED}IMPORTANT: Review all settings before enabling live trading!${NC}"
echo "=============================================="
