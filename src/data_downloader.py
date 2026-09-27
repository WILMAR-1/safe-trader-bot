"""
Descargador de datos historicos reales de Binance.
Guarda datos en CSV locales para backtesting rapido y analisis.
"""

import os
import time
import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from src.config import Config

logger = logging.getLogger(__name__)

DATA_DIR = Path("data/market_data")


class DataDownloader:
    """Descarga y almacena datos historicos reales del exchange."""

    def __init__(self):
        import ccxt
        self.exchange = getattr(ccxt, Config.EXCHANGE_NAME)({
            "enableRateLimit": True,
        })
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def download_pair(self, symbol: str, timeframe: str = "5m",
                      days: int = 365) -> pd.DataFrame:
        """
        Descargar datos historicos de un par.
        Binance permite hasta 1000 velas por request.
        """
        print(f"  Descargando {symbol} ({timeframe}, {days} dias)...")

        all_data = []
        since = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        limit = 1000

        while True:
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
                if not ohlcv:
                    break

                all_data.extend(ohlcv)
                since = ohlcv[-1][0] + 1  # Siguiente vela

                # Si recibimos menos de 1000, ya no hay mas
                if len(ohlcv) < limit:
                    break

                time.sleep(0.5)  # Rate limit

            except Exception as e:
                logger.error("Error descargando %s: %s", symbol, e)
                break

        if not all_data:
            print(f"  No se obtuvieron datos para {symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.drop_duplicates(subset=["timestamp"], inplace=True)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)

        # Guardar en CSV
        safe_symbol = symbol.replace("/", "_")
        filepath = DATA_DIR / f"{safe_symbol}_{timeframe}_{days}d.csv"
        df.to_csv(filepath, index=False)

        print(f"  {symbol}: {len(df)} velas descargadas -> {filepath}")
        return df

    def download_all(self, pairs: list[str] = None, timeframe: str = "5m",
                     days: int = 365):
        """Descargar datos de todos los pares configurados."""
        if pairs is None:
            pairs = Config.PAIR_WHITELIST

        print("\n" + "=" * 65)
        print("  DESCARGANDO DATOS HISTORICOS REALES")
        print("=" * 65)
        print(f"  Exchange: {Config.EXCHANGE_NAME.upper()}")
        print(f"  Pares: {len(pairs)}")
        print(f"  Timeframe: {timeframe}")
        print(f"  Periodo: {days} dias")
        print()

        results = {}
        for pair in pairs:
            pair = pair.strip()
            df = self.download_pair(pair, timeframe, days)
            if not df.empty:
                results[pair] = df

        total_candles = sum(len(df) for df in results.values())
        print(f"\n  Total: {total_candles} velas descargadas de {len(results)} pares")
        print(f"  Guardados en: {DATA_DIR}/")
        print("=" * 65)

        return results

    def load_local(self, symbol: str, timeframe: str = "5m") -> pd.DataFrame:
        """Cargar datos desde CSV local (sin descargar)."""
        safe_symbol = symbol.replace("/", "_")
        files = list(DATA_DIR.glob(f"{safe_symbol}_{timeframe}_*.csv"))

        if not files:
            return pd.DataFrame()

        # Usar el mas reciente
        latest = max(files, key=os.path.getmtime)
        df = pd.read_csv(latest)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def get_stats(self):
        """Mostrar estadisticas de los datos locales."""
        files = list(DATA_DIR.glob("*.csv"))
        if not files:
            print("  No hay datos locales. Ejecuta: python main.py download")
            return

        print("\n  DATOS LOCALES DISPONIBLES:")
        print("  " + "-" * 55)
        print(f"  {'Archivo':<40} {'Velas':<10} {'Tamano':<10}")
        print("  " + "-" * 55)

        for f in sorted(files):
            df = pd.read_csv(f)
            size = f.stat().st_size / 1024
            print(f"  {f.name:<40} {len(df):<10} {size:.0f} KB")

        total_size = sum(f.stat().st_size for f in files) / (1024 * 1024)
        print("  " + "-" * 55)
        print(f"  Total: {len(files)} archivos, {total_size:.1f} MB")
