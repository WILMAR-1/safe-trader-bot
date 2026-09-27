"""
Motor de backtesting propio.
Prueba la estrategia contra datos historicos para validar resultados.
"""

import logging
import pandas as pd
from datetime import datetime
from src.exchange import Exchange
from src.indicators import add_all_indicators
from src.strategy import SafeStrategy
from src.config import Config

logger = logging.getLogger(__name__)


class BacktestResult:
    def __init__(self):
        self.trades = []
        self.initial_balance = 0
        self.final_balance = 0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.max_drawdown = 0
        self.best_trade = 0
        self.worst_trade = 0
        self.avg_profit = 0
        self.total_profit_pct = 0
        self.win_rate = 0
        self.avg_duration_minutes = 0
        self.sharpe_ratio = 0
        self.profit_factor = 0

    def summary(self) -> str:
        lines = [
            "",
            "=" * 65,
            "  RESULTADOS DEL BACKTESTING",
            "=" * 65,
            f"  Periodo:           {self.period}",
            f"  Pares:             {', '.join(self.pairs)}",
            f"  Timeframe:         {self.timeframe}",
            "-" * 65,
            f"  Balance inicial:   {self.initial_balance:.2f} USDT",
            f"  Balance final:     {self.final_balance:.2f} USDT",
            f"  Profit total:      {self.final_balance - self.initial_balance:+.2f} USDT ({self.total_profit_pct:+.2f}%)",
            "-" * 65,
            f"  Total trades:      {self.total_trades}",
            f"  Ganadores:         {self.winning_trades} ({self.win_rate:.1f}%)",
            f"  Perdedores:        {self.losing_trades}",
            f"  Mejor trade:       {self.best_trade:+.2f}%",
            f"  Peor trade:        {self.worst_trade:+.2f}%",
            f"  Promedio P/L:      {self.avg_profit:+.2f}%",
            f"  Duracion prom:     {self.avg_duration_minutes:.0f} min",
            "-" * 65,
            f"  Max drawdown:      {self.max_drawdown:.2f}%",
            f"  Sharpe ratio:      {self.sharpe_ratio:.2f}",
            f"  Profit factor:     {self.profit_factor:.2f}",
            "=" * 65,
            "",
        ]

        # Tabla de trades
        if self.trades:
            lines.append("  DETALLE DE TRADES:")
            lines.append("  " + "-" * 63)
            lines.append(f"  {'#':<4} {'Par':<12} {'Entrada':<10} {'Salida':<10} {'P/L %':<10} {'Razon':<18}")
            lines.append("  " + "-" * 63)
            for i, t in enumerate(self.trades, 1):
                pl = f"{t['profit_pct']:+.2f}%"
                lines.append(
                    f"  {i:<4} {t['symbol']:<12} {t['entry_price']:<10.4f} "
                    f"{t['exit_price']:<10.4f} {pl:<10} {t['exit_reason']:<18}"
                )
            lines.append("  " + "-" * 63)

        # Veredicto
        lines.append("")
        if self.total_trades < 10:
            lines.append("  VEREDICTO: Pocos trades para evaluar. Necesitas mas datos.")
        elif self.win_rate >= 55 and self.total_profit_pct > 0 and self.max_drawdown < 10:
            lines.append("  VEREDICTO: APTO PARA LIVE (con precaucion)")
        elif self.win_rate >= 45 and self.total_profit_pct > 0:
            lines.append("  VEREDICTO: PROMETEDOR - Seguir en simulacion")
        else:
            lines.append("  VEREDICTO: NO APTO - Necesita optimizacion")
        lines.append("")

        return "\n".join(lines)


class Backtester:
    """
    Motor de backtesting. Simula la estrategia contra datos historicos.
    """

    def __init__(self, initial_balance: float = 1000.0, stake_amount: float = 50.0,
                 commission: float = 0.001, max_open_trades: int = 3):
        self.initial_balance = initial_balance
        self.stake_amount = stake_amount
        self.commission = commission  # 0.1% por defecto (Binance)
        self.max_open_trades = max_open_trades
        self.strategy = SafeStrategy()

    def run(self, pairs: list[str], timeframe: str = "5m",
            days: int = 30) -> BacktestResult:
        """
        Ejecutar backtest contra datos historicos.
        """
        print(f"\n  Descargando datos historicos ({days} dias)...")

        exchange = Exchange()
        result = BacktestResult()
        result.initial_balance = self.initial_balance
        result.pairs = pairs
        result.timeframe = timeframe

        balance = self.initial_balance
        peak_balance = balance
        max_drawdown = 0
        open_trades = []
        closed_trades = []

        # Cargar datos de todos los pares
        from src.data_downloader import DataDownloader
        downloader = DataDownloader()

        pair_data = {}
        for pair in pairs:
            pair = pair.strip()
            try:
                # 1. Preferir datos locales (CSV descargados) -> historial COMPLETO
                df = downloader.load_local(pair, timeframe)
                if not df.empty:
                    print(f"  {pair}: usando datos LOCALES ({len(df)} velas)")
                else:
                    # 2. Fallback: descarga en vivo (limitada a 1000 velas)
                    print(f"  {pair}: sin CSV local, descargando en vivo (max 1000 velas)...")
                    limit = min(days * (1440 // self._tf_minutes(timeframe)), 1000)
                    ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
                    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

                df = add_all_indicators(df)
                df.dropna(inplace=True)
                df.reset_index(drop=True, inplace=True)
                pair_data[pair] = df
                print(f"  {pair}: {len(df)} velas listas (con indicadores)")
            except Exception as e:
                print(f"  Error con {pair}: {e}")

        if not pair_data:
            print("  No se pudieron descargar datos.")
            return result

        # Encontrar el rango comun
        min_len = min(len(df) for df in pair_data.values())
        start_idx = 200  # Necesitamos al menos 200 velas para indicadores

        if min_len <= start_idx:
            print("  Datos insuficientes para backtesting.")
            return result

        first_df = list(pair_data.values())[0]
        result.period = (
            f"{first_df.iloc[start_idx]['timestamp'].strftime('%Y-%m-%d')} -> "
            f"{first_df.iloc[-1]['timestamp'].strftime('%Y-%m-%d')}"
        )

        print(f"\n  Simulando {min_len - start_idx} velas...")
        print(f"  Balance inicial: {balance:.2f} USDT")
        print()

        # Iterar vela por vela
        for i in range(start_idx, min_len):
            # 1. Verificar trades abiertos
            trades_to_close = []
            for trade in open_trades:
                pair = trade["symbol"]
                df = pair_data[pair]
                current_price = df.iloc[i]["close"]
                entry_price = trade["entry_price"]
                current_profit = (current_price - entry_price) / entry_price
                candles_open = i - trade["entry_idx"]

                # Stoploss
                max_loss = self.strategy.get_dynamic_stoploss(current_profit)
                if current_profit <= max_loss:
                    trades_to_close.append((trade, current_price, f"STOPLOSS ({current_profit:.1%})", i))
                    continue

                # Take profit (8%+)
                if current_profit >= 0.08:
                    trades_to_close.append((trade, current_price, f"TAKE_PROFIT ({current_profit:.1%})", i))
                    continue

                # Timeout 24h equivalent
                timeout_candles = (24 * 60) // self._tf_minutes(timeframe)
                if candles_open > timeout_candles and current_profit < 0.005:
                    trades_to_close.append((trade, current_price, "TIMEOUT", i))
                    continue

                # Senal tecnica de venta
                window = df.iloc[max(0, i - 60):i + 1].copy()
                should_sell, reason = self.strategy.should_sell(window, entry_price, current_profit)
                if should_sell:
                    trades_to_close.append((trade, current_price, reason, i))

            # Cerrar trades
            for trade, exit_price, reason, idx in trades_to_close:
                cost_entry = trade["amount"] * trade["entry_price"] * self.commission
                cost_exit = trade["amount"] * exit_price * self.commission
                profit = (exit_price - trade["entry_price"]) * trade["amount"] - cost_entry - cost_exit
                profit_pct = ((exit_price / trade["entry_price"]) - 1) * 100

                balance += (trade["amount"] * exit_price) - cost_exit
                open_trades.remove(trade)

                closed_trades.append({
                    "symbol": trade["symbol"],
                    "entry_price": trade["entry_price"],
                    "exit_price": exit_price,
                    "amount": trade["amount"],
                    "profit": profit,
                    "profit_pct": profit_pct,
                    "exit_reason": reason,
                    "duration_candles": idx - trade["entry_idx"],
                })

            # 2. Buscar nuevas entradas
            if len(open_trades) < self.max_open_trades:
                for pair, df in pair_data.items():
                    if len(open_trades) >= self.max_open_trades:
                        break
                    if any(t["symbol"] == pair for t in open_trades):
                        continue

                    window = df.iloc[max(0, i - 60):i + 1].copy()
                    if len(window) < 50:
                        continue

                    if self.strategy.should_buy(window):
                        entry_price = df.iloc[i]["close"]
                        amount = self.stake_amount / entry_price
                        cost = amount * entry_price * self.commission
                        total_cost = (amount * entry_price) + cost

                        if total_cost > balance:
                            continue

                        balance -= total_cost
                        open_trades.append({
                            "symbol": pair,
                            "entry_price": entry_price,
                            "amount": amount,
                            "entry_idx": i,
                        })

            # Actualizar drawdown
            total_value = balance
            for t in open_trades:
                current = pair_data[t["symbol"]].iloc[i]["close"]
                total_value += t["amount"] * current

            if total_value > peak_balance:
                peak_balance = total_value
            dd = ((peak_balance - total_value) / peak_balance) * 100
            if dd > max_drawdown:
                max_drawdown = dd

        # Cerrar trades abiertos al final
        for trade in open_trades:
            pair = trade["symbol"]
            exit_price = pair_data[pair].iloc[-1]["close"]
            profit_pct = ((exit_price / trade["entry_price"]) - 1) * 100
            cost = trade["amount"] * exit_price * self.commission
            profit = (exit_price - trade["entry_price"]) * trade["amount"] - cost

            balance += (trade["amount"] * exit_price) - cost
            closed_trades.append({
                "symbol": pair,
                "entry_price": trade["entry_price"],
                "exit_price": exit_price,
                "amount": trade["amount"],
                "profit": profit,
                "profit_pct": profit_pct,
                "exit_reason": "END_OF_DATA",
                "duration_candles": min_len - trade["entry_idx"],
            })

        # Calcular resultados
        result.trades = closed_trades
        result.final_balance = balance
        result.total_trades = len(closed_trades)
        result.winning_trades = len([t for t in closed_trades if t["profit"] >= 0])
        result.losing_trades = len([t for t in closed_trades if t["profit"] < 0])
        result.max_drawdown = max_drawdown
        result.total_profit_pct = ((balance / self.initial_balance) - 1) * 100

        if closed_trades:
            profits = [t["profit_pct"] for t in closed_trades]
            result.best_trade = max(profits)
            result.worst_trade = min(profits)
            result.avg_profit = sum(profits) / len(profits)
            result.win_rate = (result.winning_trades / result.total_trades) * 100

            durations = [t["duration_candles"] * self._tf_minutes(timeframe) for t in closed_trades]
            result.avg_duration_minutes = sum(durations) / len(durations)

            # Sharpe ratio simplificado
            import numpy as np
            returns = np.array(profits)
            if returns.std() > 0:
                result.sharpe_ratio = (returns.mean() / returns.std()) * (252 ** 0.5)

            # Profit factor
            gross_profit = sum(t["profit"] for t in closed_trades if t["profit"] > 0)
            gross_loss = abs(sum(t["profit"] for t in closed_trades if t["profit"] < 0))
            result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return result

    def _tf_minutes(self, tf: str) -> int:
        multipliers = {"m": 1, "h": 60, "d": 1440}
        return int(tf[:-1]) * multipliers.get(tf[-1], 1)
