# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# ===========================
#  Binance API & Environment
# ===========================

# Get these from https://demo.binance.com/en/my/settings/api-management
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Base URL for Binance USDT-margined futures.
# Override in .env if needed.
BINANCE_FAPI_BASE = os.getenv("BINANCE_FAPI_BASE", "https://testnet.binancefuture.com")

# Trading symbol
SYMBOL = os.getenv("SYMBOL", "ETHUSDT")

# Futures leverage
TARGET_LEVERAGE = int(os.getenv("TARGET_LEVERAGE", 10))

# Margin mode:
# Valid values for Binance API: "CROSSED" or "ISOLATED"
# You asked to use cross margin:
MARGIN_TYPE = os.getenv("MARGIN_TYPE", "CROSSED")

# ===========================
#  Telegram Notifications
# ===========================

# From @BotFather
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Your chat ID (numeric, but stored as string is fine)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ===========================
#  Scheduling / Loop Timing
# ===========================

# Check market every 5 minutes (300 seconds)
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 300))

# ===========================
#  Timeframes (you requested 1h / 30m / 15m)
# ===========================

# Entry / fine trend timeframe
TF_MAIN = os.getenv("TF_MAIN", "15m")

# Medium trend timeframe
TF_MID = os.getenv("TF_MID", "30m")

# Higher timeframe context
TF_HIGH = os.getenv("TF_HIGH", "1h")

# ===========================
#  Position Sizing & Risk
# ===========================

# Use 10% of current available USDT wallet balance per trade
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", 0.10))  # 0.10 = 10%

# Disable opening new trades for the day if equity falls
# this % from day's start (daily drawdown)
MAX_DAILY_DRAWDOWN_PCT = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", 0.10))  # 10%

# Extra safety: maximum allowed notional relative to equity (e.g. 5x)
# If equity is 5,000 USDT and this is 5.0 => max notional ≈ 25,000 USDT
MAX_NOTIONAL_MULTIPLIER = float(os.getenv("MAX_NOTIONAL_MULTIPLIER", 5.0))

# Kept for backwards compatibility with any old code that might reference it.
# The new bot uses ATR-based SL/TP instead of this.
DEFAULT_STOP_LOSS_PCT = float(os.getenv("DEFAULT_STOP_LOSS_PCT", 0.10))

# ===========================
#  Indicator Parameters
# ===========================

# --- MACD ---
# You requested MACD(8, 17, 5)
MACD_FAST = int(os.getenv("MACD_FAST", 8))
MACD_SLOW = int(os.getenv("MACD_SLOW", 17))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", 5))

# --- ATR (for SL/TP) ---
# ATR is computed on 15m candles in bot.py
ATR_PERIOD = int(os.getenv("ATR_PERIOD", 14))
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 1.5))  # 1.5 * ATR = stop loss
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 3.0))  # 3 * ATR = take profit

# --- Trend detection / oscillators (used in strategy.py) ---
ADX_THRESHOLD_TREND = float(os.getenv("ADX_THRESHOLD_TREND", 20))
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", 30))
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", 70))

# --- Volume filters (used in strategy.py) ---
# Volume must be at least X * MA(20) to be considered "good"
MIN_VOLUME_FACTOR_TREND = float(os.getenv("MIN_VOLUME_FACTOR_TREND", 0.8))
MIN_VOLUME_FACTOR_RANGE = float(os.getenv("MIN_VOLUME_FACTOR_RANGE", 0.5))
