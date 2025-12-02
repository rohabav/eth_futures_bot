# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# -------- Exchange / API --------

BINANCE_ENV = os.getenv("BINANCE_ENV", "demo")  # "demo" or "real"

# Base URLs from Binance docs / demo migration info
if BINANCE_ENV == "demo":
    BINANCE_FAPI_BASE = "https://demo-fapi.binance.com"  # Demo Futures REST :contentReference[oaicite:6]{index=6}
else:
    BINANCE_FAPI_BASE = "https://fapi.binance.com"       # Real Futures REST :contentReference[oaicite:7]{index=7}

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = "ETHUSDT"

# Leverage & margin mode (applied once at startup)
TARGET_LEVERAGE = 10
MARGIN_TYPE = "ISOLATED"  # or "CROSSED"

# -------- Risk Management --------

TRADE_BALANCE_FRACTION = 0.10  # 10% of wallet per new position
DEFAULT_STOP_LOSS_PCT = 0.015  # 1.5% price SL (safer than 10% with 10x)
DAILY_DRAWDOWN_LIMIT = 0.10    # 10% equity drop stops new entries

MAX_OPEN_POSITIONS = 1         # v1: 1 position per symbol (we can extend later)

# -------- Strategy / Indicators --------

# Timeframes as Binance intervals
TF_MAIN = "5m"
TF_MID = "15m"
TF_HIGH = "1h"

ADX_THRESHOLD_TREND = 20
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Volume & liquidity filters
MIN_VOLUME_FACTOR_TREND = 0.8
MIN_VOLUME_FACTOR_RANGE = 0.5
MAX_RELATIVE_SPREAD = 0.0005  # 0.05%

# -------- Scheduler --------

CHECK_INTERVAL_SECONDS = 300   # 5 minutes

# -------- Telegram --------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
