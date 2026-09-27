"""
Adaptador Exness via MetaTrader 5.

Exness NO tiene API REST propia: el unico camino oficial es la terminal MetaTrader 5.
El paquete 'MetaTrader5' de MetaQuotes es SOLO Windows, asi que este modulo soporta
dos modos y elige solo:

  1. NATIVO (Windows): import MetaTrader5  -> habla por IPC con la terminal local.
  2. REMOTO (Linux/Docker): import mt5linux -> habla por RPyC con la terminal que
     corre bajo Wine en el contenedor 'mt5'. Misma API.

Toda orden pasa OBLIGATORIAMENTE por LeverageRiskManager: sin stop loss y sin
respetar el tope de apalancamiento, no se envia nada.
"""

import os
import logging
from datetime import datetime

import pandas as pd

from src.config import Config
from src.leverage_risk import LeverageRiskManager

logger = logging.getLogger(__name__)

# Timeframes de MT5 por nombre (se resuelven contra el modulo ya importado)
_TF_NAMES = {
    "1m": "TIMEFRAME_M1", "5m": "TIMEFRAME_M5", "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30", "1h": "TIMEFRAME_H1", "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
}


class ExnessExchange:
    """Conexion a Exness a traves de MetaTrader 5, con reglas de apalancamiento."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.mt5 = None
        self.connected = False
        self.mode = None  # "native" | "remote"
        self.account = None
        # Cuentas CENT (USC/EUC/GBC/AUC): el balance viene en centimos.
        # 1 USD = 100 USC. Normalizamos a USD para que el riesgo salga bien.
        self.is_cent = False
        self.cent_factor = 1.0

        self.risk = LeverageRiskManager(
            max_leverage=Config.MAX_LEVERAGE,
            risk_per_trade_pct=Config.RISK_PER_TRADE_PCT,
            min_free_margin_pct=Config.MIN_FREE_MARGIN_PCT,
            max_daily_loss_pct=Config.MAX_DAILY_LOSS_PCT,
        )

    # ------------------------------------------------------------------
    def connect(self) -> tuple[bool, str]:
        """
        Conectar a la terminal MT5 (nativa o remota) y loguear en Exness.
        NUNCA lanza excepcion: devuelve (False, motivo) para que el bot siga vivo.
        """
        try:
            self.mt5, self.mode = self._import_mt5()
        except ImportError as e:
            return False, str(e)
        except Exception as e:
            # El servidor RPyC puede estar arrancando o no responder
            return False, f"Sin conexion con el servidor MT5: {type(e).__name__}: {e}"

        try:
            ok = self._initialize()
            if not ok:
                err = self._last_error()
                return False, f"No se pudo inicializar MT5: {err}"

            info = self.mt5.account_info()
            if info is None:
                return False, f"Login rechazado: {self._last_error()}"

            self.account = info
            self.connected = True

            # Detectar cuenta CENT por la divisa de la cuenta
            currency = str(getattr(info, "currency", "") or "").upper()
            self.is_cent = currency in ("USC", "EUC", "GBC", "AUC")
            self.cent_factor = 100.0 if self.is_cent else 1.0
            if self.is_cent:
                logger.info(
                    "Cuenta CENT detectada (%s): balance %.0f %s = %.2f USD reales. "
                    "El riesgo se calcula sobre el valor REAL.",
                    currency, float(info.balance), currency,
                    float(info.balance) / self.cent_factor,
                )

            logger.info(
                "Conectado a Exness (%s) | cuenta=%s | servidor=%s | apalancamiento broker=1:%s",
                self.mode, getattr(info, "login", "?"), getattr(info, "server", "?"),
                getattr(info, "leverage", "?"),
            )
            return True, "Conectado"
        except Exception as e:
            return False, f"Error conectando a Exness: {e}"

    def _import_mt5(self):
        """Elegir backend: nativo en Windows, remoto (Wine/RPyC) en Linux."""
        host = os.getenv("MT5_HOST", "").strip()
        if host:  # forzado a remoto
            try:
                import rpyc
                # RPyC corta las llamadas a los 30s por defecto. Las operaciones de
                # MT5 pueden tardar mas, y al cortar lanzaba "result expired" que
                # tumbaba el bot. Lo subimos a 60s (> INIT_TIMEOUT_MS).
                rpyc.core.protocol.DEFAULT_CONFIG["sync_request_timeout"] = 60
            except Exception:
                pass
            try:
                from mt5linux import MetaTrader5 as MT5Remote
                port = int(os.getenv("MT5_PORT", "8001"))
                return MT5Remote(host=host, port=port), "remote"
            except ImportError:
                raise ImportError(
                    "MT5_HOST definido pero falta 'mt5linux'. Instala: pip install mt5linux"
                )
        try:
            import MetaTrader5 as MT5Native
            return MT5Native, "native"
        except ImportError:
            pass
        try:
            from mt5linux import MetaTrader5 as MT5Remote
            return MT5Remote(host="mt5", port=int(os.getenv("MT5_PORT", "8001"))), "remote"
        except ImportError:
            raise ImportError(
                "No hay backend MT5. En Windows: pip install MetaTrader5. "
                "En Linux/Docker: pip install mt5linux y levanta el contenedor 'mt5'."
            )

    # Timeout de MT5 en ms. DEBE quedar por debajo del sync_request_timeout de
    # RPyC (ver _import_mt5), o RPyC corta antes con "TimeoutError: result expired".
    INIT_TIMEOUT_MS = 20000

    def _initialize(self) -> bool:
        login = Config.EXNESS_LOGIN
        password = Config.EXNESS_PASSWORD
        server = Config.EXNESS_SERVER
        path = Config.MT5_TERMINAL_PATH

        kwargs = {"timeout": self.INIT_TIMEOUT_MS}
        if path:
            kwargs["path"] = path
        if login:
            kwargs.update(login=int(login), password=password, server=server)

        return bool(self.mt5.initialize(**kwargs))

    def _last_error(self):
        try:
            return self.mt5.last_error()
        except Exception:
            return "desconocido"

    def shutdown(self):
        if self.mt5 and self.connected:
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            self.connected = False

    # ------------------------------------------------------------------
    #  DATOS DE CUENTA Y MERCADO
    # ------------------------------------------------------------------
    def get_balance_summary(self) -> dict:
        """Balance REAL de la cuenta Exness, incluido margen y riesgo de liquidacion."""
        if not self.connected:
            return {"error": "No conectado a Exness"}
        info = self.mt5.account_info()
        if info is None:
            return {"error": "Sin datos de cuenta"}

        f = self.cent_factor
        equity = float(info.equity) / f      # USD reales
        margin = float(info.margin) / f
        level = (equity / margin * 100.0) if margin > 0 else 0.0
        ok, msg = self.risk.check_open_positions(equity, margin)

        return {
            "currency": "USD" if self.is_cent else info.currency,
            "account_currency": info.currency,
            "cent_account": self.is_cent,
            "balance": float(info.balance) / f,
            "equity": equity,
            "free": float(info.margin_free) / f,
            "used": margin,
            "total": equity,
            "margin_level": round(level, 1),
            "broker_leverage": int(getattr(info, "leverage", 0) or 0),
            "profit": float(info.profit),
            "server": getattr(info, "server", ""),
            "login": getattr(info, "login", ""),
            "margin_ok": ok,
            "margin_msg": msg,
        }

    def list_markets(self, quote: str = "USD", limit: int = 300) -> list:
        """Simbolos disponibles en Exness (BTCUSD, XRPUSD, EURUSD...)."""
        if not self.connected:
            return []
        try:
            symbols = self.mt5.symbols_get()
            names = [s.name for s in symbols if quote in s.name]
            return sorted(names)[:limit]
        except Exception as e:
            logger.error("Error listando simbolos: %s", e)
            return []

    def fetch_ticker(self, symbol: str) -> dict:
        if not self.connected:
            return {}
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            self.mt5.symbol_select(symbol, True)
            tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return {}
        return {"symbol": symbol, "bid": tick.bid, "ask": tick.ask,
                "last": tick.last or tick.bid, "time": tick.time}

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 250) -> pd.DataFrame:
        """Velas historicas desde MT5, ya en el formato del bot."""
        if not self.connected:
            return pd.DataFrame()
        tf_name = _TF_NAMES.get(timeframe, "TIMEFRAME_M5")
        tf = getattr(self.mt5, tf_name)
        self.mt5.symbol_select(symbol, True)
        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, limit)
        if rates is None or len(rates) == 0:
            logger.error("Sin velas para %s: %s", symbol, self._last_error())
            return pd.DataFrame()

        # Al viajar por RPyC, el array estructurado de numpy pierde los nombres de
        # campo y las columnas quedan como 0,1,2... Los asignamos a mano.
        # Orden fijo de MT5: time, open, high, low, close, tick_volume, spread, real_volume
        MT5_COLS = ["time", "open", "high", "low", "close",
                    "tick_volume", "spread", "real_volume"]
        df = pd.DataFrame([tuple(r) for r in rates])
        if "time" not in df.columns:
            df.columns = MT5_COLS[:len(df.columns)]

        df["timestamp"] = pd.to_datetime(df["time"], unit="s")
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["timestamp", "open", "high", "low", "close", "volume"]]

    def get_positions(self) -> list:
        if not self.connected:
            return []
        positions = self.mt5.positions_get()
        if not positions:
            return []
        return [{
            "ticket": p.ticket, "symbol": p.symbol,
            "side": "buy" if p.type == 0 else "sell",
            "lots": p.volume, "entry_price": p.price_open,
            "current_price": p.price_current, "stop_loss": p.sl,
            "take_profit": p.tp, "profit": p.profit,
            "time": datetime.fromtimestamp(p.time).isoformat(),
        } for p in positions]

    # ------------------------------------------------------------------
    #  ORDENES (con reglas de apalancamiento obligatorias)
    # ------------------------------------------------------------------
    def plan_order(self, symbol: str, side: str, stop_loss_price: float) -> dict:
        """
        Calcular la posicion aplicando TODAS las reglas de riesgo.
        No envia nada: solo dice si se puede y con cuantos lotes.
        """
        if not self.connected:
            return {"allowed": False, "reason": "No conectado a Exness"}

        acc = self.mt5.account_info()
        info = self.mt5.symbol_info(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        if acc is None or info is None or tick is None:
            return {"allowed": False, "reason": f"Sin datos de {symbol}"}

        price = tick.ask if side == "buy" else tick.bid

        # En cuentas CENT el balance viene en centimos: lo pasamos a USD reales
        # para que el 1% de riesgo y el tope de apalancamiento sean correctos.
        f = self.cent_factor
        plan = self.risk.plan_position(
            balance=float(acc.balance) / f,
            equity=float(acc.equity) / f,
            used_margin=float(acc.margin) / f,
            price=float(price),
            stop_loss_price=float(stop_loss_price),
            contract_size=float(info.trade_contract_size),
            symbol=symbol,
            min_lot=float(info.volume_min),
            max_lot=float(info.volume_max),
            lot_step=float(info.volume_step),
            broker_leverage=float(getattr(acc, "leverage", 0) or 0),
        )
        out = plan.as_dict()
        out["symbol"] = symbol
        out["side"] = side
        out["price"] = float(price)
        out["cent_account"] = self.is_cent
        return out

    def create_order(self, symbol: str, side: str, stop_loss_price: float,
                     take_profit_price: float = 0.0) -> dict:
        """
        Enviar una orden a mercado CON stop loss obligatorio.
        En dry_run solo simula (no toca la cuenta).
        """
        plan = self.plan_order(symbol, side, stop_loss_price)
        if not plan.get("allowed"):
            logger.warning("Orden RECHAZADA por riesgo (%s %s): %s",
                           side, symbol, plan.get("reason"))
            return {"ok": False, "reason": plan.get("reason"), "plan": plan}

        # ==============================================================
        #  GUARDA FINAL (defensa en profundidad)
        #  Recalcula el nominal REAL justo antes de enviar la orden y lo
        #  compara con el equity de la cuenta. Si supera el tope, NO se
        #  envia. Esto es independiente del calculo anterior: si aquel
        #  fallara por lo que sea, esto lo detiene igual.
        # ==============================================================
        try:
            acc = self.mt5.account_info()
            info = self.mt5.symbol_info(symbol)
            # Deducir la divisa aqui mismo: si la deteccion de cuenta cent
            # fallara antes, esta guarda seguiria siendo correcta.
            divisa = str(getattr(acc, "currency", "") or "").upper()
            factor = 100.0 if divisa in ("USC", "EUC", "GBC", "AUC") else 1.0
            equity_usd = float(acc.equity) / factor
            nominal = float(plan["lots"]) * float(info.trade_contract_size) * float(plan["price"])
            apal_real = nominal / equity_usd if equity_usd > 0 else float("inf")
            tope = self.risk.max_leverage * 1.05  # 5% de holgura por redondeos
            if apal_real > tope:
                msg = (f"BLOQUEO DE SEGURIDAD: {plan['lots']} lotes de {symbol} serian "
                       f"{nominal:.2f} USD sobre {equity_usd:.2f} USD = {apal_real:.1f}x "
                       f"(tope {self.risk.max_leverage}x). Orden NO enviada.")
                logger.error(msg)
                return {"ok": False, "reason": msg, "plan": plan}
            logger.info("Guarda OK: %s %s | %.2f lotes | nominal %.2f USD | %.2fx de %.2f USD",
                        side.upper(), symbol, plan["lots"], nominal, apal_real, equity_usd)
        except Exception as e:
            msg = f"No se pudo verificar el riesgo antes de enviar ({e}). Orden cancelada por seguridad."
            logger.error(msg)
            return {"ok": False, "reason": msg, "plan": plan}

        if self.dry_run:
            logger.info("[DRY RUN] %s %s | lotes=%.2f | riesgo=%.2f | apal=%.2fx",
                        side.upper(), symbol, plan["lots"], plan["risk_usd"],
                        plan["effective_leverage"])
            return {"ok": True, "dry_run": True, "plan": plan}

        order_type = self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(plan["lots"]),
            "type": order_type,
            "price": float(plan["price"]),
            "sl": float(stop_loss_price),          # stop loss SIEMPRE
            "tp": float(take_profit_price) if take_profit_price else 0.0,
            "deviation": 20,
            "magic": 20260731,
            "comment": "SafeTraderBot",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", "?")
            comment = getattr(result, "comment", self._last_error())
            logger.error("Orden fallida %s %s: %s %s", side, symbol, code, comment)
            return {"ok": False, "reason": f"MT5 retcode {code}: {comment}", "plan": plan}

        logger.info("ORDEN EJECUTADA %s %s | lotes=%.2f | ticket=%s",
                    side.upper(), symbol, plan["lots"], result.order)
        return {"ok": True, "ticket": result.order, "plan": plan}

    def close_position(self, ticket: int) -> dict:
        """Cerrar una posicion abierta por su ticket."""
        if not self.connected:
            return {"ok": False, "reason": "No conectado"}
        if self.dry_run:
            return {"ok": True, "dry_run": True}

        positions = self.mt5.positions_get(ticket=ticket)
        if not positions:
            return {"ok": False, "reason": "Posicion no encontrada"}
        p = positions[0]
        tick = self.mt5.symbol_info_tick(p.symbol)
        is_buy = p.type == 0
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": 20,
            "magic": 20260731,
            "comment": "SafeTraderBot close",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            return {"ok": False, "reason": f"retcode {getattr(result,'retcode','?')}"}
        self.risk.register_close(float(p.profit))
        return {"ok": True, "profit": float(p.profit)}
