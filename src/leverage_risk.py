"""
Gestor de riesgo CON APALANCAMIENTO (Exness / MetaTrader 5).

A diferencia de spot, aqui una operacion mal dimensionada puede LIQUIDAR la cuenta.
Este modulo impone limites duros ANTES de enviar cualquier orden.

Formulas estandar del sector:
  - Riesgo por trade   : riesgo_usd = balance * (risk_pct / 100)
  - Tamano (lotes)     : lotes = riesgo_usd / (distancia_stop * valor_por_punto_por_lote)
  - Margen requerido   : margen = (lotes * contract_size * precio) / apalancamiento
  - Margin level %     : (equity / margen_usado) * 100   -> si baja mucho, STOP OUT
  - Margen libre       : equity - margen_usado

Reglas duras que aplica:
  1. Apalancamiento efectivo nunca supera MAX_LEVERAGE (aunque el broker permita 1:2000)
  2. Solo se arriesga RISK_PER_TRADE_PCT del capital por operacion
  3. No abre si el margen libre queda por debajo de MIN_FREE_MARGIN_PCT
  4. No abre si el margin level proyectado cae bajo el umbral seguro
  5. Se pausa al alcanzar MAX_DAILY_LOSS_PCT de perdida diaria
  6. SIEMPRE exige stop loss (sin stop, no hay operacion)
"""

import logging
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Margin level (%) por debajo del cual NO abrimos nada nuevo.
# Los brokers hacen margin call ~100% y stop out ~50-0%. Exigimos MUCHO margen.
SAFE_MARGIN_LEVEL = 300.0


@dataclass
class PositionPlan:
    """Resultado del calculo de una posicion antes de enviarla."""
    allowed: bool
    reason: str
    lots: float = 0.0
    risk_usd: float = 0.0
    margin_required: float = 0.0
    effective_leverage: float = 0.0
    stop_loss_price: float = 0.0
    projected_margin_level: float = 0.0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "lots": round(self.lots, 4),
            "risk_usd": round(self.risk_usd, 2),
            "margin_required": round(self.margin_required, 2),
            "effective_leverage": round(self.effective_leverage, 2),
            "stop_loss_price": round(self.stop_loss_price, 6),
            "projected_margin_level": round(self.projected_margin_level, 1),
        }


class LeverageRiskManager:
    """Reglas duras de apalancamiento. Nada se envia si esto dice que no."""

    def __init__(self, max_leverage: float = 3.0, risk_per_trade_pct: float = 1.0,
                 min_free_margin_pct: float = 50.0, max_daily_loss_pct: float = 3.0):
        self.max_leverage = max(1.0, float(max_leverage))
        self.risk_per_trade_pct = max(0.01, float(risk_per_trade_pct))
        self.min_free_margin_pct = float(min_free_margin_pct)
        self.max_daily_loss_pct = float(max_daily_loss_pct)

        self.day = datetime.now().date()
        self.daily_pnl = 0.0
        self.day_start_balance = 0.0
        self.is_paused = False
        self.pause_reason = ""

    # ------------------------------------------------------------------
    def plan_position(self, *, balance: float, equity: float, used_margin: float,
                      price: float, stop_loss_price: float, contract_size: float,
                      symbol: str = "", min_lot: float = 0.01, max_lot: float = 100.0,
                      lot_step: float = 0.01, broker_leverage: float = 0.0) -> PositionPlan:
        """
        Calcular si se puede abrir la posicion y con cuantos lotes.
        stop_loss_price es OBLIGATORIO: sin stop no operamos.

        Ojo con dos conceptos DISTINTOS:
          - self.max_leverage : NUESTRO tope de exposicion (limita el nominal)
          - broker_leverage   : el apalancamiento REAL de la cuenta (Exness puede dar 1:2000)
                                y es el que determina cuanto margen bloquea el broker.
        Si no se pasa broker_leverage, se asume el nuestro (conservador).
        """
        self._reset_day_if_needed(balance)

        # Apalancamiento real de la cuenta (para el margen). Nunca menor que el nuestro.
        margin_leverage = broker_leverage if broker_leverage and broker_leverage > 0 else self.max_leverage
        margin_leverage = max(margin_leverage, self.max_leverage)

        if self.is_paused:
            return PositionPlan(False, f"Pausado: {self.pause_reason}")

        if balance <= 0 or equity <= 0:
            return PositionPlan(False, "Balance/equity no valido")

        if price <= 0:
            return PositionPlan(False, "Precio no valido")

        # 6. Stop loss obligatorio
        if not stop_loss_price or stop_loss_price <= 0:
            return PositionPlan(False, "Sin stop loss: operacion rechazada")

        stop_distance = abs(price - stop_loss_price)
        if stop_distance <= 0:
            return PositionPlan(False, "Stop loss igual al precio de entrada")

        # 5. Limite de perdida diaria
        loss_limit = self.day_start_balance * (self.max_daily_loss_pct / 100.0)
        if self.day_start_balance > 0 and self.daily_pnl <= -loss_limit:
            self._pause(f"Perdida diaria maxima alcanzada ({self.max_daily_loss_pct}%)")
            return PositionPlan(False, self.pause_reason)

        # 2. Riesgo por operacion
        risk_usd = balance * (self.risk_per_trade_pct / 100.0)

        # Tamano por riesgo: perdida al tocar el stop == risk_usd
        # valor de 1 lote moviendose 1 unidad de precio = contract_size
        value_per_price_unit = contract_size
        if value_per_price_unit <= 0:
            return PositionPlan(False, "contract_size no valido")

        lots = risk_usd / (stop_distance * value_per_price_unit)

        # Ajustar al step del broker (redondeo hacia ABAJO: nunca arriesgar de mas)
        if lot_step > 0:
            lots = int(lots / lot_step) * lot_step
        lots = round(lots, 4)

        if lots < min_lot:
            return PositionPlan(
                False,
                f"Lote calculado {lots:.4f} < minimo {min_lot}. "
                f"Capital insuficiente para arriesgar solo {self.risk_per_trade_pct}% "
                f"con un stop de {stop_distance:.4f}.",
            )
        lots = min(lots, max_lot)

        notional = lots * contract_size * price

        # 1. Tope de apalancamiento efectivo
        effective_leverage = notional / equity if equity > 0 else float("inf")
        if effective_leverage > self.max_leverage:
            # Reducir lotes para respetar el tope en vez de rechazar
            max_notional = equity * self.max_leverage
            lots = max_notional / (contract_size * price)
            if lot_step > 0:
                lots = int(lots / lot_step) * lot_step
            lots = round(lots, 4)
            if lots < min_lot:
                return PositionPlan(
                    False,
                    f"Apalancamiento {effective_leverage:.1f}x supera el tope "
                    f"{self.max_leverage}x y reducir deja el lote bajo el minimo",
                )
            notional = lots * contract_size * price
            effective_leverage = notional / equity
            risk_usd = lots * stop_distance * value_per_price_unit

        # El margen lo determina el apalancamiento REAL de la cuenta, no nuestro tope
        margin_required = notional / margin_leverage

        # 3. Margen libre minimo
        free_margin_after = equity - used_margin - margin_required
        free_pct_after = (free_margin_after / equity * 100.0) if equity > 0 else 0.0
        if free_pct_after < self.min_free_margin_pct:
            return PositionPlan(
                False,
                f"Margen libre quedaria en {free_pct_after:.0f}% "
                f"(minimo {self.min_free_margin_pct:.0f}%)",
            )

        # 4. Margin level proyectado
        total_margin = used_margin + margin_required
        projected_level = (equity / total_margin * 100.0) if total_margin > 0 else float("inf")
        if projected_level < SAFE_MARGIN_LEVEL:
            return PositionPlan(
                False,
                f"Margin level proyectado {projected_level:.0f}% < seguro {SAFE_MARGIN_LEVEL:.0f}%",
            )

        return PositionPlan(
            allowed=True,
            reason="OK",
            lots=lots,
            risk_usd=risk_usd,
            margin_required=margin_required,
            effective_leverage=effective_leverage,
            stop_loss_price=stop_loss_price,
            projected_margin_level=projected_level,
        )

    # ------------------------------------------------------------------
    def register_close(self, profit: float):
        """Registrar el resultado de una operacion cerrada."""
        self.daily_pnl += profit
        loss_limit = self.day_start_balance * (self.max_daily_loss_pct / 100.0)
        if self.day_start_balance > 0 and self.daily_pnl <= -loss_limit:
            self._pause(f"Perdida diaria maxima alcanzada ({self.max_daily_loss_pct}%)")

    def check_open_positions(self, equity: float, used_margin: float) -> tuple[bool, str]:
        """Alerta si el margin level cae a zona peligrosa (riesgo de liquidacion)."""
        if used_margin <= 0:
            return True, "Sin posiciones abiertas"
        level = equity / used_margin * 100.0
        if level < 150:
            return False, f"PELIGRO: margin level {level:.0f}% - riesgo de liquidacion"
        if level < SAFE_MARGIN_LEVEL:
            return True, f"Aviso: margin level {level:.0f}% (bajo)"
        return True, f"Margin level {level:.0f}% OK"

    def resume(self):
        self.is_paused = False
        self.pause_reason = ""

    def get_stats(self) -> dict:
        return {
            "max_leverage": self.max_leverage,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "min_free_margin_pct": self.min_free_margin_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "daily_pnl": round(self.daily_pnl, 2),
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
        }

    # ------------------------------------------------------------------
    def _pause(self, reason: str):
        self.is_paused = True
        self.pause_reason = reason
        logger.warning("RIESGO APALANCADO - BOT PAUSADO: %s", reason)

    def _reset_day_if_needed(self, balance: float):
        today = datetime.now().date()
        if today > self.day:
            self.day = today
            self.daily_pnl = 0.0
            self.day_start_balance = balance
            if self.is_paused and "diaria" in self.pause_reason:
                self.resume()
                logger.info("Nuevo dia: limite de perdida diaria reiniciado")
        elif self.day_start_balance <= 0:
            self.day_start_balance = balance
