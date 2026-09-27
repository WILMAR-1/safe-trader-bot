"""
Protecciones al estilo Freqtrade ("protections"), que la comunidad usa para
evitar las rachas de perdidas mas tipicas de los bots:

- Cooldown: tras cerrar un par, no reentrar en el durante N minutos
  (evita el "whipsaw": entrar y salir una y otra vez en el mismo movimiento).
- StoplossGuard: si saltan demasiados stops en poco tiempo, el mercado ha
  cambiado de regimen -> bloquear todas las entradas un rato.

Sin estado persistente: si el bot se reinicia, las protecciones empiezan limpias.
"""

from __future__ import annotations

from datetime import datetime, timedelta


class Protections:
    def __init__(self, cooldown_minutes: int = 60, stoploss_guard_count: int = 3,
                 stoploss_guard_hours: int = 24, stoploss_guard_lock_hours: int = 12):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.sl_count = stoploss_guard_count
        self.sl_window = timedelta(hours=stoploss_guard_hours)
        self.sl_lock = timedelta(hours=stoploss_guard_lock_hours)
        self._last_exit: dict[str, datetime] = {}
        self._stops: list[datetime] = []
        self._global_lock_until: datetime | None = None

    def register_exit(self, symbol: str, is_stoploss: bool, now: datetime | None = None):
        now = now or datetime.now()
        self._last_exit[symbol] = now
        if not is_stoploss:
            return
        self._stops = [t for t in self._stops if now - t <= self.sl_window] + [now]
        if self.sl_count > 0 and len(self._stops) >= self.sl_count:
            self._global_lock_until = now + self.sl_lock
            self._stops.clear()

    def can_enter(self, symbol: str, now: datetime | None = None) -> tuple[bool, str]:
        now = now or datetime.now()
        if self._global_lock_until and now < self._global_lock_until:
            return False, f"StoplossGuard: demasiados stops, bloqueado hasta {self._global_lock_until:%H:%M}"
        last = self._last_exit.get(symbol)
        if last and now - last < self.cooldown:
            return False, f"Cooldown en {symbol} hasta {(last + self.cooldown):%H:%M}"
        return True, ""
