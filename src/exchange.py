"""
Modulo de conexion al exchange usando CCXT.
Maneja todas las operaciones: datos de mercado, ordenes, balances.
"""

import ccxt
import logging
from datetime import datetime
from typing import Optional
from src.config import Config

logger = logging.getLogger(__name__)


class Exchange:
    def __init__(self):
        self.config = Config
        self.dry_run = not Config.is_live()
        self.dry_run_balance = 1000.0  # Balance virtual para simulacion
        self.dry_run_trades = []
        self._exchange = None
        self._public = None  # instancia publica cacheada (datos de mercado)

        if not self.dry_run:
            self._init_exchange()
        else:
            logger.info("Modo DRY RUN activo - usando balance virtual de %.2f %s",
                        self.dry_run_balance, Config.STAKE_CURRENCY)

    def _init_exchange(self):
        exchange_class = getattr(ccxt, Config.EXCHANGE_NAME, None)
        if exchange_class is None:
            raise ValueError(f"Exchange '{Config.EXCHANGE_NAME}' no soportado por CCXT")

        params = {
            "apiKey": Config.EXCHANGE_API_KEY,
            "secret": Config.EXCHANGE_API_SECRET,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",  # Solo spot, NUNCA futuros
            },
        }
        if Config.EXCHANGE_PASSWORD:
            params["password"] = Config.EXCHANGE_PASSWORD

        self._exchange = exchange_class(params)
        self._exchange.load_markets()
        logger.info("Conectado a %s en modo LIVE", Config.EXCHANGE_NAME.upper())

    def fetch_ohlcv(self, symbol: str, timeframe: str = "5m", limit: int = 200) -> list:
        """Obtener velas OHLCV del exchange."""
        if self.dry_run:
            # En dry_run tambien necesitamos datos reales del mercado
            exchange = getattr(ccxt, Config.EXCHANGE_NAME)({
                "enableRateLimit": True,
            })
            return exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    def fetch_ticker(self, symbol: str) -> dict:
        """Obtener precio actual."""
        if self.dry_run:
            exchange = getattr(ccxt, Config.EXCHANGE_NAME)({
                "enableRateLimit": True,
            })
            return exchange.fetch_ticker(symbol)
        return self._exchange.fetch_ticker(symbol)

    def get_balance(self) -> dict:
        """Obtener balance de la cuenta."""
        if self.dry_run:
            return {
                "total": {Config.STAKE_CURRENCY: self.dry_run_balance},
                "free": {Config.STAKE_CURRENCY: self.dry_run_balance},
                "used": {Config.STAKE_CURRENCY: 0},
            }
        return self._exchange.fetch_balance()

    def create_buy_order(self, symbol: str, amount: float, price: Optional[float] = None) -> dict:
        """Crear orden de compra."""
        if self.dry_run:
            ticker = self.fetch_ticker(symbol)
            entry_price = ticker["last"]
            order = {
                "id": f"dry_{datetime.now().timestamp()}",
                "symbol": symbol,
                "side": "buy",
                "type": "market",
                "amount": amount,
                "price": entry_price,
                "cost": amount * entry_price,
                "status": "closed",
                "timestamp": datetime.now().isoformat(),
                "dry_run": True,
            }
            self.dry_run_balance -= order["cost"]
            logger.info("[DRY RUN] COMPRA %s: %.6f @ %.2f (costo: %.2f %s)",
                        symbol, amount, entry_price, order["cost"], Config.STAKE_CURRENCY)
            return order

        # Orden real - siempre market para garantizar ejecucion
        return self._exchange.create_market_buy_order(symbol, amount)

    def create_sell_order(self, symbol: str, amount: float, price: Optional[float] = None) -> dict:
        """Crear orden de venta."""
        if self.dry_run:
            ticker = self.fetch_ticker(symbol)
            exit_price = ticker["last"]
            order = {
                "id": f"dry_{datetime.now().timestamp()}",
                "symbol": symbol,
                "side": "sell",
                "type": "market",
                "amount": amount,
                "price": exit_price,
                "cost": amount * exit_price,
                "status": "closed",
                "timestamp": datetime.now().isoformat(),
                "dry_run": True,
            }
            self.dry_run_balance += order["cost"]
            logger.info("[DRY RUN] VENTA %s: %.6f @ %.2f (recibido: %.2f %s)",
                        symbol, amount, exit_price, order["cost"], Config.STAKE_CURRENCY)
            return order

        return self._exchange.create_market_sell_order(symbol, amount)

    def _public_exchange(self):
        """Instancia publica cacheada para datos de mercado (precios, trades)."""
        if self._public is None:
            self._public = getattr(ccxt, Config.EXCHANGE_NAME)({"enableRateLimit": True})
        return self._public

    def get_balance_summary(self) -> dict:
        """Resumen del balance REAL de la cuenta (o virtual en dry_run)."""
        bal = self.get_balance()
        cur = Config.STAKE_CURRENCY
        free = (bal.get("free", {}) or {}).get(cur, 0) or 0
        used = (bal.get("used", {}) or {}).get(cur, 0) or 0
        total = (bal.get("total", {}) or {}).get(cur, 0) or 0
        # Otros activos en cartera (monedas que tienes ademas del USDT)
        assets = []
        totals = bal.get("total", {}) or {}
        if isinstance(totals, dict):
            for sym, amt in totals.items():
                if sym == cur or not amt or amt <= 0:
                    continue
                assets.append({"asset": sym, "amount": amt})
        assets = sorted(assets, key=lambda x: -x["amount"])[:12]
        return {"currency": cur, "free": free, "used": used, "total": total, "assets": assets}

    def fetch_recent_trades(self, symbol: str, limit: int = 12) -> list:
        """Operaciones REALES recientes del mercado (trades publicos)."""
        try:
            ex = self._public_exchange() if self.dry_run else self._exchange
            trades = ex.fetch_trades(symbol, limit=limit)
            return [{
                "symbol": symbol,
                "price": t.get("price"),
                "amount": t.get("amount"),
                "side": t.get("side"),
                "time": t.get("timestamp"),
            } for t in trades]
        except Exception as e:
            logger.error("Error obteniendo trades de %s: %s", symbol, e)
            return []

    def list_markets(self, quote: str = "USDT", limit: int = 300) -> list:
        """Listar mercados spot disponibles (para elegir en el panel)."""
        try:
            ex = self._public_exchange() if self.dry_run else self._exchange
            markets = ex.load_markets()
            syms = [
                s for s, m in markets.items()
                if m.get("quote") == quote and m.get("spot") and m.get("active")
            ]
            return sorted(syms)[:limit]
        except Exception as e:
            logger.error("Error listando mercados: %s", e)
            return []

    def get_min_amount(self, symbol: str) -> float:
        """Obtener cantidad minima para operar un par."""
        if self.dry_run:
            return 0.0001
        market = self._exchange.market(symbol)
        return market.get("limits", {}).get("amount", {}).get("min", 0.0001)
