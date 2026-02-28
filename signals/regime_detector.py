import pandas as pd
import numpy as np


# helper functions copied from the first half of 1138.txt
# (user instruction) - include ATR and Hurst implementations


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate the Average True Range (ATR) for an OHLC DataFrame.

    Expects a DataFrame with columns ['high', 'low', 'close'].  The result is a
    rolling mean of the true range over the specified period.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=1).mean()
    return atr


def hurst_exponent(series: pd.Series, max_lag: int = 50) -> float:
    """Estimate the Hurst exponent of a time series.

    This implementation uses the standard "rescaled range" approach via the
    log-log slope of the lagged standard deviation method.  A value
    <0.5 indicates mean reversion, >0.5 trending behavior, and ~0.5 random
    walk.
    """
    # drop NaN to avoid problems with diff
    series = series.dropna()
    if len(series) < 2:
        return 0.5

    lags = range(2, min(max_lag, len(series)))
    # use the standard deviation of lagged differences; no extra sqrt
    tau = [np.std(series.diff(lag).dropna()) for lag in lags]
    # guard against zero or constant series
    if any(t <= 0 for t in tau):
        return 0.5
    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    # slope = H*2 in this formulation, so multiply by 0.5
    hurst = poly[0] * 0.5
    return float(hurst)
