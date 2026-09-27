"""
Backtest realista de TrendStrategy + validacion walk-forward.

Buenas practicas tomadas de los casos publicos (y de los errores mas comunes):
- La senal se calcula al CIERRE de la vela y se ejecuta en la APERTURA de la
  siguiente (nada de comprar al mismo precio que genero la senal).
- Si el stop se toca dentro de la vela se ejecuta al stop, o a la apertura si
  hubo hueco (gap) por debajo: el peor de los dos.
- Comision + slippage en cada lado.
- Walk-forward anclado: los parametros se eligen SOLO con datos pasados y se
  evaluan en el tramo siguiente, que nunca se uso para ajustar.
- Se reporta el numero de variantes probadas y el Deflated Sharpe Ratio.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from src.indicators import add_all_indicators
from src.performance import bars_per_year, sharpe, summarize
from src.trend_strategy import TrendParams, TrendStrategy


@dataclass
class Costs:
    fee: float = 0.001        # 0.1% por lado (Binance spot sin descuento)
    slippage: float = 0.0005  # 0.05% por lado


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Agrupar velas (p.ej. 5m -> 1h) para no depender de descargar otro timeframe."""
    rule = timeframe.replace("m", "min") if timeframe.endswith("m") else timeframe
    out = (df.set_index("timestamp")
             .resample(rule, label="left", closed="left")
             .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
             .dropna()
             .reset_index())
    return out


def prepare_data(df: pd.DataFrame, strategy: TrendStrategy) -> pd.DataFrame:
    df = add_all_indicators(df.copy())
    df = strategy.prepare(df)
    return df.reset_index(drop=True)


def simulate(df: pd.DataFrame, strategy: TrendStrategy, capital: float = 1000.0,
             risk_pct: float = 1.0, max_leverage: float = 1.0, allow_short: bool = False,
             costs: Costs = Costs(), start: int = 0, end: int | None = None):
    """Simular una cartera de un solo par sobre df[start:end].
    df debe venir de prepare_data con los parametros de `strategy`.
    Devuelve (equity por vela, lista de P/L por trade)."""
    end = len(df) if end is None else end
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    ex_lo = df["dc_exit_low"].to_numpy(float)
    ex_hi = df["dc_exit_high"].to_numpy(float)
    sig = strategy.signals(df, allow_short).to_numpy()
    p = strategy.p
    cost = costs.fee + costs.slippage

    cash = capital
    pos = 0          # 1 largo, -1 corto
    qty = entry = stop = extreme = 0.0
    pending_entry = 0
    pending_exit = False
    equity = np.empty(end - start)
    trades: list[float] = []

    def close_at(price: float):
        nonlocal cash, pos
        gross = (price - entry) * qty * pos
        fees = (entry + price) * qty * cost
        cash += gross - fees
        trades.append(gross - fees)
        pos = 0

    for k in range(start, end):
        # 1. Ordenes decididas al cierre de la vela anterior -> apertura de esta
        if pos and pending_exit:
            close_at(o[k])
            pending_exit = False
        if not pos and pending_entry:
            a = atr[k - 1]
            entry = o[k]
            if a > 0 and entry > 0 and cash > 0:
                stop_pct = p.stop_atr * a / entry
                notional = min(cash * risk_pct / 100 / stop_pct, cash * max_leverage)
                qty = notional / entry
                pos = pending_entry
                stop = strategy.initial_stop(entry, a, "buy" if pos > 0 else "sell")
                extreme = entry
            pending_entry = 0

        # 2. Stop dentro de la vela (con el stop conocido ANTES de la vela)
        if pos > 0 and lo[k] <= stop:
            close_at(min(o[k], stop))
        elif pos < 0 and h[k] >= stop:
            close_at(max(o[k], stop))

        # 3. Al cierre: actualizar trailing y decidir para la siguiente vela
        if pos > 0:
            extreme = max(extreme, h[k])
            stop = max(stop, extreme - p.trail_atr * atr[k])
            pending_exit = c[k] < ex_lo[k]
        elif pos < 0:
            extreme = min(extreme, lo[k])
            stop = min(stop, extreme + p.trail_atr * atr[k])
            pending_exit = c[k] > ex_hi[k]
        elif k + 1 < end and sig[k] != 0:
            pending_entry = int(sig[k])

        equity[k - start] = cash + ((c[k] - entry) * qty * pos if pos else 0.0)

    if pos:  # cerrar al final para contabilizar
        close_at(c[end - 1])
        equity[-1] = cash
    return pd.Series(equity, index=df["timestamp"].iloc[start:end].to_numpy()), trades


def param_grid() -> list[TrendParams]:
    """Rejilla PEQUENA a proposito: cada variante extra sube el liston del DSR."""
    base = TrendParams()
    grid = itertools.product([20, 55, 100], [2.5, 3.5], [60, 180])
    return [replace(base, entry_n=e, trail_atr=t, momentum_n=m) for e, t, m in grid]


def walk_forward(raw: dict[str, pd.DataFrame], timeframe: str, folds: int = 4,
                 capital: float = 1000.0, risk_pct: float = 1.0, max_leverage: float = 1.0,
                 allow_short: bool = False, costs: Costs = Costs(),
                 grid: list[TrendParams] | None = None) -> dict:
    """Walk-forward anclado sobre una cartera equiponderada de pares.
    Divide el historico en folds+1 tramos; en cada paso ajusta con todo lo anterior
    y evalua en el tramo siguiente. Devuelve la curva out-of-sample concatenada."""
    grid = grid or param_grid()
    prepared = {i: {s: prepare_data(d, TrendStrategy(pr)) for s, d in raw.items()}
                for i, pr in enumerate(grid)}
    n = min(len(d) for d in raw.values())
    warmup = 250
    bounds = np.linspace(warmup, n, folds + 2).astype(int)
    per_pair = capital / len(raw)

    def portfolio(i: int, a: int, b: int):
        strat = TrendStrategy(grid[i])
        eq_total, pls = None, []
        for s, df in prepared[i].items():
            df = df.iloc[len(df) - n:].reset_index(drop=True)  # alinear por el final
            eq, tr = simulate(df, strat, per_pair, risk_pct, max_leverage, allow_short, costs, a, b)
            # sumar por posicion (los pares estan alineados por el final)
            eq_total = eq if eq_total is None else eq_total + eq.to_numpy()
            pls += tr
        return eq_total, pls

    oos_curves, oos_trades, chosen, trial_sr = [], [], [], []
    for f in range(folds):
        is_a, is_b, oos_b = bounds[0], bounds[f + 1], bounds[f + 2]
        scores = []
        for i in range(len(grid)):
            eq, _ = portfolio(i, is_a, is_b)
            sr = sharpe(eq.pct_change().dropna())
            scores.append(sr)
        trial_sr += scores
        best = int(np.argmax(scores))
        chosen.append(grid[best])
        eq, tr = portfolio(best, is_b, oos_b)
        # encadenar: cada tramo OOS arranca donde termino el anterior
        scale = (oos_curves[-1].iloc[-1] if oos_curves else capital) / capital
        oos_curves.append(eq * scale)
        oos_trades += [t * scale for t in tr]

    oos = pd.concat(oos_curves)
    stats = summarize(oos, oos_trades, timeframe, n_trials=len(grid), trial_sharpes=trial_sr)

    # Referencia: comprar y aguantar la misma cesta en el mismo periodo OOS
    bh = None
    for d in raw.values():
        closes = d["close"].iloc[len(d) - n:].reset_index(drop=True).iloc[bounds[1]:bounds[-1]]
        curve = closes / closes.iloc[0] * per_pair
        bh = curve if bh is None else bh + curve.to_numpy()
    bh_stats = summarize(pd.Series(bh.to_numpy()), [], timeframe)

    return {"oos": stats, "buy_hold": bh_stats, "chosen": chosen,
            "n_trials": len(grid), "equity": oos,
            "years_oos": len(oos) / bars_per_year(timeframe)}
