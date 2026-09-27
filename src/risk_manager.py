"""
Gestor de riesgo - La pieza mas importante del bot.
Controla cuanto arriesgar, cuando parar, y protege el capital.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, initial_balance: float, max_drawdown_pct: float = 10.0,
                 max_open_trades: int = 3, max_daily_losses: int = 3):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.peak_balance = initial_balance
        self.max_drawdown_pct = max_drawdown_pct
        self.max_open_trades = max_open_trades
        self.max_daily_losses = max_daily_losses

        self.open_trades = 0
        self.daily_losses = 0
        self.daily_losses_reset = datetime.now()
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.consecutive_losses = 0
        self.is_paused = False
        self.pause_until = None
        self.pause_reason = ""

    def can_open_trade(self, stake_amount: float) -> tuple[bool, str]:
        """Verificar si podemos abrir un nuevo trade."""
        self._reset_daily_if_needed()

        # Bot pausado por riesgo
        if self.is_paused:
            if self.pause_until and datetime.now() > self.pause_until:
                self.is_paused = False
                self.pause_reason = ""
                logger.info("Pausa de riesgo finalizada. Resumiendo operaciones.")
            else:
                return False, f"Bot pausado: {self.pause_reason}"

        # Max trades abiertos
        if self.open_trades >= self.max_open_trades:
            return False, f"Max trades abiertos ({self.max_open_trades})"

        # Max perdidas diarias
        if self.daily_losses >= self.max_daily_losses:
            self._pause(hours=12, reason=f"Max perdidas diarias alcanzadas ({self.max_daily_losses})")
            return False, "Max perdidas diarias - pausado 12h"

        # Perdidas consecutivas
        if self.consecutive_losses >= 3:
            self._pause(hours=6, reason="3 perdidas consecutivas")
            return False, "3 perdidas consecutivas - pausado 6h"

        # Drawdown maximo
        drawdown = self._current_drawdown()
        if drawdown >= self.max_drawdown_pct:
            self._pause(hours=24, reason=f"Drawdown maximo alcanzado ({drawdown:.1f}%)")
            return False, f"Drawdown {drawdown:.1f}% >= max {self.max_drawdown_pct}%"

        # Capital suficiente
        if stake_amount > self.current_balance * 0.5:
            return False, "Stake supera el 50% del balance disponible"

        if stake_amount > self.current_balance:
            return False, "Balance insuficiente"

        return True, "OK"

    def register_trade_open(self):
        """Registrar apertura de trade."""
        self.open_trades += 1
        self.total_trades += 1

    def register_trade_close(self, profit: float):
        """Registrar cierre de trade con su profit/loss."""
        self.open_trades = max(0, self.open_trades - 1)
        self.current_balance += profit

        if self.current_balance > self.peak_balance:
            self.peak_balance = self.current_balance

        if profit >= 0:
            self.winning_trades += 1
            self.consecutive_losses = 0
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
            self.daily_losses += 1

        logger.info(
            "Trade cerrado: P/L=%.2f | Balance=%.2f | Drawdown=%.1f%% | "
            "Win/Loss=%d/%d | Consecutivas=%d",
            profit, self.current_balance, self._current_drawdown(),
            self.winning_trades, self.losing_trades, self.consecutive_losses,
        )

    def get_position_size(self, stake_amount: float) -> float:
        """
        Calcular tamano de posicion ajustado al riesgo.
        Reduce el tamano si hay drawdown o perdidas consecutivas.
        """
        size = stake_amount

        # Reducir posicion si hay drawdown
        drawdown = self._current_drawdown()
        if drawdown > 5:
            size *= 0.5  # Mitad de posicion si drawdown > 5%
            logger.warning("Drawdown %.1f%% - reduciendo posicion a %.2f", drawdown, size)
        elif drawdown > 3:
            size *= 0.75
            logger.warning("Drawdown %.1f%% - reduciendo posicion a %.2f", drawdown, size)

        # Reducir si hay perdidas consecutivas
        if self.consecutive_losses >= 2:
            size *= 0.5
            logger.warning("%d perdidas consecutivas - reduciendo posicion", self.consecutive_losses)

        return max(size, 10)  # Minimo 10 USDT

    def _current_drawdown(self) -> float:
        if self.peak_balance == 0:
            return 0
        return ((self.peak_balance - self.current_balance) / self.peak_balance) * 100

    def _pause(self, hours: int, reason: str):
        self.is_paused = True
        self.pause_until = datetime.now() + timedelta(hours=hours)
        self.pause_reason = reason
        logger.warning("BOT PAUSADO %dh: %s", hours, reason)

    def _reset_daily_if_needed(self):
        now = datetime.now()
        if now.date() > self.daily_losses_reset.date():
            self.daily_losses = 0
            self.daily_losses_reset = now

    def get_stats(self) -> dict:
        win_rate = 0
        if self.total_trades > 0:
            win_rate = (self.winning_trades / self.total_trades) * 100

        # El balance inicial puede ser 0 si el broker aun no esta conectado
        profit_pct = 0.0
        if self.initial_balance:
            profit_pct = ((self.current_balance / self.initial_balance) - 1) * 100

        return {
            "balance": round(self.current_balance, 2),
            "initial_balance": round(self.initial_balance, 2),
            "profit_total": round(self.current_balance - self.initial_balance, 2),
            "profit_pct": round(profit_pct, 2),
            "drawdown_pct": round(self._current_drawdown(), 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(win_rate, 1),
            "consecutive_losses": self.consecutive_losses,
            "open_trades": self.open_trades,
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
        }
