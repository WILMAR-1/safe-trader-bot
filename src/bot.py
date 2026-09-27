"""
Motor principal del bot de trading.
Coordina exchange, estrategia, riesgo, base de datos y notificaciones.
"""

import time
import signal
import logging
import pandas as pd
from datetime import datetime, timedelta, timezone
from src.config import Config
from src.exchange import Exchange
from src.indicators import add_all_indicators
from src.strategy import SafeStrategy
from src.trend_strategy import TrendParams, TrendStrategy
from src.protections import Protections
from src.risk_manager import RiskManager
from src.database import Database
from src.telegram_bot import TelegramCommander

logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(self):
        Config.validate()
        self._load_runtime()  # aplicar cambios guardados (pares, stake, max trades)

        # Broker: Binance (spot) o Exness (CFD apalancado via MT5)
        self.is_exness = Config.is_exness()
        self.exness = None
        if self.is_exness:
            from src.exness import ExnessExchange
            self.exness = ExnessExchange(dry_run=not Config.is_live())
            try:
                ok, msg = self.exness.connect()
            except Exception as e:
                # Blindaje: ningun fallo de MT5 puede tumbar el bot al arrancar
                ok, msg = False, f"{type(e).__name__}: {e}"
            self.exness_ready = ok
            if not ok:
                # NO tumbamos el bot: seguimos vivos, con Telegram funcionando,
                # y reintentamos cada ciclo. Asi el usuario puede hacer el login
                # en MT5 cuando pueda y el bot se conecta solo.
                logger.warning(
                    "Exness aun no disponible: %s | El bot seguira reintentando. "
                    "Haz el login en la terminal MT5 (VNC) y se conectara solo.", msg
                )
            self.exchange = self.exness
        else:
            self.exchange = Exchange()

        self.use_trend = Config.STRATEGY == "trend"
        if self.use_trend:
            self.strategy = TrendStrategy(TrendParams(
                entry_n=Config.TREND_ENTRY_N, exit_n=Config.TREND_EXIT_N,
                momentum_n=Config.TREND_MOMENTUM_N, stop_atr=Config.TREND_STOP_ATR,
                trail_atr=Config.TREND_TRAIL_ATR))
            self.protections = Protections(cooldown_minutes=Config.COOLDOWN_MINUTES,
                                           stoploss_guard_count=Config.STOPLOSS_GUARD_COUNT)
        else:
            self.strategy = SafeStrategy()
            self.protections = None
        logger.info("Estrategia activa: %s", Config.STRATEGY)
        self.db = Database(Config.DB_PATH)

        # Telegram Commander (notificaciones + comandos remotos)
        if Config.TELEGRAM_ENABLED and Config.TELEGRAM_TOKEN and Config.TELEGRAM_CHAT_ID:
            self.notifier = TelegramCommander(
                token=Config.TELEGRAM_TOKEN,
                chat_id=Config.TELEGRAM_CHAT_ID,
            )
            self.notifier.set_bot(self)
            self.notifier.start()
        else:
            from src.notifier import Notifier
            self.notifier = Notifier()

        # Determinar balance inicial
        if self.is_exness:
            summary = self.exness.get_balance_summary()
            if "error" in summary:
                initial_balance = 0.0
                logger.warning("Balance de Exness no disponible todavia (falta login en MT5)")
            else:
                initial_balance = float(summary.get("balance", 0) or 0)
                logger.info("Cuenta Exness %s | balance=%.2f %s | apalancamiento broker=1:%s",
                            summary.get("login"), initial_balance,
                            summary.get("currency"), summary.get("broker_leverage"))
        else:
            balance = self.exchange.get_balance()
            initial_balance = balance["free"].get(Config.STAKE_CURRENCY, 0)
            if not Config.is_live():
                initial_balance = 1000.0

        self.risk_manager = RiskManager(
            initial_balance=initial_balance,
            max_drawdown_pct=Config.MAX_DRAWDOWN_PERCENT,
            max_open_trades=Config.MAX_OPEN_TRADES,
        )

        # Restaurar trades abiertos desde la DB
        open_trades = self.db.get_open_trades()
        self.risk_manager.open_trades = len(open_trades)

        self.running = True
        self.start_time = datetime.now()
        self.last_stats_time = datetime.now()

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # =========================================================
    #  CONTROL EN TIEMPO REAL (panel web / Telegram)
    # =========================================================
    def pause(self, reason: str = "Pausado manualmente"):
        """Pausar: no abre trades nuevos, sigue cuidando los abiertos."""
        self.risk_manager.is_paused = True
        self.risk_manager.pause_reason = reason
        self.risk_manager.pause_until = None
        self._notify_control("PAUSADO", reason)
        return {"paused": True, "reason": reason}

    def resume(self):
        """Reanudar operaciones."""
        self.risk_manager.is_paused = False
        self.risk_manager.pause_reason = ""
        self.risk_manager.pause_until = None
        self._notify_control("REANUDADO", "Operaciones activas")
        return {"paused": False}

    def stop(self):
        """Detener el bot por completo (el loop termina)."""
        self.running = False
        self._notify_control("DETENIDO", "Bot detenido desde el panel")
        return {"running": False}

    def set_stake(self, amount: float) -> float:
        """Cambiar cuanto invierte por trade (en caliente)."""
        amount = float(amount)
        if amount <= 0:
            raise ValueError("El stake debe ser mayor que 0")
        Config.STAKE_AMOUNT = amount
        self._save_runtime()
        self._notify_control("STAKE", f"Ahora {amount:.2f} {Config.STAKE_CURRENCY} por trade")
        return amount

    def set_max_trades(self, n: int) -> int:
        """Cambiar cuantos mercados puede operar a la vez (en caliente)."""
        n = int(n)
        if n < 1:
            raise ValueError("Debe permitir al menos 1 trade")
        Config.MAX_OPEN_TRADES = n
        self.risk_manager.max_open_trades = n
        self._save_runtime()
        self._notify_control("MAX TRADES", f"Hasta {n} mercados a la vez")
        return n

    def set_pairs(self, pairs: list) -> list:
        """Cambiar en que mercados opera el bot (en caliente)."""
        cleaned = []
        for p in pairs:
            p = str(p).strip().upper()
            if not p:
                continue
            if "/" not in p:
                p = p + "/USDT"
            if p not in cleaned:
                cleaned.append(p)
        if not cleaned:
            raise ValueError("Debe haber al menos un par")
        Config.PAIR_WHITELIST = cleaned
        self._save_runtime()
        self._notify_control("PARES", ", ".join(cleaned))
        return cleaned

    def get_control_state(self) -> dict:
        """Estado actual de los controles (para el panel)."""
        return {
            "paused": self.risk_manager.is_paused,
            "pause_reason": self.risk_manager.pause_reason,
            "running": self.running,
            "stake_amount": Config.STAKE_AMOUNT,
            "max_open_trades": Config.MAX_OPEN_TRADES,
            "pairs": Config.PAIR_WHITELIST,
            "mode": "LIVE" if Config.is_live() else "DRY RUN",
        }

    def _notify_control(self, action: str, detail: str):
        """Registrar un cambio de control en el feed y Telegram."""
        try:
            from src.web import push_event
            push_event("control", {"action": action, "detail": detail})
        except Exception:
            pass
        try:
            self.notifier.send(f"*[CONTROL] {action}*\n{detail}")
        except Exception:
            pass

    def _runtime_path(self) -> str:
        return "data/runtime_config.json"

    def _save_runtime(self):
        import json
        try:
            with open(self._runtime_path(), "w", encoding="utf-8") as f:
                json.dump({
                    "STAKE_AMOUNT": Config.STAKE_AMOUNT,
                    "MAX_OPEN_TRADES": Config.MAX_OPEN_TRADES,
                    "PAIR_WHITELIST": Config.PAIR_WHITELIST,
                }, f)
        except Exception as e:
            logger.error("No se pudo guardar runtime config: %s", e)

    def _load_runtime(self):
        import json
        import os
        path = "data/runtime_config.json"
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("STAKE_AMOUNT"):
                Config.STAKE_AMOUNT = float(data["STAKE_AMOUNT"])
            if data.get("MAX_OPEN_TRADES"):
                Config.MAX_OPEN_TRADES = int(data["MAX_OPEN_TRADES"])
            if data.get("PAIR_WHITELIST"):
                Config.PAIR_WHITELIST = list(data["PAIR_WHITELIST"])
            logger.info("Runtime config cargada: %d pares, stake %.2f, max %d",
                        len(Config.PAIR_WHITELIST), Config.STAKE_AMOUNT, Config.MAX_OPEN_TRADES)
        except Exception as e:
            logger.error("No se pudo cargar runtime config: %s", e)

    def run(self):
        """Loop principal del bot."""
        mode = "LIVE" if Config.is_live() else "DRY RUN (simulacion)"
        logger.info("=" * 60)
        logger.info("  SafeTraderBot iniciado en modo %s", mode)
        if self.is_exness:
            logger.info("  Broker: EXNESS (MetaTrader 5) - %s",
                        "conectado" if getattr(self, "exness_ready", False)
                        else "ESPERANDO LOGIN EN MT5")
            logger.info("  Mercados: %s", ", ".join(Config.EXNESS_SYMBOLS))
            logger.info("  Apalancamiento max: %.0fx | Riesgo/trade: %.1f%% | "
                        "Perdida diaria max: %.1f%%",
                        Config.MAX_LEVERAGE, Config.RISK_PER_TRADE_PCT,
                        Config.MAX_DAILY_LOSS_PCT)
        else:
            logger.info("  Broker: %s", Config.EXCHANGE_NAME.upper())
            logger.info("  Pares: %s", ", ".join(Config.PAIR_WHITELIST))
        logger.info("  Balance: %.2f %s", self.risk_manager.initial_balance, Config.STAKE_CURRENCY)
        logger.info("  Timeframe: %s", Config.TIMEFRAME)
        logger.info("  Max trades: %d", Config.MAX_OPEN_TRADES)
        logger.info("  Stoploss: %.1f%%", Config.STOPLOSS * 100)
        logger.info("=" * 60)

        self.notifier.send(
            f"*Bot iniciado* ({mode})\n"
            f"Balance: `{self.risk_manager.initial_balance:.2f} {Config.STAKE_CURRENCY}`"
        )

        # Iniciar dashboard web
        from src.web import start_web
        start_web(self, host="0.0.0.0", port=8080)

        interval = self._timeframe_to_seconds(Config.TIMEFRAME)

        while self.running:
            try:
                self._tick()

                # Enviar stats cada hora
                if datetime.now() - self.last_stats_time > timedelta(hours=1):
                    stats = self.risk_manager.get_stats()
                    self.db.save_stats(stats)
                    self.notifier.notify_stats(stats)
                    self.last_stats_time = datetime.now()

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Error en el loop principal: %s", e, exc_info=True)
                self.notifier.notify_risk_alert(f"Error: {e}")
                time.sleep(30)  # Esperar antes de reintentar

            # Esperar hasta la proxima vela
            time.sleep(interval)

        self._shutdown()

    def _tick(self):
        """Un ciclo de analisis y ejecucion."""
        if self.is_exness:
            self._tick_exness()
            return

        # 1. Verificar trades abiertos (stoploss, trailing, exit signals)
        self._check_open_trades()

        # 2. Buscar nuevas oportunidades de compra
        self._scan_for_entries()

    # ==================================================================
    #  CICLO EXNESS (apalancado, largos y cortos, stop loss obligatorio)
    # ==================================================================
    def _tick_exness(self):
        """Ciclo para Exness/MT5. Las posiciones viven en MT5, no en la DB."""
        # 0. Si aun no hay conexion, reintentar (el usuario puede estar haciendo
        #    el login en MT5 justo ahora). El bot NO se cae por esto.
        if not getattr(self, "exness_ready", False):
            try:
                ok, msg = self.exness.connect()
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            if not ok:
                logger.info("Esperando a MT5... (%s)", msg)
                return
            self.exness_ready = True
            summary = self.exness.get_balance_summary()
            bal = summary.get("balance", 0)
            cur = summary.get("currency", "USD")
            logger.info("CONECTADO a Exness | balance=%.2f %s", bal, cur)
            self.risk_manager.initial_balance = bal or self.risk_manager.initial_balance
            self.notifier.send(
                f"*Conectado a EXNESS*\n"
                f"Cuenta: `{summary.get('login')}`\n"
                f"Balance: `{bal:.2f} {cur}`\n"
                f"Apalancamiento broker: `1:{summary.get('broker_leverage')}`\n"
                f"Modo: `{'LIVE' if Config.is_live() else 'DRY RUN'}`"
            )

        # 1. Vigilar riesgo de liquidacion en lo que ya esta abierto
        summary = self.exness.get_balance_summary()
        if "error" not in summary:
            if not summary.get("margin_ok", True):
                logger.warning("RIESGO: %s", summary.get("margin_msg"))
                self.notifier.notify_risk_alert(
                    f"Margin level {summary.get('margin_level')}% - riesgo de liquidacion"
                )

        if self.use_trend:
            self._manage_exness_positions()

        positions = self.exness.get_positions()
        open_symbols = {p["symbol"] for p in positions}

        # 2. Buscar entradas en los simbolos configurados
        for symbol in Config.EXNESS_SYMBOLS:
            symbol = symbol.strip()
            if not symbol or symbol in open_symbols:
                continue
            if len(positions) >= Config.MAX_OPEN_TRADES:
                break
            try:
                if self.protections:
                    ok, why = self.protections.can_enter(symbol)
                    if not ok:
                        logger.debug(why)
                        continue
                df = self.exness.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=Config.OHLCV_LIMIT)
                if df is None or df.empty or len(df) < 200:
                    continue
                df = self._closed_candles(df)
                df = add_all_indicators(df)
                if self.use_trend:
                    df = self.strategy.prepare(df)
                df.dropna(inplace=True)
                if len(df) < 2:
                    continue

                signal = (self.strategy.signal(df, allow_short=True) if self.use_trend
                          else self._exness_signal(df))
                if not signal:
                    continue

                price = float(df.iloc[-1]["close"])
                atr = float(df.iloc[-1]["atr"])
                if atr <= 0:
                    continue

                if self.use_trend:
                    # Stop OBLIGATORIO a stop_atr x ATR y sin TP: la salida la gestiona
                    # el trailing chandelier (_manage_exness_positions)
                    sl = self.strategy.initial_stop(price, atr, signal)
                    tp = 0.0
                # Stop loss OBLIGATORIO a 1.5 ATR
                elif signal == "buy":
                    sl = price - 1.5 * atr
                    tp = price + 3.0 * atr   # ratio 1:2
                else:
                    sl = price + 1.5 * atr
                    tp = price - 3.0 * atr

                result = self.exness.create_order(symbol, signal, sl, tp)
                if result.get("ok"):
                    plan = result.get("plan", {})
                    self.notifier.send(
                        f"*{signal.upper()} {symbol}*\n"
                        f"Lotes: `{plan.get('lots')}`\n"
                        f"Riesgo: `{plan.get('risk_usd')} USD`\n"
                        f"Apalancamiento: `{plan.get('effective_leverage')}x`\n"
                        f"Stop: `{sl:.4f}` | TP: `{tp:.4f}`"
                    )
                    try:
                        from src.web import push_event
                        push_event("buy" if signal == "buy" else "sell",
                                   {"symbol": symbol, "lots": plan.get("lots"),
                                    "leverage": plan.get("effective_leverage")})
                    except Exception:
                        pass
                    positions = self.exness.get_positions()
                else:
                    logger.info("Sin orden en %s: %s", symbol, result.get("reason"))
                    self._notify_rejection(symbol, signal, result.get("reason", "?"))

            except Exception as e:
                logger.error("Error analizando %s: %s", symbol, e)

    def _manage_exness_positions(self):
        """Trailing chandelier y salida por canal para posiciones Exness (estrategia trend).
        El stop inicial vive en MT5; aqui se cierra por mercado cuando el trailing lo pide."""
        from src.exness import BOT_MAGIC
        for pos in self.exness.get_positions():
            if pos.get("magic") != BOT_MAGIC:
                continue  # posicion manual: no es nuestra
            try:
                df = self.exness.fetch_ohlcv(pos["symbol"], Config.TIMEFRAME, limit=Config.OHLCV_LIMIT)
                if df is None or df.empty:
                    continue
                df = self.strategy.prepare(add_all_indicators(self._closed_candles(df)))
                df.dropna(subset=["atr"], inplace=True)
                exit_now, reason = self.strategy.check_exit(
                    df, pos["entry_price"], self._to_utc_naive(pos["time"]),
                    side=pos["side"], price=pos["current_price"])
                if not exit_now:
                    continue
                result = self.exness.close_position(pos["ticket"])
                if result.get("ok"):
                    is_stop = reason.startswith("STOP")
                    self.protections.register_exit(pos["symbol"], is_stop)
                    self.notifier.send(f"*CIERRE {pos['symbol']}*\nRazon: `{reason}`\n"
                                       f"P/L: `{pos.get('profit', 0):.2f}`")
                    logger.info("Cerrada %s (%s)", pos["symbol"], reason)
            except Exception as e:
                logger.error("Error gestionando posicion %s: %s", pos.get("symbol"), e)

    def _notify_rejection(self, symbol: str, side: str, reason: str):
        """Avisar por Telegram de una senal rechazada por riesgo (max. 1 aviso/6h por simbolo).
        Antes solo quedaba en el log y parecia que el bot no hacia nada."""
        last = getattr(self, "_rejection_notified", {})
        self._rejection_notified = last
        now = datetime.now()
        if symbol in last and now - last[symbol] < timedelta(hours=6):
            return
        last[symbol] = now
        self.notifier.send(f"*Senal {side.upper()} {symbol} RECHAZADA por riesgo*\n`{reason}`\n"
                           f"Usa /diagnostico para ver que simbolos puede operar tu cuenta.")

    def diagnose_exness(self, symbols: list | None = None) -> list[dict]:
        """Para cada simbolo: si la cuenta PUEDE operarlo con las reglas de riesgo actuales
        (con un stop tipico de la estrategia activa) y si hay senal ahora mismo."""
        out = []
        if not (self.is_exness and getattr(self, "exness_ready", False)):
            return [{"symbol": "-", "allowed": False, "reason": "Exness no conectado"}]
        for symbol in symbols or Config.EXNESS_SYMBOLS:
            row = {"symbol": symbol}
            try:
                df = self.exness.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=Config.OHLCV_LIMIT)
                if df is None or df.empty:
                    out.append({**row, "allowed": False, "reason": "sin velas (¿nombre del simbolo?)"})
                    continue
                df = add_all_indicators(self._closed_candles(df))
                if self.use_trend:
                    df = self.strategy.prepare(df)
                    row["signal"] = self.strategy.signal(df, allow_short=True) or "-"
                else:
                    row["signal"] = self._exness_signal(df.dropna()) or "-"
                atr = float(df.iloc[-1]["atr"])
                price = float(self.exness.fetch_ticker(symbol).get("ask") or df.iloc[-1]["close"])
                mult = self.strategy.p.stop_atr if self.use_trend else 1.5
                plan = self.exness.plan_order(symbol, "buy", price - mult * atr)
                row.update(allowed=bool(plan.get("allowed")), reason=plan.get("reason", ""),
                           lots=plan.get("lots"), risk_usd=plan.get("risk_usd"),
                           leverage=plan.get("effective_leverage"))
            except Exception as e:
                row.update(allowed=False, reason=f"{type(e).__name__}: {e}")
            out.append(row)
        return out

    @staticmethod
    def _closed_candles(df: pd.DataFrame) -> pd.DataFrame:
        """Quitar la ultima vela: sigue abierta y sus valores cambian hasta el cierre
        (operar con ella es la causa clasica de backtests que no se replican en vivo)."""
        return df.iloc[:-1].reset_index(drop=True) if len(df) > 1 else df

    @staticmethod
    def _to_utc_naive(t) -> pd.Timestamp:
        """Hora local ISO (como la guarda la DB/MT5) -> UTC sin zona, como las velas."""
        dt = datetime.fromisoformat(str(t))
        if dt.tzinfo is None:
            dt = dt.astimezone()  # interpretar como hora local
        return pd.Timestamp(dt.astimezone(timezone.utc).replace(tzinfo=None))

    def _exness_signal(self, df: pd.DataFrame) -> str:
        """
        Senal para Exness: permite LARGOS y CORTOS (aprovecha subidas y bajadas).
        Tendencia + confirmacion de momento.
        """
        cur, prev = df.iloc[-1], df.iloc[-2]
        adx_ok = cur["adx"] > 20

        up = cur["ema_9"] > cur["ema_21"] > cur["ema_50"] and cur["close"] > cur["ema_200"]
        dn = cur["ema_9"] < cur["ema_21"] < cur["ema_50"] and cur["close"] < cur["ema_200"]

        if up and adx_ok and 40 < cur["rsi"] < 62 and cur["rsi"] > prev["rsi"] \
                and cur["macd_hist"] > prev["macd_hist"] and cur["adx_pos"] > cur["adx_neg"]:
            return "buy"
        if dn and adx_ok and 38 < cur["rsi"] < 60 and cur["rsi"] < prev["rsi"] \
                and cur["macd_hist"] < prev["macd_hist"] and cur["adx_neg"] > cur["adx_pos"]:
            return "sell"
        return ""

    def _scan_for_entries(self):
        """Escanear todos los pares buscando senales de compra."""
        for symbol in Config.PAIR_WHITELIST:
            symbol = symbol.strip()
            try:
                # Verificar si ya tenemos un trade abierto en este par
                open_trades = self.db.get_open_trades()
                if any(t["symbol"] == symbol for t in open_trades):
                    continue

                if self.protections:
                    ok, why = self.protections.can_enter(symbol)
                    if not ok:
                        logger.debug(why)
                        continue

                # Verificar riesgo
                stake = self.risk_manager.get_position_size(Config.STAKE_AMOUNT)
                can_trade, reason = self.risk_manager.can_open_trade(stake)
                if not can_trade:
                    logger.debug("No se puede operar %s: %s", symbol, reason)
                    continue

                # Obtener datos y calcular indicadores
                df = self._get_dataframe(symbol)
                if df is None or len(df) < (self.strategy.min_candles if self.use_trend else 200):
                    continue

                # Evaluar estrategia
                if self.strategy.should_buy(df):
                    if self.use_trend:
                        # Tamano por volatilidad: el stop inicial cuesta RISK_PER_TRADE_PCT
                        # del balance; STAKE_AMOUNT (ya ajustado por el RiskManager) es el tope
                        last = df.iloc[-1]
                        stake = self.strategy.position_size(
                            self.risk_manager.current_balance, float(last["close"]),
                            float(last["atr"]), Config.RISK_PER_TRADE_PCT, stake)
                        if stake <= 0:
                            logger.info("%s: posicion por riesgo por debajo del minimo, se omite", symbol)
                            continue
                    self._execute_buy(symbol, stake, df)

            except Exception as e:
                logger.error("Error analizando %s: %s", symbol, e)

    def _check_open_trades(self):
        """Verificar stoploss y senales de salida en trades abiertos."""
        open_trades = self.db.get_open_trades()

        for trade in open_trades:
            try:
                symbol = trade["symbol"]
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker["last"]
                entry_price = trade["entry_price"]
                current_profit = (current_price - entry_price) / entry_price

                if self.use_trend:
                    # Seguimiento de tendencia: sin timeouts ni take profit, deja correr
                    df = self._get_dataframe(symbol, dropna=False)
                    if df is None or df.empty:
                        continue
                    should_sell, reason = self.strategy.check_exit(
                        df, entry_price, self._to_utc_naive(trade["entry_time"]),
                        price=current_price)
                    if should_sell:
                        self._execute_sell(trade, current_price, reason)
                    continue

                # Timeout: cerrar si lleva mas de 48h
                entry_time = datetime.fromisoformat(trade["entry_time"])
                if datetime.now() - entry_time > timedelta(hours=48):
                    self._execute_sell(trade, current_price, "TIMEOUT_48H")
                    continue

                # Timeout: cerrar si lleva mas de 24h sin ganancia
                if datetime.now() - entry_time > timedelta(hours=24) and current_profit < 0.005:
                    self._execute_sell(trade, current_price, "TIMEOUT_24H_NO_PROFIT")
                    continue

                # Stoploss dinamico
                max_loss = self.strategy.get_dynamic_stoploss(current_profit)
                if current_profit <= max_loss:
                    self._execute_sell(trade, current_price, f"STOPLOSS ({current_profit:.1%})")
                    continue

                # Senal tecnica de venta
                df = self._get_dataframe(symbol)
                if df is not None and len(df) > 10:
                    should_sell, reason = self.strategy.should_sell(df, entry_price, current_profit)
                    if should_sell:
                        self._execute_sell(trade, current_price, reason)

            except Exception as e:
                logger.error("Error verificando trade #%d (%s): %s",
                             trade["id"], trade["symbol"], e)

    def _execute_buy(self, symbol: str, stake: float, df: pd.DataFrame):
        """Ejecutar orden de compra."""
        try:
            current_price = df.iloc[-1]["close"]
            amount = stake / current_price
            min_amount = self.exchange.get_min_amount(symbol)

            if amount < min_amount:
                logger.warning("Cantidad %.6f menor que minimo %.6f para %s",
                               amount, min_amount, symbol)
                return

            order = self.exchange.create_buy_order(symbol, amount)

            trade_id = self.db.open_trade(
                symbol=symbol,
                entry_price=order["price"],
                amount=order["amount"],
                stake_amount=stake,
                entry_reason=f"{Config.STRATEGY}_strategy",
                order_id=order.get("id", ""),
            )

            self.risk_manager.register_trade_open()
            self.notifier.notify_trade_open(symbol, order["price"], order["amount"], stake)
            try:
                from src.web import push_event
                push_event("buy", {"symbol": symbol, "price": round(order["price"], 4), "stake": stake})
            except Exception:
                pass

            logger.info("COMPRA ejecutada: %s | Precio: %.4f | Cantidad: %.6f | Stake: %.2f",
                        symbol, order["price"], order["amount"], stake)

        except Exception as e:
            logger.error("Error ejecutando compra de %s: %s", symbol, e)

    def _execute_sell(self, trade: dict, current_price: float, reason: str):
        """Ejecutar orden de venta."""
        try:
            order = self.exchange.create_sell_order(trade["symbol"], trade["amount"])

            profit = (order["price"] - trade["entry_price"]) * trade["amount"]
            profit_pct = ((order["price"] / trade["entry_price"]) - 1) * 100

            self.db.close_trade(
                trade_id=trade["id"],
                exit_price=order["price"],
                exit_reason=reason,
                order_id=order.get("id", ""),
            )

            self.risk_manager.register_trade_close(profit)
            if self.protections:
                self.protections.register_exit(trade["symbol"], reason.startswith("STOP"))
            self.notifier.notify_trade_close(
                trade["symbol"], order["price"], profit, profit_pct, reason
            )
            try:
                from src.web import push_event
                ev = "sell" if profit >= 0 else "stoploss"
                push_event(ev, {"symbol": trade["symbol"], "pl": round(profit, 2),
                                "pct": round(profit_pct, 2), "reason": reason})
            except Exception:
                pass

            logger.info("VENTA ejecutada: %s | Precio: %.4f | P/L: %.2f (%.1f%%) | Razon: %s",
                        trade["symbol"], order["price"], profit, profit_pct, reason)

        except Exception as e:
            logger.error("Error ejecutando venta de %s: %s", trade["symbol"], e)

    def _get_dataframe(self, symbol: str, dropna: bool = True) -> pd.DataFrame:
        """Obtener velas CERRADAS y calcular indicadores.
        Con limit=250 la EMA200 dejaba ~50 velas tras dropna y el filtro de 200 velas
        impedia cualquier entrada; por eso se piden OHLCV_LIMIT (500) velas."""
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, Config.TIMEFRAME, limit=Config.OHLCV_LIMIT)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = add_all_indicators(self._closed_candles(df))
            if self.use_trend:
                df = self.strategy.prepare(df)
            if dropna:
                df.dropna(inplace=True)
            else:
                df.dropna(subset=["atr"], inplace=True)
            return df
        except Exception as e:
            logger.error("Error obteniendo datos de %s: %s", symbol, e)
            return None

    def _timeframe_to_seconds(self, tf: str) -> int:
        """Convertir timeframe string a segundos."""
        multipliers = {"m": 60, "h": 3600, "d": 86400}
        unit = tf[-1]
        value = int(tf[:-1])
        return value * multipliers.get(unit, 60)

    def _handle_shutdown(self, signum, frame):
        logger.info("Senal de apagado recibida. Cerrando...")
        self.running = False

    def _shutdown(self):
        stats = self.risk_manager.get_stats()
        self.db.save_stats(stats)
        self.notifier.notify_stats(stats)

        logger.info("=" * 60)
        logger.info("  Bot detenido")
        logger.info("  Balance final: %.2f %s", stats["balance"], Config.STAKE_CURRENCY)
        logger.info("  Profit: %.2f%%", stats["profit_pct"])
        logger.info("  Win Rate: %.0f%%", stats["win_rate"])
        logger.info("  Trades: %d (W:%d L:%d)", stats["total_trades"],
                     stats["winning_trades"], stats["losing_trades"])
        logger.info("=" * 60)

        self.notifier.send(
            f"*Bot detenido*\nBalance: `{stats['balance']:.2f}`\nProfit: `{stats['profit_pct']:.1f}%`"
        )
        self.db.close()
