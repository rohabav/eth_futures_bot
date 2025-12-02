# strategy.py
from dataclasses import dataclass
from typing import Optional, Literal, Tuple

import pandas as pd

from config import (
    ADX_THRESHOLD_TREND,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    MIN_VOLUME_FACTOR_TREND,
    MIN_VOLUME_FACTOR_RANGE,
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
    klines_15m,
    klines_30m,
    klines_1h,
) -> Tuple[Optional[Signal], str]:
    """
    Returns (Signal or None, detailed_explanation_string).

    Structure:
    - 1h: context trend using EMA50/EMA100 + ADX.
    - 30m: main trend direction (EMA50/EMA100 + ADX).
    - 15m: entry timing (RSI crosses, MACD 8/17/5, price vs EMA, volume).
    - Range regime: Bollinger + RSI at extremes.

    IMPORTANT: no order book logic here anymore.
    """
    df15 = _prepare_ohlc_df(klines_15m).iloc[:-1]   # drop last (potentially incomplete) candle
    df30 = _prepare_ohlc_df(klines_30m).iloc[:-1]
    df1h = _prepare_ohlc_df(klines_1h).iloc[:-1]

    if len(df15) < 60 or len(df30) < 60 or len(df1h) < 60:
        return None, "Not enough candles on one of the timeframes (need ~60 each)."

    explanation_parts = []

    # ---------- 1h context ----------
    df1h["ema50"] = ema(df1h["close"], 50)
    df1h["ema100"] = ema(df1h["close"], 100)
    df1h["adx"] = adx(df1h)

    last1h = df1h.iloc[-1]
    ema50_1h = last1h["ema50"]
    ema100_1h = last1h["ema100"]
    adx_1h = last1h["adx"]

    if ema50_1h > ema100_1h and adx_1h > ADX_THRESHOLD_TREND:
        big_trend = "UP"
    elif ema50_1h < ema100_1h and adx_1h > ADX_THRESHOLD_TREND:
        big_trend = "DOWN"
    else:
        big_trend = "RANGE"

    explanation_parts.append(
        f"1h: EMA50={ema50_1h:.2f}, EMA100={ema100_1h:.2f}, ADX={adx_1h:.1f} -> big_trend={big_trend}"
    )

    # ---------- 30m trend ----------
    df30["ema50"] = ema(df30["close"], 50)
    df30["ema100"] = ema(df30["close"], 100)
    df30["adx"] = adx(df30)

    last30 = df30.iloc[-1]
    ema50_30 = last30["ema50"]
    ema100_30 = last30["ema100"]
    adx_30 = last30["adx"]

    mid_up = ema50_30 > ema100_30
    mid_down = ema50_30 < ema100_30

    if mid_up:
        mid_trend = "UP"
    elif mid_down:
        mid_trend = "DOWN"
    else:
        mid_trend = "FLAT"

    explanation_parts.append(
        f"30m: EMA50={ema50_30:.2f}, EMA100={ema100_30:.2f}, ADX={adx_30:.1f} -> mid_trend={mid_trend}"
    )

    # ---------- 15m entry timeframe ----------
    df15["ema50"] = ema(df15["close"], 50)
    df15["ema100"] = ema(df15["close"], 100)
    df15["rsi"] = rsi(df15["close"], 14)
    macd_line, signal_line, hist = macd(df15["close"])  # uses 8/17/5 by default
    df15["macd_hist"] = hist
    df15["vol_ma20"] = df15["volume"].rolling(20).mean()
    lower_bb, mid_bb, upper_bb = bollinger_bands(df15["close"], 20, 2.0)
    df15["bb_lower"] = lower_bb
    df15["bb_upper"] = upper_bb

    last15 = df15.iloc[-1]
    prev15 = df15.iloc[-2]

    rsi_last = last15["rsi"]
    rsi_prev = prev15["rsi"]
    macd_hist_last = last15["macd_hist"]
    macd_hist_prev = prev15["macd_hist"]
    vol_last = last15["volume"]
    vol_ma20 = last15["vol_ma20"]
    ema50_15 = last15["ema50"]
    ema100_15 = last15["ema100"]
    bb_lower = last15["bb_lower"]
    bb_upper = last15["bb_upper"]
    close_15 = last15["close"]

    if vol_ma20 is None or vol_ma20 == 0:
        return None, "15m volume MA is zero/NaN, skipping trading."

    explanation_parts.append(
        f"15m: close={close_15:.2f}, EMA50={ema50_15:.2f}, EMA100={ema100_15:.2f}, "
        f"RSI(prev={rsi_prev:.1f}, last={rsi_last:.1f}), "
        f"MACD hist(prev={macd_hist_prev:.4f}, last={macd_hist_last:.4f}), "
        f"vol={vol_last:.0f}, vol_ma20={vol_ma20:.0f}"
    )

    # ---------- Conditions ----------

    # Trend-following LONG: big UP, mid UP, RSI cross up, MACD cross up, price above EMA50, sufficient volume
    rsi_cross_up = rsi_prev < 45 <= rsi_last
    macd_bull = macd_hist_prev < 0 <= macd_hist_last
    price_above_ema50 = close_15 > ema50_15
    vol_ok_trend = vol_last > MIN_VOLUME_FACTOR_TREND * vol_ma20

    long_trend_ok = (big_trend == "UP") and (mid_trend == "UP")

    # Trend-following SHORT: big DOWN, mid DOWN, RSI cross down, MACD cross down, price below EMA50, sufficient volume
    rsi_cross_down = rsi_prev > 55 >= rsi_last
    macd_bear = macd_hist_prev > 0 >= macd_hist_last
    price_below_ema50 = close_15 < ema50_15
    vol_ok_trend_short = vol_last > MIN_VOLUME_FACTOR_TREND * vol_ma20

    short_trend_ok = (big_trend == "DOWN") and (mid_trend == "DOWN")

    # Range / mean-reversion mode
    range_vol_ok = vol_last > MIN_VOLUME_FACTOR_RANGE * vol_ma20

    explanation_parts.append(
        "Conditions: "
        f"long_trend_ok={long_trend_ok}, rsi_cross_up={rsi_cross_up}, macd_bull={macd_bull}, "
        f"price_above_ema50={price_above_ema50}, vol_ok_trend={vol_ok_trend} | "
        f"short_trend_ok={short_trend_ok}, rsi_cross_down={rsi_cross_down}, macd_bear={macd_bear}, "
        f"price_below_ema50={price_below_ema50}, vol_ok_trend_short={vol_ok_trend_short}, "
        f"range_vol_ok={range_vol_ok}"
    )

    # ---------- Trend-following entries ----------

    if long_trend_ok and rsi_cross_up and macd_bull and price_above_ema50 and vol_ok_trend:
        explanation_parts.append("Decision: OPEN LONG (trend-following) – all long conditions satisfied.")
        return Signal(side="BUY", reason="Trend long (1h+30m up, RSI/MACD cross up, EMA50 support)"), "\n".join(
            explanation_parts
        )

    if short_trend_ok and rsi_cross_down and macd_bear and price_below_ema50 and vol_ok_trend_short:
        explanation_parts.append("Decision: OPEN SHORT (trend-following) – all short conditions satisfied.")
        return Signal(side="SELL", reason="Trend short (1h+30m down, RSI/MACD cross down, EMA50 resistance)"), "\n".join(
            explanation_parts
        )

    # ---------- Range entries ----------

    if big_trend == "RANGE" and bb_lower and bb_upper:
        # Range long near lower band, RSI rebounding from oversold
        touch_lower = close_15 <= bb_lower
        rsi_rebound = rsi_prev < RSI_OVERSOLD <= rsi_last

        if touch_lower and rsi_rebound and range_vol_ok:
            explanation_parts.append(
                "Decision: OPEN LONG (range) – price at lower Bollinger, RSI rebounding from oversold, volume ok."
            )
            return Signal(side="BUY", reason="Range long at lower Bollinger"), "\n".join(explanation_parts)

        # Range short near upper band, RSI rolling over from overbought
        touch_upper = close_15 >= bb_upper
        rsi_rollover = rsi_prev > RSI_OVERBOUGHT >= rsi_last

        if touch_upper and rsi_rollover and range_vol_ok:
            explanation_parts.append(
                "Decision: OPEN SHORT (range) – price at upper Bollinger, RSI rolling from overbought, volume ok."
            )
            return Signal(side="SELL", reason="Range short at upper Bollinger"), "\n".join(explanation_parts)

    # ---------- No trade ----------
    if big_trend in ("UP", "DOWN"):
        fail_reasons = []
        if big_trend == "UP":
            if not long_trend_ok:
                fail_reasons.append("1h/30m trend alignment not strong up.")
            if not rsi_cross_up:
                fail_reasons.append("RSI did not cross up through 45.")
            if not macd_bull:
                fail_reasons.append("MACD histogram did not cross from negative to positive.")
            if not price_above_ema50:
                fail_reasons.append("Price not firmly above 15m EMA50.")
            if not vol_ok_trend:
                fail_reasons.append("Trend volume not high enough.")
        else:
            if not short_trend_ok:
                fail_reasons.append("1h/30m trend alignment not strong down.")
            if not rsi_cross_down:
                fail_reasons.append("RSI did not cross down through 55.")
            if not macd_bear:
                fail_reasons.append("MACD histogram did not cross from positive to negative.")
            if not price_below_ema50:
                fail_reasons.append("Price not firmly below 15m EMA50.")
            if not vol_ok_trend_short:
                fail_reasons.append("Trend volume not high enough.")

        explanation_parts.append(
            "Decision: NO TRADE (trend mode) – " + ("; ".join(fail_reasons) if fail_reasons else "conditions ambiguous.")
        )
    else:
        explanation_parts.append(
            "Decision: NO TRADE (range mode) – range, but Bollinger/RSI conditions not clean enough yet."
        )

    return None, "\n".join(explanation_parts)
