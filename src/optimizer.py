"""
Optimizador de parametros (nuestro Hyperopt).
Prueba miles de combinaciones para encontrar los mejores valores.
"""

import random
import logging
import pandas as pd
import numpy as np
from itertools import product
from src.exchange import Exchange
from src.indicators import add_all_indicators
from src.strategy import SafeStrategy

logger = logging.getLogger(__name__)


class StrategyParams:
    """Parametros optimizables de la estrategia."""
    def __init__(self, rsi_low=25, rsi_high=45, adx_min=20,
                 stoploss=-0.03, tp_high=0.08, tp_mid=0.05,
                 vol_ratio=1.2, ema_fast=9, ema_mid=21, ema_slow=50):
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.adx_min = adx_min
        self.stoploss = stoploss
        self.tp_high = tp_high
        self.tp_mid = tp_mid
        self.vol_ratio = vol_ratio
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow

    def to_dict(self):
        return vars(self)

    def __repr__(self):
        return (f"RSI({self.rsi_low}-{self.rsi_high}) ADX>{self.adx_min} "
                f"SL={self.stoploss:.1%} TP={self.tp_high:.1%}/{self.tp_mid:.1%} "
                f"Vol>{self.vol_ratio} EMA({self.ema_fast}/{self.ema_mid}/{self.ema_slow})")


class CustomStrategy:
    """Estrategia con parametros personalizables para optimizacion."""

    def __init__(self, params: StrategyParams):
        self.p = params

    def should_buy(self, df: pd.DataFrame) -> bool:
        if len(df) < 2:
            return False
        c = df.iloc[-1]
        p = df.iloc[-2]

        return (
            c[f"ema_{self.p.ema_fast}"] > c[f"ema_{self.p.ema_mid}"] > c[f"ema_{self.p.ema_slow}"]
            and c["close"] > c["ema_200"]
            and self.p.rsi_low < c["rsi"] < self.p.rsi_high
            and c["rsi"] > p["rsi"]
            and c["macd_hist"] > 0
            and c["macd_hist"] > p["macd_hist"]
            and c["vol_ratio"] > self.p.vol_ratio
            and c["adx"] > self.p.adx_min
            and c["adx_pos"] > c["adx_neg"]
            and c["close"] < c["bb_mid"]
            and c["atr_pct"] < 5
        )

    def should_sell(self, df, entry_price, current_profit):
        if len(df) < 2:
            return False, ""
        c = df.iloc[-1]

        if current_profit <= self.p.stoploss:
            return True, "STOPLOSS"
        if current_profit > self.p.tp_high:
            return True, "TP_HIGH"
        if current_profit > self.p.tp_mid and c["rsi"] > 65:
            return True, "TP_MID"
        if c["rsi"] > 70 and c["macd"] < c["macd_signal"] and c["close"] > c["bb_upper"] * 0.98:
            return True, "SELL_SIGNAL"
        return False, ""


class Optimizer:
    """
    Optimizador que prueba combinaciones de parametros.
    Busca la combinacion que maximiza el profit con menor drawdown.
    """

    # Rangos de busqueda
    PARAM_SPACE = {
        "rsi_low": [20, 25, 28, 30],
        "rsi_high": [40, 45, 48, 50],
        "adx_min": [15, 18, 20, 25],
        "stoploss": [-0.02, -0.03, -0.04, -0.05],
        "tp_high": [0.06, 0.08, 0.10],
        "tp_mid": [0.03, 0.04, 0.05],
        "vol_ratio": [1.0, 1.2, 1.5],
    }

    def __init__(self, initial_balance: float = 1000.0, stake_amount: float = 50.0,
                 commission: float = 0.001):
        self.initial_balance = initial_balance
        self.stake_amount = stake_amount
        self.commission = commission

    def optimize(self, pairs: list[str], timeframe: str = "5m",
                 days: int = 30, max_trials: int = 200) -> dict:
        """
        Ejecutar optimizacion.
        Prueba combinaciones aleatorias y devuelve la mejor.
        """
        print("\n" + "=" * 65)
        print("  OPTIMIZADOR DE PARAMETROS")
        print("=" * 65)

        # Descargar datos una sola vez
        print(f"\n  Descargando datos historicos ({days} dias)...")
        exchange = Exchange()
        pair_data = {}

        for pair in pairs:
            pair = pair.strip()
            try:
                limit = min(days * (1440 // self._tf_minutes(timeframe)), 1000)
                ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
                df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df = add_all_indicators(df)
                df.dropna(inplace=True)
                df.reset_index(drop=True, inplace=True)
                pair_data[pair] = df
                print(f"  {pair}: {len(df)} velas")
            except Exception as e:
                print(f"  Error {pair}: {e}")

        if not pair_data:
            print("  No hay datos.")
            return {}

        # Generar combinaciones aleatorias
        total_combinations = 1
        for values in self.PARAM_SPACE.values():
            total_combinations *= len(values)

        trials = min(max_trials, total_combinations)
        print(f"\n  Combinaciones posibles: {total_combinations}")
        print(f"  Probando: {trials} combinaciones")
        print()

        results = []
        best_score = -999
        best_params = None
        best_result = None

        for trial in range(trials):
            # Generar parametros aleatorios
            params = StrategyParams(
                rsi_low=random.choice(self.PARAM_SPACE["rsi_low"]),
                rsi_high=random.choice(self.PARAM_SPACE["rsi_high"]),
                adx_min=random.choice(self.PARAM_SPACE["adx_min"]),
                stoploss=random.choice(self.PARAM_SPACE["stoploss"]),
                tp_high=random.choice(self.PARAM_SPACE["tp_high"]),
                tp_mid=random.choice(self.PARAM_SPACE["tp_mid"]),
                vol_ratio=random.choice(self.PARAM_SPACE["vol_ratio"]),
            )

            # Asegurar que rsi_low < rsi_high
            if params.rsi_low >= params.rsi_high:
                continue

            # Simular con estos parametros
            res = self._simulate(pair_data, params, timeframe)

            # Score = profit - (drawdown * 2)  (penalizamos drawdown fuerte)
            score = res["profit_pct"] - (res["max_drawdown"] * 2)
            if res["total_trades"] < 5:
                score -= 50  # Penalizar si hay muy pocos trades

            results.append({
                "params": params,
                "score": score,
                **res,
            })

            if score > best_score:
                best_score = score
                best_params = params
                best_result = res

            # Progreso
            if (trial + 1) % 20 == 0 or trial == 0:
                print(
                    f"  [{trial + 1}/{trials}] "
                    f"Mejor: {best_score:+.2f} | "
                    f"Profit: {best_result['profit_pct']:+.2f}% | "
                    f"WR: {best_result['win_rate']:.0f}% | "
                    f"DD: {best_result['max_drawdown']:.1f}% | "
                    f"Trades: {best_result['total_trades']}"
                )

        # Ordenar resultados
        results.sort(key=lambda x: x["score"], reverse=True)

        # Mostrar top 5
        print("\n" + "=" * 65)
        print("  TOP 5 MEJORES COMBINACIONES")
        print("=" * 65)
        print(f"  {'#':<4} {'Score':<8} {'Profit':<10} {'WR':<8} {'DD':<8} {'Trades':<8} Parametros")
        print("  " + "-" * 63)

        for i, r in enumerate(results[:5], 1):
            print(
                f"  {i:<4} {r['score']:+<8.2f} {r['profit_pct']:+<10.2f}% "
                f"{r['win_rate']:<8.0f}% {r['max_drawdown']:<8.1f}% "
                f"{r['total_trades']:<8} {r['params']}"
            )

        # Mejor resultado
        print("\n" + "=" * 65)
        print("  MEJORES PARAMETROS ENCONTRADOS")
        print("=" * 65)
        print(f"\n  {best_params}")
        print(f"\n  Profit: {best_result['profit_pct']:+.2f}%")
        print(f"  Win Rate: {best_result['win_rate']:.1f}%")
        print(f"  Max Drawdown: {best_result['max_drawdown']:.1f}%")
        print(f"  Trades: {best_result['total_trades']}")
        print(f"  Score: {best_score:+.2f}")

        print("\n  Para aplicar estos parametros, actualiza tu .env:")
        print(f"  STOPLOSS={best_params.stoploss}")
        print("=" * 65)

        return {
            "best_params": best_params.to_dict(),
            "best_score": best_score,
            "best_result": best_result,
            "all_results": results[:10],
        }

    def _simulate(self, pair_data: dict, params: StrategyParams,
                  timeframe: str) -> dict:
        """Simulacion rapida con parametros dados."""
        strategy = CustomStrategy(params)
        balance = self.initial_balance
        peak = balance
        max_dd = 0
        open_trades = []
        wins = 0
        losses = 0
        total = 0

        min_len = min(len(df) for df in pair_data.values())
        start = 200

        for i in range(start, min_len):
            # Verificar abiertos
            to_close = []
            for t in open_trades:
                price = pair_data[t["symbol"]].iloc[i]["close"]
                profit = (price - t["entry"]) / t["entry"]

                window = pair_data[t["symbol"]].iloc[max(0, i - 200):i + 1]
                sell, reason = strategy.should_sell(window, t["entry"], profit)

                timeout = (i - t["idx"]) > ((24 * 60) // self._tf_minutes(timeframe))
                if timeout and profit < 0.005:
                    sell, reason = True, "TIMEOUT"

                if sell:
                    to_close.append((t, price, profit))

            for t, price, profit in to_close:
                comm = t["amount"] * price * self.commission
                balance += (t["amount"] * price) - comm
                open_trades.remove(t)
                total += 1
                if profit >= 0:
                    wins += 1
                else:
                    losses += 1

            # Nuevas entradas
            if len(open_trades) < 3:
                for pair, df in pair_data.items():
                    if len(open_trades) >= 3:
                        break
                    if any(t["symbol"] == pair for t in open_trades):
                        continue

                    window = df.iloc[max(0, i - 200):i + 1]
                    if len(window) < 50:
                        continue

                    if strategy.should_buy(window):
                        price = df.iloc[i]["close"]
                        amount = self.stake_amount / price
                        cost = (amount * price) + (amount * price * self.commission)
                        if cost > balance:
                            continue
                        balance -= cost
                        open_trades.append({
                            "symbol": pair, "entry": price,
                            "amount": amount, "idx": i,
                        })

            # Drawdown
            val = balance + sum(
                t["amount"] * pair_data[t["symbol"]].iloc[i]["close"]
                for t in open_trades
            )
            if val > peak:
                peak = val
            dd = ((peak - val) / peak) * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Cerrar abiertos
        for t in open_trades:
            price = pair_data[t["symbol"]].iloc[-1]["close"]
            comm = t["amount"] * price * self.commission
            balance += (t["amount"] * price) - comm
            total += 1
            if price >= t["entry"]:
                wins += 1
            else:
                losses += 1

        profit_pct = ((balance / self.initial_balance) - 1) * 100
        win_rate = (wins / total * 100) if total > 0 else 0

        return {
            "profit_pct": profit_pct,
            "win_rate": win_rate,
            "max_drawdown": max_dd,
            "total_trades": total,
            "final_balance": balance,
        }

    def _tf_minutes(self, tf: str) -> int:
        multipliers = {"m": 1, "h": 60, "d": 1440}
        return int(tf[:-1]) * multipliers.get(tf[-1], 1)
