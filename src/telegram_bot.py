"""
Bot de Telegram con comandos de administracion remota.
Controla el bot de trading completo desde tu telefono.

Comandos disponibles:
  /start      - Menu principal
  /status     - Estado del bot y balance
  /trades     - Trades abiertos con P/L en vivo
  /history    - Ultimos 10 trades cerrados
  /prices     - Precios actuales de todos los pares
  /profit     - Resumen de ganancias
  /risk       - Estado del gestor de riesgo
  /diagnostico - Que simbolos puede operar tu cuenta y por que no
  /pause      - Pausar el bot
  /resume     - Reanudar el bot
  /stop       - Detener el bot completamente
  /config     - Ver configuracion actual
  /help       - Lista de comandos
"""

import logging
import threading
import time
import requests
from datetime import datetime
from src.config import Config

logger = logging.getLogger(__name__)


class TelegramCommander:
    """Bot de Telegram con comandos bidireccionales."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.bot_instance = None
        self.running = False
        self.last_update_id = 0
        self._thread = None
        self._args = []

    def set_bot(self, bot):
        """Conectar con la instancia del bot de trading."""
        self.bot_instance = bot

    def start(self):
        """Iniciar el listener de comandos en un hilo separado."""
        self.running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._set_commands()
        logger.info("Telegram Commander iniciado - escuchando comandos")

    def stop(self):
        self.running = False

    def send(self, text: str, parse_mode: str = "Markdown"):
        """Enviar mensaje al chat."""
        try:
            requests.post(f"{self.base_url}/sendMessage", json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
            }, timeout=10)
        except Exception as e:
            logger.error("Error enviando mensaje Telegram: %s", e)

    def _set_commands(self):
        """Registrar comandos en el menu de Telegram."""
        try:
            requests.post(f"{self.base_url}/setMyCommands", json={
                "commands": [
                    {"command": "status", "description": "Estado del bot y balance"},
                    {"command": "trades", "description": "Trades abiertos con P/L"},
                    {"command": "history", "description": "Ultimos trades cerrados"},
                    {"command": "prices", "description": "Precios actuales"},
                    {"command": "profit", "description": "Resumen de ganancias"},
                    {"command": "risk", "description": "Estado del gestor de riesgo"},
                    {"command": "pause", "description": "Pausar el bot"},
                    {"command": "resume", "description": "Reanudar el bot"},
                    {"command": "stop", "description": "Detener el bot"},
                    {"command": "login", "description": "Conectar a Exness (MT5)"},
                    {"command": "pairs", "description": "Ver mercados activos"},
                    {"command": "addpair", "description": "Anadir mercado (ej: /addpair SOL)"},
                    {"command": "removepair", "description": "Quitar mercado (ej: /removepair SOL)"},
                    {"command": "setstake", "description": "Cambiar inversion por trade"},
                    {"command": "setmaxtrades", "description": "Cambiar max mercados a la vez"},
                    {"command": "config", "description": "Ver configuracion"},
                    {"command": "help", "description": "Lista de comandos"},
                ]
            }, timeout=10)
        except Exception:
            pass

    def _poll_loop(self):
        """Loop principal que escucha comandos de Telegram."""
        while self.running:
            try:
                response = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": self.last_update_id + 1, "timeout": 30},
                    timeout=35,
                )
                data = response.json()

                if data.get("ok") and data.get("result"):
                    for update in data["result"]:
                        self.last_update_id = update["update_id"]
                        self._handle_update(update)

            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                logger.error("Error en Telegram poll: %s", e)
                time.sleep(5)

    def _handle_update(self, update: dict):
        """Procesar un update de Telegram."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Solo responder a nuestro chat ID (seguridad)
        if chat_id != self.chat_id:
            logger.warning("Comando de chat no autorizado: %s", chat_id)
            return

        if not text.startswith("/"):
            return

        parts = text.split()
        command = parts[0].lower().replace("@", "").split("@")[0]
        self._args = parts[1:]  # argumentos del comando (ej: /setstake 50 -> ["50"])

        handlers = {
            "/start": self._cmd_start,
            "/status": self._cmd_status,
            "/trades": self._cmd_trades,
            "/history": self._cmd_history,
            "/prices": self._cmd_prices,
            "/profit": self._cmd_profit,
            "/risk": self._cmd_risk,
            "/diagnostico": self._cmd_diagnostico,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/stop": self._cmd_stop,
            "/config": self._cmd_config,
            "/ml": self._cmd_ml,
            "/login": self._cmd_login,
            "/pairs": self._cmd_pairs,
            "/addpair": self._cmd_addpair,
            "/removepair": self._cmd_removepair,
            "/setstake": self._cmd_setstake,
            "/setmaxtrades": self._cmd_setmaxtrades,
            "/help": self._cmd_help,
        }

        handler = handlers.get(command, self._cmd_unknown)
        try:
            handler()
        except Exception as e:
            self.send(f"Error ejecutando comando: `{e}`")
            logger.error("Error en comando %s: %s", command, e)

    # === COMANDOS ===

    def _cmd_start(self):
        mode = "LIVE (DINERO REAL)" if Config.is_live() else "DRY RUN (simulacion)"
        if Config.is_exness():
            broker = "EXNESS (MetaTrader 5)"
            mercados = ", ".join(Config.EXNESS_SYMBOLS)
            listo = getattr(self.bot_instance, "exness_ready", False) if self.bot_instance else False
            estado = "Conectado" if listo else "Esperando login en MT5"
        else:
            broker = Config.EXCHANGE_NAME.upper()
            mercados = ", ".join(Config.PAIR_WHITELIST)
            estado = "Conectado"
        self.send(
            f"*WILMAR SAFE TRADER BOT*\n\n"
            f"Broker: `{broker}`\n"
            f"Estado: `{estado}`\n"
            f"Modo: `{mode}`\n"
            f"Mercados: `{mercados}`\n\n"
            f"Usa /help para ver los comandos disponibles."
        )

    def _cmd_status(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        stats = self.bot_instance.risk_manager.get_stats()
        mode = "LIVE" if Config.is_live() else "DRY RUN"
        uptime = str(datetime.now() - self.bot_instance.start_time).split(".")[0]

        status = "PAUSADO" if stats["is_paused"] else "ACTIVO"

        # Si operamos con Exness pero aun no hay sesion en MT5, avisar claramente
        if getattr(self.bot_instance, "is_exness", False) and \
                not getattr(self.bot_instance, "exness_ready", False):
            self.send(
                "*Esperando a MetaTrader 5*\n\n"
                "El bot esta vivo pero AUN NO conectado a Exness.\n\n"
                "Falta iniciar sesion en la terminal MT5 (solo la primera vez):\n"
                f"  Login: `{Config.EXNESS_LOGIN}`\n"
                f"  Servidor: `{Config.EXNESS_SERVER}`\n\n"
                "Abre el escritorio MT5 y entra con tus datos.\n"
                "El bot se conectara solo en cuanto lo hagas."
            )
            return

        self.send(
            f"*Estado del Bot*\n\n"
            f"Estado: `{status}`\n"
            f"Modo: `{mode}`\n"
            f"Uptime: `{uptime}`\n\n"
            f"Balance: `{stats['balance']:.2f} USDT`\n"
            f"Profit: `{stats['profit_pct']:+.2f}%` (`{stats['profit_total']:+.2f} USDT`)\n"
            f"Drawdown: `{stats['drawdown_pct']:.1f}%`\n"
            f"Win Rate: `{stats['win_rate']:.0f}%`\n"
            f"Trades: `{stats['total_trades']}` (W:{stats['winning_trades']} L:{stats['losing_trades']})\n"
            f"Abiertos: `{stats['open_trades']}/{Config.MAX_OPEN_TRADES}`"
        )

    def _cmd_trades(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        trades = self.bot_instance.db.get_open_trades()
        if not trades:
            self.send("*Trades Abiertos*\n\nNo hay trades abiertos.")
            return

        lines = ["*Trades Abiertos*\n"]
        for t in trades:
            try:
                ticker = self.bot_instance.exchange.fetch_ticker(t["symbol"])
                current = ticker["last"]
                profit_pct = ((current / t["entry_price"]) - 1) * 100
                profit_usd = (current - t["entry_price"]) * t["amount"]
                sign = "+" if profit_pct >= 0 else ""

                entry_time = datetime.fromisoformat(t["entry_time"])
                duration = str(datetime.now() - entry_time).split(".")[0]

                lines.append(
                    f"*{t['symbol']}*\n"
                    f"  Entrada: `{t['entry_price']:.4f}`\n"
                    f"  Actual: `{current:.4f}`\n"
                    f"  P/L: `{sign}{profit_pct:.2f}%` (`{sign}{profit_usd:.2f} USDT`)\n"
                    f"  Duracion: `{duration}`\n"
                )
            except Exception:
                lines.append(f"*{t['symbol']}* - Error obteniendo precio\n")

        self.send("\n".join(lines))

    def _cmd_history(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        trades = self.bot_instance.db.get_trade_history(10)
        if not trades:
            self.send("*Historial*\n\nNo hay trades cerrados.")
            return

        lines = ["*Ultimos 10 Trades*\n"]
        for t in trades:
            sign = "+" if (t.get("profit_pct") or 0) >= 0 else ""
            lines.append(
                f"`{t['symbol']}` | "
                f"{t['entry_price']:.4f} -> {t.get('exit_price', 0):.4f} | "
                f"`{sign}{t.get('profit_pct', 0):.1f}%` | "
                f"{t.get('exit_reason', '-')}"
            )

        self.send("\n".join(lines))

    def _cmd_prices(self):
        lines = ["*Precios Actuales*\n"]

        for pair in Config.PAIR_WHITELIST:
            pair = pair.strip()
            try:
                if self.bot_instance:
                    ticker = self.bot_instance.exchange.fetch_ticker(pair)
                else:
                    import ccxt
                    ex = getattr(ccxt, Config.EXCHANGE_NAME)({"enableRateLimit": True})
                    ticker = ex.fetch_ticker(pair)

                change = ticker.get("percentage", 0) or 0
                sign = "+" if change >= 0 else ""
                lines.append(
                    f"`{pair}`: *${ticker['last']:.4f}* ({sign}{change:.2f}%)"
                )
            except Exception:
                lines.append(f"`{pair}`: Error")

        self.send("\n".join(lines))

    def _cmd_profit(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        stats = self.bot_instance.risk_manager.get_stats()
        trades = self.bot_instance.db.get_trade_history(100)

        # Calcular profit por par
        pair_profits = {}
        for t in trades:
            symbol = t["symbol"]
            if symbol not in pair_profits:
                pair_profits[symbol] = {"count": 0, "profit": 0, "wins": 0}
            pair_profits[symbol]["count"] += 1
            pair_profits[symbol]["profit"] += t.get("profit", 0)
            if (t.get("profit", 0)) >= 0:
                pair_profits[symbol]["wins"] += 1

        lines = [
            f"*Resumen de Ganancias*\n",
            f"Balance inicial: `{stats['initial_balance']:.2f} USDT`",
            f"Balance actual: `{stats['balance']:.2f} USDT`",
            f"Profit total: `{stats['profit_total']:+.2f} USDT` (`{stats['profit_pct']:+.2f}%`)\n",
            f"*Por Par:*",
        ]

        for pair, data in sorted(pair_profits.items(), key=lambda x: x[1]["profit"], reverse=True):
            wr = (data["wins"] / data["count"] * 100) if data["count"] > 0 else 0
            sign = "+" if data["profit"] >= 0 else ""
            lines.append(
                f"`{pair}`: {sign}{data['profit']:.2f} USDT | "
                f"{data['count']} trades | WR: {wr:.0f}%"
            )

        self.send("\n".join(lines))

    def _cmd_risk(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        stats = self.bot_instance.risk_manager.get_stats()
        rm = self.bot_instance.risk_manager

        status = "PAUSADO" if stats["is_paused"] else "NORMAL"

        self.send(
            f"*Gestor de Riesgo*\n\n"
            f"Estado: `{status}`\n"
            f"{'Razon: `' + stats['pause_reason'] + '`' if stats['is_paused'] else ''}\n"
            f"Drawdown actual: `{stats['drawdown_pct']:.1f}%` (max: {rm.max_drawdown_pct}%)\n"
            f"Perdidas consecutivas: `{stats['consecutive_losses']}`\n"
            f"Perdidas hoy: `{rm.daily_losses}/{rm.max_daily_losses}`\n"
            f"Trades abiertos: `{stats['open_trades']}/{rm.max_open_trades}`\n\n"
            f"*Limites:*\n"
            f"  Stoploss: `{Config.STOPLOSS:.1%}`\n"
            f"  Trailing stop: `{Config.TRAILING_STOP}`\n"
            f"  Max drawdown: `{rm.max_drawdown_pct}%`\n"
            f"  Max perdidas diarias: `{rm.max_daily_losses}`"
        )

    def _cmd_diagnostico(self):
        """/diagnostico [SIMBOLO ...] - Explica simbolo a simbolo si la cuenta puede operar."""
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return
        if not Config.is_exness():
            self.send("El diagnostico es para Exness. En Binance usa /risk.")
            return
        symbols = list(self._args) or None  # sin upper(): los simbolos cent acaban en "c"
        lines = [f"*Diagnostico* (estrategia `{Config.STRATEGY}`, {Config.TIMEFRAME})\n"]
        for r in self.bot_instance.diagnose_exness(symbols):
            if r.get("allowed"):
                lines.append(f"OK `{r['symbol']}`: {r['lots']} lotes, riesgo {r['risk_usd']}$, "
                             f"{r['leverage']}x | senal: `{r.get('signal', '-')}`")
            else:
                lines.append(f"NO `{r['symbol']}`: {r.get('reason', '')}")
        lines.append("\nSi todos salen NO, tu saldo no alcanza el lote minimo con el riesgo "
                     "configurado. Prueba simbolos con lote pequeno (p.ej. `/diagnostico EURUSDc XAUUSDc`) "
                     "y anadelos con /addpair.")
        self.send("\n".join(lines))

    def _cmd_pause(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        self.bot_instance.risk_manager.is_paused = True
        self.bot_instance.risk_manager.pause_reason = "Pausado manualmente via Telegram"
        self.bot_instance.risk_manager.pause_until = None  # Indefinido hasta /resume
        self.send("*Bot PAUSADO*\nNo se abriran nuevos trades.\nTrades abiertos seguiran siendo monitoreados.\nUsa /resume para reanudar.")

    def _cmd_resume(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        self.bot_instance.risk_manager.is_paused = False
        self.bot_instance.risk_manager.pause_reason = ""
        self.bot_instance.risk_manager.pause_until = None
        self.send("*Bot REANUDADO*\nOperaciones activas nuevamente.")

    def _cmd_stop(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        stats = self.bot_instance.risk_manager.get_stats()
        self.send(
            f"*Bot DETENIDO*\n\n"
            f"Balance final: `{stats['balance']:.2f} USDT`\n"
            f"Profit: `{stats['profit_pct']:+.2f}%`\n\n"
            f"Para reiniciar usa Docker:\n`docker-compose up -d`"
        )
        self.bot_instance.running = False

    def _cmd_config(self):
        mode = "LIVE" if Config.is_live() else "DRY RUN"
        self.send(
            f"*Configuracion Actual*\n\n"
            f"Modo: `{mode}`\n"
            f"Exchange: `{Config.EXCHANGE_NAME.upper()}`\n"
            f"Timeframe: `{Config.TIMEFRAME}`\n"
            f"Stake: `{Config.STAKE_AMOUNT} {Config.STAKE_CURRENCY}`\n"
            f"Max trades: `{Config.MAX_OPEN_TRADES}`\n"
            f"Stoploss: `{Config.STOPLOSS:.1%}`\n"
            f"Trailing: `{Config.TRAILING_STOP}`\n\n"
            f"*Pares:*\n`{', '.join(Config.PAIR_WHITELIST)}`"
        )

    def _cmd_login(self):
        """Conectar a Exness/MT5 desde el movil, sin tocar la PC."""
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return
        if not getattr(self.bot_instance, "is_exness", False):
            self.send("Este comando solo aplica con `BROKER=exness`.")
            return
        if getattr(self.bot_instance, "exness_ready", False):
            self.send("*Ya estas conectado a Exness.* Usa /status para ver el balance.")
            return

        self.send(
            f"*Conectando a Exness...*\n"
            f"Cuenta: `{Config.EXNESS_LOGIN}`\n"
            f"Servidor: `{Config.EXNESS_SERVER}`\n\n"
            f"Puede tardar hasta 3 minutos. Te aviso al terminar."
        )

        def _worker():
            try:
                ok, msg = self.bot_instance.exness.connect()
                if not ok:
                    self.send(
                        f"*No se pudo conectar*\n`{msg}`\n\n"
                        f"Si el error es `IPC timeout`, MetaTrader necesita que se "
                        f"inicie sesion una vez desde su ventana grafica. "
                        f"Vuelve a intentar con /login mas tarde."
                    )
                    return
                self.bot_instance.exness_ready = True
                s = self.bot_instance.exness.get_balance_summary()
                bal = s.get("balance", 0)
                cur = s.get("currency", "USD")
                self.bot_instance.risk_manager.initial_balance = bal or 0
                self.bot_instance.risk_manager.current_balance = bal or 0
                self.send(
                    f"*CONECTADO A EXNESS*\n\n"
                    f"Cuenta: `{s.get('login')}`\n"
                    f"Balance: `{bal:.2f} {cur}`\n"
                    f"Margen libre: `{s.get('free', 0):.2f}`\n"
                    f"Apalancamiento broker: `1:{s.get('broker_leverage')}`\n"
                    f"Modo: `{'LIVE (dinero real)' if Config.is_live() else 'DRY RUN'}`\n\n"
                    f"El bot ya opera. Usa /pause para detenerlo."
                )
            except Exception as e:
                self.send(f"*Error al conectar*\n`{type(e).__name__}: {e}`")

        threading.Thread(target=_worker, daemon=True).start()

    def _cmd_pairs(self):
        self.send(
            f"*Mercados activos* ({len(Config.PAIR_WHITELIST)})\n\n"
            f"`{', '.join(Config.PAIR_WHITELIST)}`\n\n"
            f"Anadir: `/addpair SOL`\n"
            f"Quitar: `/removepair SOL`"
        )

    def _cmd_addpair(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return
        if not self._args:
            self.send("Uso: `/addpair SOL`  o  `/addpair SOL/USDT`")
            return
        try:
            new = self._args[0].upper()
            if "/" not in new:
                new = new + "/USDT"
            pairs = list(Config.PAIR_WHITELIST)
            if new in pairs:
                self.send(f"`{new}` ya esta en la lista.")
                return
            pairs.append(new)
            result = self.bot_instance.set_pairs(pairs)
            self.send(f"*Mercado anadido:* `{new}`\n\nAhora opera: `{', '.join(result)}`")
        except Exception as e:
            self.send(f"Error: `{e}`")

    def _cmd_removepair(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return
        if not self._args:
            self.send("Uso: `/removepair SOL`")
            return
        try:
            target = self._args[0].upper()
            if "/" not in target:
                target = target + "/USDT"
            pairs = [p for p in Config.PAIR_WHITELIST if p != target]
            if len(pairs) == len(Config.PAIR_WHITELIST):
                self.send(f"`{target}` no estaba en la lista.")
                return
            if not pairs:
                self.send("No puedes quitar el ultimo mercado. Anade otro primero.")
                return
            result = self.bot_instance.set_pairs(pairs)
            self.send(f"*Mercado quitado:* `{target}`\n\nAhora opera: `{', '.join(result)}`")
        except Exception as e:
            self.send(f"Error: `{e}`")

    def _cmd_setstake(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return
        if not self._args:
            self.send(f"Uso: `/setstake 50`\nActual: `{Config.STAKE_AMOUNT} {Config.STAKE_CURRENCY}`")
            return
        try:
            val = self.bot_instance.set_stake(float(self._args[0]))
            self.send(f"*Stake actualizado*\nAhora cada trade usa `{val:.2f} {Config.STAKE_CURRENCY}`")
        except Exception as e:
            self.send(f"Valor invalido. Ej: `/setstake 50` ({e})")

    def _cmd_setmaxtrades(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return
        if not self._args:
            self.send(f"Uso: `/setmaxtrades 3`\nActual: `{Config.MAX_OPEN_TRADES}`")
            return
        try:
            n = self.bot_instance.set_max_trades(int(self._args[0]))
            self.send(f"*Max trades actualizado*\nHasta `{n}` mercados a la vez.")
        except Exception as e:
            self.send(f"Valor invalido. Ej: `/setmaxtrades 3` ({e})")

    def _cmd_ml(self):
        if not self.bot_instance:
            self.send("Bot no iniciado.")
            return

        if hasattr(self.bot_instance.strategy, 'ml_engine') and self.bot_instance.strategy.ml_engine:
            ml = self.bot_instance.strategy.ml_engine
            stats = ml.get_stats()
            self.send(
                f"*Machine Learning*\n\n"
                f"Estado: `{'Listo' if stats['is_ready'] else 'No entrenado'}`\n"
                f"Modelo: `{stats['model_type']}`\n"
                f"Train accuracy: `{stats['train_accuracy']:.1f}%`\n"
                f"Test accuracy: `{stats['test_accuracy']:.1f}%`\n"
                f"Confianza minima: `{stats['min_confidence']:.0%}`\n"
                f"Predicciones hechas: `{stats['predictions_made']}`\n"
                f"Ultimo entrenamiento: `{stats['last_train'] or 'Nunca'}`\n"
                f"Necesita retraining: `{'Si' if stats['needs_retrain'] else 'No'}`"
            )
        else:
            self.send("*Machine Learning*\n\nML no esta activado.")

    def _cmd_help(self):
        self.send(
            "*Comandos Disponibles*\n\n"
            "*Informacion:*\n"
            "/status - Estado del bot y balance\n"
            "/trades - Trades abiertos con P/L\n"
            "/history - Ultimos 10 trades\n"
            "/prices - Precios actuales\n"
            "/profit - Resumen de ganancias por par\n"
            "/risk - Estado del gestor de riesgo\n"
            "/diagnostico - Por que no opera (simbolo a simbolo)\n"
            "/config - Ver configuracion\n"
            "/ml - Estado del Machine Learning\n\n"
            "*Control:*\n"
            "/pause - Pausar (no abre trades nuevos)\n"
            "/resume - Reanudar operaciones\n"
            "/stop - Detener el bot\n\n"
            "*Configuracion en vivo:*\n"
            "/pairs - Ver mercados activos\n"
            "/addpair SOL - Anadir un mercado\n"
            "/removepair SOL - Quitar un mercado\n"
            "/setstake 50 - Cambiar inversion por trade\n"
            "/setmaxtrades 3 - Cambiar max mercados a la vez\n"
        )

    def _cmd_unknown(self):
        self.send("Comando no reconocido. Usa /help para ver comandos disponibles.")

    # === NOTIFICACIONES (compatibilidad con el notifier anterior) ===

    def notify_trade_open(self, symbol: str, price: float, amount: float, stake: float):
        self.send(
            f"*COMPRA* {symbol}\n"
            f"Precio: `{price:.4f}`\n"
            f"Cantidad: `{amount:.6f}`\n"
            f"Invertido: `{stake:.2f} USDT`"
        )

    def notify_trade_close(self, symbol: str, price: float, profit: float,
                           profit_pct: float, reason: str):
        sign = "+" if profit >= 0 else ""
        self.send(
            f"*VENTA* {symbol} ({reason})\n"
            f"Precio: `{price:.4f}`\n"
            f"P/L: `{sign}{profit:.2f} USDT ({sign}{profit_pct:.1f}%)`"
        )

    def notify_stats(self, stats: dict):
        self.send(
            f"*Resumen Horario*\n"
            f"Balance: `{stats['balance']:.2f} USDT`\n"
            f"Profit: `{stats['profit_pct']:.1f}%`\n"
            f"Drawdown: `{stats['drawdown_pct']:.1f}%`\n"
            f"Win Rate: `{stats['win_rate']:.0f}%`\n"
            f"Trades: `{stats['total_trades']}` (W:{stats['winning_trades']} L:{stats['losing_trades']})\n"
            f"Abiertos: `{stats['open_trades']}`"
        )

    def notify_risk_alert(self, message: str):
        self.send(f"*ALERTA DE RIESGO*\n{message}")
