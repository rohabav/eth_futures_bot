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
from indicators import ema, rsi, macd, atr, bollinger_bands, adx


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
    Returns a Signal or None (no trade).
    """
    df5 = _prepare_ohlc_df(klines_5m).iloc[:-1]   # drop last (potentially incomplete) candle
    df15 = _prepare_ohlc_df(klines_15m).iloc[:-1]
    df1h = _prepare_ohlc_df(klines_1h).iloc[:-1]

    if len(df5) < 60 or len(df15) < 60 or len(df1h) < 60:
        return None

    # ---- Trend filter on 1h ----
    df1h["ema50"] = ema(df1h["close"], 50)
    df1h["ema200"] = ema(df1h["close"], 200)
    df1h["adx"] = adx(df1h)

    last1h = df1h.iloc[-1]
    if last1h["ema50"] > last1h["ema200"] and last1h["adx"] > ADX_THRESHOLD_TREND:
        big_trend = "UP"
    elif last1h["ema50"] < last1h["ema200"] and last1h["adx"] > ADX_THRESHOLD_TREND:
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
    prev5 = df5.iloc[-2]

    # ---- Volume filter ----
    vol_ma20 = last5["vol_ma20"]
    if vol_ma20 is None or vol_ma20 == 0:
        return None

    # ---- Order book / liquidity filter ----
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    if not bids or not asks:
        return None

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid_price = (best_bid + best_ask) / 2
    spread = (best_ask - best_bid) / mid_price

    if spread > MAX_RELATIVE_SPREAD:
        return None

    def depth_sum(levels):
        return sum(float(x[1]) for x in levels)

    bid_depth = depth_sum(bids[:20])
    ask_depth = depth_sum(asks[:20])

    # ---- Trend-following mode ----
    if big_trend == "UP" and mid_up:
        # Pullback long: RSI crosses up from below 45, MACD hist from negative to positive
        rsi_cross_up = prev5["rsi"] < 45 <= last5["rsi"]
        macd_bull = prev5["macd_hist"] < 0 <= last5["macd_hist"]
        vol_ok = last5["volume"] > MIN_VOLUME_FACTOR_TREND * vol_ma20
        price_above_ema = last5["close"] > last5["ema50"]
        bid_support = bid_depth >= 0.9 * ask_depth

        if rsi_cross_up and macd_bull and vol_ok and price_above_ema and bid_support:
            return Signal(side="BUY", reason="UP trend pullback long")

    if big_trend == "DOWN" and mid_down:
        # Pullback short: RSI crosses down from above 55, MACD hist from positive to negative
        rsi_cross_down = prev5["rsi"] > 55 >= last5["rsi"]
        macd_bear = prev5["macd_hist"] > 0 >= last5["macd_hist"]
        vol_ok = last5["volume"] > MIN_VOLUME_FACTOR_TREND * vol_ma20
        price_below_ema = last5["close"] < last5["ema50"]
        ask_pressure = ask_depth >= 0.9 * bid_depth

        if rsi_cross_down and macd_bear and vol_ok and price_below_ema and ask_pressure:
            return Signal(side="SELL", reason="DOWN trend pullback short")

    # ---- Range / mean-reversion mode ----
    if big_trend == "RANGE":
        vol_ok = last5["volume"] > MIN_VOLUME_FACTOR_RANGE * vol_ma20

        # Long at lower band
        is_touch_lower = last5["close"] <= last5["bb_lower"]
        rsi_rebound = prev5["rsi"] < RSI_OVERSOLD <= last5["rsi"]

        if is_touch_lower and rsi_rebound and vol_ok:
            return Signal(side="BUY", reason="Range long at lower Bollinger")

        # Short at upper band
        is_touch_upper = last5["close"] >= last5["bb_upper"]
        rsi_rollover = prev5["rsi"] > RSI_OVERBOUGHT >= last5["rsi"]

        if is_touch_upper and rsi_rollover and vol_ok:
            return Signal(side="SELL", reason="Range short at upper Bollinger")

    return None
