"""
Notificaciones por Telegram (opcional).
Solo envia mensajes al chat ID configurado, no recopila datos.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, enabled: bool = False, token: str = "", chat_id: str = ""):
        self.enabled = enabled
        self.token = token
        self.chat_id = chat_id
        self._bot = None

        if enabled and token and chat_id:
            try:
                import requests
                self._requests = requests
                logger.info("Notificaciones Telegram activadas")
            except ImportError:
                logger.warning("requests no instalado - Telegram desactivado")
                self.enabled = False
        else:
            self.enabled = False

    def send(self, message: str):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            self._requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }, timeout=10)
        except Exception as e:
            logger.error("Error enviando notificacion Telegram: %s", e)

    def notify_trade_open(self, symbol: str, price: float, amount: float, stake: float, **_):
        self.send(
            f"*COMPRA* {symbol}\n"
            f"Precio: `{price:.4f}`\n"
            f"Cantidad: `{amount:.6f}`\n"
            f"Invertido: `{stake:.2f} USDT`"
        )

    def notify_trade_close(self, symbol: str, price: float, profit: float,
                           profit_pct: float, reason: str, **_):
        emoji = "+" if profit >= 0 else ""
        self.send(
            f"*VENTA* {symbol} ({reason})\n"
            f"Precio: `{price:.4f}`\n"
            f"P/L: `{emoji}{profit:.2f} USDT ({emoji}{profit_pct:.1f}%)`"
        )

    def notify_stats(self, stats: dict):
        self.send(
            f"*Resumen del Bot*\n"
            f"Balance: `{stats['balance']:.2f} USDT`\n"
            f"Profit: `{stats['profit_pct']:.1f}%`\n"
            f"Drawdown: `{stats['drawdown_pct']:.1f}%`\n"
            f"Win Rate: `{stats['win_rate']:.0f}%`\n"
            f"Trades: `{stats['total_trades']}` (W:{stats['winning_trades']} L:{stats['losing_trades']})\n"
            f"Abiertos: `{stats['open_trades']}`"
        )

    def notify_risk_alert(self, message: str):
        self.send(f"*ALERTA DE RIESGO*\n{message}")

    def __getattr__(self, name):
        # Avisos que solo implementa TelegramCommander: sin Telegram no hacen nada
        if name.startswith("notify_"):
            return lambda *args, **kwargs: None
        raise AttributeError(name)
