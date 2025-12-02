# strategy.py
from dataclasses import dataclass
from typing import Optional, Literal, Dict, Any

import pandas as pd

from config import (
    ADX_THRESHOLD_TREND,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    MIN_VOLUME_FACTOR_TREND,
    MIN_VOLUME_FACTOR_RANGE,
    MAX_RELATIVE_SPREAD,
)
from indicators import ema, rsi, macd, bollinger_bands, adx


Side = Literal["BUY", "SELL"]


@dataclass
class Signal:
    side: Side  # BUY = long, SELL = short
    reason: str


def _prepare_ohlc_df(klines) -> pd.DataFrame:
    # Binance futures kline format: [openTime, open, high, low, close, volume, ...]
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(klines, columns=cols)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def evaluate_strategy(
    klines_5m,
    klines_15m,
    klines_1h,
    order_book: Dict[str, Any],
) -> Optional[Signal]:
    """
    Returns a Signal (BUY/SELL) or None (no trade).

    This version is "looser": trades more often while keeping the same
    multi-timeframe trend + range structure and basic liquidity checks.
    """
    df5 = _prepare_ohlc_df(klines_5m).iloc[:-1]   # drop last (potentially incomplete) candle
    df15 = _prepare_ohlc_df(klines_15m).iloc[:-1]
    df1h = _prepare_ohlc_df(klines_1h).iloc[:-1]

    if len(df5) < 60 or len(df15) < 60 or len(df1h) < 60:
        return None

    # ---- Trend filter on 1h (looser ADX) ----
    df1h["ema50"] = ema(df1h["close"], 50)
    df1h["ema200"] = ema(df1h["close"], 200)
    df1h["adx"] = adx(df1h)

    last1h = df1h.iloc[-1]

    # Allow trend from a bit lower ADX to loosen things up
    effective_adx_threshold = ADX_THRESHOLD_TREND * 0.75

    if last1h["ema50"] > last1h["ema200"] and last1h["adx"] > effective_adx_threshold:
        big_trend = "UP"
    elif last1h["ema50"] < last1h["ema200"] and last1h["adx"] > effective_adx_threshold:
        big_trend = "DOWN"
    else:
        big_trend = "RANGE"

    # ---- Mid timeframe filter (15m) ----
    df15["ema50"] = ema(df15["close"], 50)
    df15["ema200"] = ema(df15["close"], 200)
    last15 = df15.iloc[-1]
    mid_up = last15["ema50"] > last15["ema200"]
    mid_down = last15["ema50"] < last15["ema200"]

    # ---- 5m indicators ----
    df5["ema50"] = ema(df5["close"], 50)
    df5["ema20"] = ema(df5["close"], 20)
    df5["rsi"] = rsi(df5["close"], 14)
    macd_line, signal_line, hist = macd(df5["close"])
    df5["macd_line"] = macd_line
    df5["macd_signal"] = signal_line
    df5["macd_hist"] = hist
    df5["vol_ma20"] = df5["volume"].rolling(20).mean()
    lower_bb, mid_bb, upper_bb = bollinger_bands(df5["close"], 20, 2.0)
    df5["bb_lower"] = lower_bb
    df5["bb_upper"] = upper_bb

    last5 = df5.iloc[-1]

    # ---- Volume baseline ----
    vol_ma20 = last5["vol_ma20"]
    if vol_ma20 is None or vol_ma20 == 0:
        return None

    # Softer volume multipliers
    trend_vol_factor = min(0.6, MIN_VOLUME_FACTOR_TREND)
    range_vol_factor = min(0.4, MIN_VOLUME_FACTOR_RANGE)

    # ---- Order book / liquidity filter ----
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    if not bids or not asks:
        return None

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid_price = (best_bid + best_ask) / 2
    spread = (best_ask - best_bid) / mid_price

    # Keep spread sanity check
    if spread > MAX_RELATIVE_SPREAD:
        return None

    def depth_sum(levels):
        return sum(float(x[1]) for x in levels)

    bid_depth = depth_sum(bids[:20])
    ask_depth = depth_sum(asks[:20])
    if bid_depth <= 0 or ask_depth <= 0:
        return None

    # ---- Trend-following mode (UP/DOWN) ----
    # Looser: just need momentum confirmation, not strict crossovers

    if big_trend == "UP" and mid_up:
        # Long conditions:
        price_above_ema20 = last5["close"] > last5["ema20"]
        rsi_ok = last5["rsi"] > 45           # previously needed cross, now just >45
        macd_bull = last5["macd_hist"] > 0   # just positive, no cross needed
        vol_ok = last5["volume"] > trend_vol_factor * vol_ma20

        if price_above_ema20 and rsi_ok and macd_bull and vol_ok:
            return Signal(side="BUY", reason="UP trend, 5m EMA20+RSI>45+MACD>0")

    if big_trend == "DOWN" and mid_down:
        # Short conditions:
        price_below_ema20 = last5["close"] < last5["ema20"]
        rsi_ok = last5["rsi"] < 55           # previously stricter
        macd_bear = last5["macd_hist"] < 0   # just negative
        vol_ok = last5["volume"] > trend_vol_factor * vol_ma20

        if price_below_ema20 and rsi_ok and macd_bear and vol_ok:
            return Signal(side="SELL", reason="DOWN trend, 5m EMA20+RSI<55+MACD<0")

    # ---- Range / mean-reversion mode ----
    if big_trend == "RANGE":
        vol_ok = last5["volume"] > range_vol_factor * vol_ma20

        lower_band = last5["bb_lower"]
        upper_band = last5["bb_upper"]

        if lower_band and upper_band:
            # Range long near lower band
            touch_lower = last5["close"] <= lower_band * 1.005  # within ~0.5% of lower band
            rsi_low = last5["rsi"] < max(40, RSI_OVERSOLD + 10)  # usually <40
            if touch_lower and rsi_low and vol_ok:
                return Signal(side="BUY", reason="Range long near lower Bollinger, RSI low")

            # Range short near upper band
            touch_upper = last5["close"] >= upper_band * 0.995  # within ~0.5% of upper band
            rsi_high = last5["rsi"] > min(60, RSI_OVERBOUGHT - 10)  # usually >60
            if touch_upper and rsi_high and vol_ok:
                return Signal(side="SELL", reason="Range short near upper Bollinger, RSI high")

    return None
