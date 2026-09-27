"""
Configuracion central del bot. Lee todo desde variables de entorno.
NUNCA hardcodear API keys en el codigo.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env desde la raiz del proyecto
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class Config:
    # Broker activo: binance (spot) | exness (CFD con apalancamiento via MT5)
    BROKER = os.getenv("BROKER", "binance").lower()

    # === EXNESS / MetaTrader 5 ===
    EXNESS_LOGIN = os.getenv("EXNESS_LOGIN", "")
    EXNESS_PASSWORD = os.getenv("EXNESS_PASSWORD", "")
    EXNESS_SERVER = os.getenv("EXNESS_SERVER", "Exness-MT5Trial")
    MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "")
    EXNESS_SYMBOLS = [s.strip() for s in os.getenv(
        "EXNESS_SYMBOLS", "BTCUSD,XRPUSD,ETHUSD").split(",") if s.strip()]

    # Reglas de apalancamiento (solo aplican a Exness)
    MAX_LEVERAGE = float(os.getenv("MAX_LEVERAGE", "3"))
    RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    MIN_FREE_MARGIN_PCT = float(os.getenv("MIN_FREE_MARGIN_PCT", "50"))
    MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))

    # Exchange
    EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "binance")
    EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
    EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
    EXCHANGE_PASSWORD = os.getenv("EXCHANGE_PASSWORD", "")  # Para exchanges que lo requieren

    # Trading
    TRADING_MODE = os.getenv("TRADING_MODE", "dry_run")  # dry_run | live
    STAKE_CURRENCY = os.getenv("STAKE_CURRENCY", "USDT")
    STAKE_AMOUNT = float(os.getenv("STAKE_AMOUNT", "50"))
    MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "3"))
    TIMEFRAME = os.getenv("TIMEFRAME", "5m")

    # Estrategia: safe (original, multi-indicador) | trend (seguimiento de tendencia)
    STRATEGY = os.getenv("STRATEGY", "safe").lower()
    # Velas a pedir al exchange. EMA200 + indicadores consumen ~200, hacen falta de sobra.
    OHLCV_LIMIT = int(os.getenv("OHLCV_LIMIT", "500"))
    # Cada cuanto se vigilan stops/trailing y posiciones (segundos)
    CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

    # Parametros de TrendStrategy (ver src/trend_strategy.py)
    TREND_ENTRY_N = int(os.getenv("TREND_ENTRY_N", "55"))
    TREND_EXIT_N = int(os.getenv("TREND_EXIT_N", "20"))
    TREND_MOMENTUM_N = int(os.getenv("TREND_MOMENTUM_N", "90"))
    TREND_STOP_ATR = float(os.getenv("TREND_STOP_ATR", "2.0"))
    TREND_TRAIL_ATR = float(os.getenv("TREND_TRAIL_ATR", "3.0"))

    # Protecciones estilo Freqtrade (solo estrategia trend)
    COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "60"))
    STOPLOSS_GUARD_COUNT = int(os.getenv("STOPLOSS_GUARD_COUNT", "3"))

    # Pares a operar
    PAIR_WHITELIST = os.getenv(
        "PAIR_WHITELIST", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
    ).split(",")

    # Risk Management
    MAX_DRAWDOWN_PERCENT = float(os.getenv("MAX_DRAWDOWN_PERCENT", "10"))
    STOPLOSS = float(os.getenv("STOPLOSS", "-0.03"))
    TRAILING_STOP = os.getenv("TRAILING_STOP", "true").lower() == "true"
    TRAILING_STOP_POSITIVE = float(os.getenv("TRAILING_STOP_POSITIVE", "0.01"))
    TRAILING_STOP_ACTIVATION = float(os.getenv("TRAILING_STOP_ACTIVATION", "0.02"))

    # Telegram
    TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Base de datos local (SQLite)
    DB_PATH = os.getenv("DB_PATH", "data/trades.db")

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    @classmethod
    def is_live(cls):
        return cls.TRADING_MODE == "live"

    @classmethod
    def is_exness(cls):
        return cls.BROKER == "exness"

    @classmethod
    def validate(cls):
        errors = []
        if cls.is_live() and not cls.EXCHANGE_API_KEY:
            errors.append("EXCHANGE_API_KEY requerida para modo live")
        if cls.is_live() and not cls.EXCHANGE_API_SECRET:
            errors.append("EXCHANGE_API_SECRET requerida para modo live")
        if cls.STAKE_AMOUNT <= 0:
            errors.append("STAKE_AMOUNT debe ser mayor a 0")
        if cls.STRATEGY not in ("safe", "trend"):
            errors.append("STRATEGY debe ser 'safe' o 'trend'")
        if cls.MAX_OPEN_TRADES < 1:
            errors.append("MAX_OPEN_TRADES debe ser al menos 1")
        if errors:
            raise ValueError("Errores de configuracion:\n" + "\n".join(f"  - {e}" for e in errors))
