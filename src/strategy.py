"""
Estrategia de trading propia - SafeStrategy.

Logica de entrada (COMPRA) - Requiere TODAS estas condiciones:
1. Tendencia alcista (EMA stack: 9 > 21 > 50)
2. Precio por encima de EMA 200 (macro alcista)
3. RSI saliendo de sobreventa (25-45) y subiendo
4. MACD histograma positivo y creciendo
5. Volumen por encima de la media (confirmacion)
6. ADX > 20 con DI+ > DI- (tendencia fuerte)
7. Precio debajo de Bollinger media (buen precio de entrada)
8. Volatilidad controlada (ATR < 5%)

Logica de salida (VENTA):
- RSI > 70 (sobrecomprado)
- MACD cruzando a la baja
- Precio en Bollinger superior
- O por stoploss / trailing stop / timeout
"""

import pandas as pd
import logging
from src.ml_engine import MLEngine

logger = logging.getLogger(__name__)


class SafeStrategy:
    """Estrategia conservadora multi-confirmacion + ML."""

    def __init__(self, use_ml: bool = True):
        self.use_ml = use_ml
        self.ml_engine = MLEngine(
            model_type="randomforest",
            retrain_hours=24,
            lookahead=6,
            threshold=0.01,
            min_confidence=0.65,
        ) if use_ml else None

    def train_ml(self, df: pd.DataFrame, symbol: str = "") -> dict:
        """Entrenar el modelo ML con datos historicos."""
        if not self.ml_engine:
            return {}
        return self.ml_engine.train(df, symbol)

    def should_buy(self, df: pd.DataFrame) -> bool:
        """Evaluar si debemos comprar basado en la ultima vela completa."""
        if len(df) < 2:
            return False

        current = df.iloc[-1]
        prev = df.iloc[-2]

        conditions = {
            # Tendencia alcista - EMA stack
            "ema_stack": current["ema_9"] > current["ema_21"] > current["ema_50"],

            # Macro alcista
            "above_ema200": current["close"] > current["ema_200"],

            # RSI saliendo de sobreventa
            "rsi_range": 25 < current["rsi"] < 45,
            "rsi_rising": current["rsi"] > prev["rsi"],

            # MACD positivo y creciendo
            "macd_positive": current["macd_hist"] > 0,
            "macd_growing": current["macd_hist"] > prev["macd_hist"],

            # Volumen confirmando
            "volume_ok": current["vol_ratio"] > 1.2,

            # ADX tendencia fuerte
            "adx_trending": current["adx"] > 20,
            "adx_bullish": current["adx_pos"] > current["adx_neg"],

            # Buen precio (debajo de Bollinger media)
            "good_price": current["close"] < current["bb_mid"],

            # Volatilidad controlada
            "low_volatility": current["atr_pct"] < 5,
        }

        all_met = all(conditions.values())

        # Si los indicadores tecnicos dicen comprar, consultar ML
        if all_met and self.ml_engine and self.ml_engine.is_ready:
            ml_result = self.ml_engine.predict(df)
            if not ml_result["should_buy"]:
                logger.info("Indicadores OK pero ML dice NO (confidence: %.1f%%)",
                            ml_result["confidence"] * 100)
                return False
            logger.info("SENAL DE COMPRA - Indicadores + ML confirman (confidence: %.1f%%)",
                        ml_result["confidence"] * 100)
        elif all_met:
            logger.info("SENAL DE COMPRA - Todas las condiciones cumplidas (sin ML)")
        else:
            failed = [name for name, met in conditions.items() if not met]
            logger.debug("Sin senal - faltan: %s", ", ".join(failed))

        return all_met

    def should_sell(self, df: pd.DataFrame, entry_price: float, current_profit: float) -> tuple[bool, str]:
        """
        Evaluar si debemos vender.
        Retorna (should_sell, reason).
        """
        if len(df) < 2:
            return False, ""

        current = df.iloc[-1]

        # === STOPLOSS: Nunca perder mas del 3% ===
        if current_profit <= -0.03:
            return True, "STOPLOSS (-3%)"

        # === Trailing stop: proteger ganancias ===
        if current_profit > 0.02:
            # Si ya ganamos 2%+ pero el profit empieza a caer
            if current["rsi"] > 60 and current["macd_hist"] < 0:
                return True, f"TRAILING_STOP (profit: {current_profit:.1%})"

        # === Take profit dinamico ===
        if current_profit > 0.08:
            return True, f"TAKE_PROFIT_HIGH ({current_profit:.1%})"

        if current_profit > 0.05 and current["rsi"] > 65:
            return True, f"TAKE_PROFIT_MID ({current_profit:.1%})"

        # === Senal tecnica de venta ===
        sell_signal = (
            current["rsi"] > 70
            and current["macd"] < current["macd_signal"]
            and current["close"] > current["bb_upper"] * 0.98
        )
        if sell_signal:
            return True, f"SELL_SIGNAL (RSI={current['rsi']:.0f})"

        return False, ""

    def get_dynamic_stoploss(self, current_profit: float) -> float:
        """
        Stop-loss dinamico segun el profit actual.
        Retorna el porcentaje de perdida maximo permitido.
        """
        if current_profit > 0.08:
            return -0.03  # Proteger minimo 5%
        elif current_profit > 0.05:
            return -0.03  # Proteger minimo 2%
        elif current_profit > 0.03:
            return -0.025  # Casi breakeven
        elif current_profit > 0.02:
            return -0.02  # Breakeven + buffer
        return -0.03  # Stoploss por defecto
