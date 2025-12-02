# indicators.py
from typing import Tuple

import pandas as pd

from config import MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val


def macd(
    series: pd.Series,
    fast: int = None,
    slow: int = None,
    signal: int = None,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD using config defaults (8,17,5) unless overridden.
    Returns macd_line, signal_line, histogram.
    """
    fast = fast or MACD_FAST
    slow = slow or MACD_SLOW
    signal = signal or MACD_SIGNAL

    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
):
    """Classic Bollinger Bands."""
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return lower, sma, upper


def atr(df: pd.DataFrame, period: int = None) -> pd.Series:
    """
    Average True Range from OHLC dataframe.
    df must have columns: 'high', 'low', 'close'.
    """
    period = period or ATR_PERIOD

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift()

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index for trend strength.
    df must have columns: 'high', 'low', 'close'.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_high = high.shift()
    prev_low = low.shift()
    prev_close = close.shift()

    plus_dm = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)

    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr_series = tr.rolling(period).mean()

    plus_di = 100 * (plus_dm.rolling(period).mean() / atr_series)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_series)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.rolling(period).mean()
    return adx_val
