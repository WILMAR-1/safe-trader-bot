"""
TrendStrategy - seguimiento de tendencia basado en evidencia publica.

Por que esta estrategia y no otra
---------------------------------
De todo lo publicado sobre trading sistematico, lo que mas se repite con
resultados reales, auditados y fuera de muestra es el SEGUIMIENTO DE TENDENCIA:

- Tortugas de Dennis/Eckhardt (1983-88): ruptura de canal Donchian, tamano por
  volatilidad ("N" = ATR), stops a 2N y salida por canal opuesto.
- Fondos CTA / managed futures (Winton, AQR, Man AHL...): decadas de track record.
- "Time Series Momentum" (Moskowitz, Ooi, Pedersen, 2012): el retorno de los
  ultimos 12 meses predice el siguiente en 58 mercados.
- "A Century of Evidence on Trend-Following" (Hurst, Ooi, Pedersen, 2017).
- Cripto: Liu & Tsyvinski (2021) documentan momentum de series temporales en BTC/ETH.
- "Volatility-Managed Portfolios" (Moreira & Muir, 2017): escalar la exposicion
  inversamente a la volatilidad mejora el ratio de Sharpe.

Lecciones comunes de esos casos, aplicadas aqui:
1. POCAS reglas y pocos parametros (menos sobreajuste).
2. Dejar correr las ganancias (sin take profit fijo) y cortar perdidas (stop ATR).
3. Tamano de posicion por riesgo/volatilidad, no por cantidad fija.
4. Win rate bajo (~35-45%) es NORMAL: gana por ganadores grandes.
5. Funciona mejor en marcos temporales altos (1h, 4h, 1d). En 5m las comisiones
   se comen la ventaja: usa TIMEFRAME=1h o superior.

Reglas
------
ENTRADA LARGA  : cierre > maximo de las ultimas N velas (ruptura Donchian)
                 Y EMA50 > EMA200 (regimen alcista)
                 Y momentum (retorno de M velas) > 0
                 Y ADX > umbral (hay tendencia, no rango)
ENTRADA CORTA  : espejo (solo brokers con cortos, p.ej. Exness)
SALIDA         : stop inicial a stop_atr * ATR
                 trailing "chandelier": maximo desde la entrada - trail_atr * ATR
                 o cierre < minimo de las ultimas exit_n velas (salida Tortuga)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TrendParams:
    entry_n: int = 55        # velas del canal de entrada (Tortugas sistema 2)
    exit_n: int = 20         # velas del canal de salida
    momentum_n: int = 90     # ventana del filtro de momentum (TSMOM)
    adx_min: float = 18.0    # por debajo el mercado esta en rango
    stop_atr: float = 2.0    # stop inicial = 2N (Tortugas)
    trail_atr: float = 3.0   # chandelier exit
    max_atr_pct: float = 8.0  # no entrar en volatilidad extrema (ATR/precio %)


class TrendStrategy:
    """Seguimiento de tendencia: ruptura Donchian + filtros de regimen y momentum."""

    name = "trend"

    def __init__(self, params: TrendParams | None = None):
        self.p = params or TrendParams()

    # ------------------------------------------------------------------
    #  INDICADORES PROPIOS
    # ------------------------------------------------------------------
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """Anadir columnas propias. Requiere que add_all_indicators ya se haya aplicado
        (usa ema_50, ema_200, atr, atr_pct, adx)."""
        p = self.p
        df = df.copy()
        # shift(1): el canal se calcula con velas ANTERIORES (sin mirar al futuro)
        df["dc_high"] = df["high"].rolling(p.entry_n).max().shift(1)
        df["dc_low"] = df["low"].rolling(p.entry_n).min().shift(1)
        df["dc_exit_low"] = df["low"].rolling(p.exit_n).min().shift(1)
        df["dc_exit_high"] = df["high"].rolling(p.exit_n).max().shift(1)
        df["momentum"] = df["close"] / df["close"].shift(p.momentum_n) - 1
        return df

    @property
    def min_candles(self) -> int:
        """Velas necesarias (tras indicadores) para evaluar la ultima."""
        return max(self.p.entry_n, self.p.momentum_n) + 2

    # ------------------------------------------------------------------
    #  ENTRADAS
    # ------------------------------------------------------------------
    def signals(self, df: pd.DataFrame, allow_short: bool = False) -> pd.Series:
        """Senal vectorizada por vela: 1 = largo, -1 = corto, 0 = nada.
        Unica fuente de verdad: la usan el bot en vivo y el backtest."""
        if "dc_high" not in df.columns:
            df = self.prepare(df)
        p = self.p
        trending = (df["adx"] > p.adx_min) & (df["atr_pct"] < p.max_atr_pct)
        long_ = (trending & (df["close"] > df["dc_high"])
                 & (df["ema_50"] > df["ema_200"]) & (df["momentum"] > 0))
        sig = long_.astype(int)
        if allow_short:
            short = (trending & (df["close"] < df["dc_low"])
                     & (df["ema_50"] < df["ema_200"]) & (df["momentum"] < 0))
            sig = sig - short.astype(int)
        return sig  # comparaciones con NaN dan False -> 0

    def signal(self, df: pd.DataFrame, allow_short: bool = False) -> str:
        """'buy', 'sell' o '' evaluando la ULTIMA vela (debe estar cerrada)."""
        if "dc_high" not in df.columns:
            df = self.prepare(df)
        if len(df) < self.min_candles:
            return ""
        s = int(self.signals(df.tail(1), allow_short).iloc[-1])
        if s == 0:
            return ""
        cur = df.iloc[-1]
        logger.info("TREND: ruptura %s (close %.4f, canal %.4f, mom %.1f%%, ADX %.0f)",
                    "alcista" if s > 0 else "bajista", cur["close"],
                    cur["dc_high"] if s > 0 else cur["dc_low"], cur["momentum"] * 100, cur["adx"])
        return "buy" if s > 0 else "sell"

    def should_buy(self, df: pd.DataFrame) -> bool:
        return self.signal(df, allow_short=False) == "buy"

    # ------------------------------------------------------------------
    #  SALIDAS
    # ------------------------------------------------------------------
    def initial_stop(self, entry_price: float, atr: float, side: str = "buy") -> float:
        dist = self.p.stop_atr * atr
        return entry_price - dist if side == "buy" else entry_price + dist

    def stop_price(self, df_since_entry: pd.DataFrame, entry_price: float,
                   entry_atr: float, side: str = "buy") -> float:
        """Stop vigente: el mas ajustado entre stop inicial y chandelier.
        Nunca se afloja (solo sube en largos / baja en cortos)."""
        stop = self.initial_stop(entry_price, entry_atr, side)
        if df_since_entry is None or df_since_entry.empty:
            return stop
        atr = float(df_since_entry["atr"].iloc[-1])
        if side == "buy":
            chandelier = float(df_since_entry["high"].max()) - self.p.trail_atr * atr
            return max(stop, chandelier)
        chandelier = float(df_since_entry["low"].min()) + self.p.trail_atr * atr
        return min(stop, chandelier)

    def check_exit(self, df: pd.DataFrame, entry_price: float, entry_time,
                   side: str = "buy", price: float | None = None) -> tuple[bool, str]:
        """Evaluar salida de una posicion abierta.
        Sin estado: el stop se reconstruye desde las velas (cerradas) posteriores a la entrada.
        price: precio actual en vivo; el stop se compara contra el y el canal contra el
        cierre de la ultima vela. Sin price se usa ese cierre para ambos."""
        if "dc_exit_low" not in df.columns:
            df = self.prepare(df)
        if len(df) < 2:
            return False, ""

        entry_time = pd.Timestamp(entry_time)
        before = df[df["timestamp"] <= entry_time]
        since = df[df["timestamp"] > entry_time]
        # ATR en la entrada: el de la ultima vela previa; si no hay, el mas antiguo disponible
        entry_atr = float(before["atr"].iloc[-1]) if not before.empty else float(df["atr"].iloc[0])

        cur = df.iloc[-1]
        close = float(cur["close"])
        price = close if price is None else float(price)
        stop = self.stop_price(since, entry_price, entry_atr, side)

        if side == "buy":
            pl = price / entry_price - 1
            if price <= stop:
                return True, f"{'STOP_ATR' if stop < entry_price else 'TRAILING_ATR'} ({pl:+.1%})"
            if not since.empty and not pd.isna(cur["dc_exit_low"]) and close < cur["dc_exit_low"]:
                return True, f"CANAL_SALIDA ({pl:+.1%})"
        else:
            pl = 1 - price / entry_price
            if price >= stop:
                return True, f"{'STOP_ATR' if stop > entry_price else 'TRAILING_ATR'} ({pl:+.1%})"
            if not since.empty and not pd.isna(cur["dc_exit_high"]) and close > cur["dc_exit_high"]:
                return True, f"CANAL_SALIDA ({pl:+.1%})"
        return False, ""

    # ------------------------------------------------------------------
    #  TAMANO DE POSICION (volatilidad / riesgo fijo, estilo Tortugas)
    # ------------------------------------------------------------------
    def position_size(self, equity: float, price: float, atr: float,
                      risk_pct: float, max_stake: float, min_stake: float = 10.0) -> float:
        """Nominal (en moneda de cotizacion) para que el stop inicial cueste risk_pct% del equity.
        Mas volatilidad -> posicion mas pequena. Limitado a max_stake y al equity (spot sin
        apalancamiento). Devuelve 0 si ni siquiera llega al minimo."""
        if equity <= 0 or price <= 0 or atr <= 0 or not math.isfinite(atr):
            return 0.0
        stop_pct = self.p.stop_atr * atr / price
        stake = (equity * risk_pct / 100.0) / stop_pct
        stake = min(stake, max_stake, equity)
        return stake if stake >= min_stake else 0.0

