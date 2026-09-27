"""
Indicadores tecnicos usando la libreria 'ta'.
Todos los calculos son locales, nada se envia a ningun servidor.
"""

import pandas as pd
import ta


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Agregar todos los indicadores necesarios al DataFrame de velas."""

    # === EMAs ===
    df["ema_9"] = ta.trend.ema_indicator(df["close"], window=9)
    df["ema_21"] = ta.trend.ema_indicator(df["close"], window=21)
    df["ema_50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["ema_100"] = ta.trend.ema_indicator(df["close"], window=100)
    df["ema_200"] = ta.trend.ema_indicator(df["close"], window=200)

    # === RSI ===
    df["rsi"] = ta.momentum.rsi(df["close"], window=14)
    df["rsi_fast"] = ta.momentum.rsi(df["close"], window=7)

    # === MACD ===
    macd = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # === Bollinger Bands ===
    bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_mid"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = bb.bollinger_wband()

    # === ATR (volatilidad) ===
    df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    df["atr_pct"] = df["atr"] / df["close"] * 100

    # === ADX (fuerza de tendencia) ===
    adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
    df["adx"] = adx.adx()
    df["adx_pos"] = adx.adx_pos()
    df["adx_neg"] = adx.adx_neg()

    # === Stochastic RSI ===
    stoch = ta.momentum.StochRSIIndicator(df["close"], window=14)
    df["stoch_k"] = stoch.stochrsi_k()
    df["stoch_d"] = stoch.stochrsi_d()

    # === Volumen ===
    df["vol_mean_20"] = df["volume"].rolling(window=20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_mean_20"]

    # === Ichimoku ===
    ichi = ta.trend.IchimokuIndicator(df["high"], df["low"])
    df["ichi_a"] = ichi.ichimoku_a()
    df["ichi_b"] = ichi.ichimoku_b()
    df["ichi_base"] = ichi.ichimoku_base_line()
    df["ichi_conv"] = ichi.ichimoku_conversion_line()

    return df
