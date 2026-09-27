"""
Metricas de rendimiento con el rigor que usan los fondos cuantitativos.

La leccion mas repetida en los casos publicos (Lopez de Prado, Bailey, Harvey):
la mayoria de backtests "ganadores" son falsos positivos por probar muchas
variantes. Por eso, ademas del Sharpe, calculamos:

- Probabilistic Sharpe Ratio (PSR): probabilidad de que el Sharpe real sea > 0,
  teniendo en cuenta la longitud de la muestra, la asimetria y las colas.
- Deflated Sharpe Ratio (DSR): igual, pero penalizando el numero de variantes
  probadas. Si probaste 50 combinaciones de parametros, el listón sube.
  (Bailey & Lopez de Prado, "The Deflated Sharpe Ratio", 2014)
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def bars_per_year(timeframe: str) -> float:
    """Velas por ano para '5m', '1h', '4h', '1d'... (mercado 24/7)."""
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    minutes = int(timeframe[:-1]) * units.get(timeframe[-1], 1)
    return 365 * 1440 / minutes


def max_drawdown(equity: pd.Series) -> float:
    """Maxima caida desde un pico, en fraccion (0.25 = -25%)."""
    peak = equity.cummax()
    return float(((peak - equity) / peak).max()) if len(equity) else 0.0


def sharpe(returns: pd.Series) -> float:
    """Sharpe por periodo (sin anualizar)."""
    sd = returns.std()
    return float(returns.mean() / sd) if sd and sd > 0 else 0.0


def probabilistic_sharpe(returns: pd.Series, sr_benchmark: float = 0.0) -> float:
    """PSR: P(SR real > sr_benchmark). SR por periodo, no anualizado."""
    r = returns.dropna()
    n = len(r)
    if n < 3:
        return 0.0
    sr = sharpe(r)
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0  # pandas da exceso de curtosis
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Sharpe maximo esperado por PURA SUERTE tras n_trials intentos sin ventaja real."""
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    return math.sqrt(sr_variance) * (
        (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n_trials)
        + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(returns: pd.Series, n_trials: int, trial_sharpes: list[float] | None = None) -> float:
    """DSR: PSR contra el Sharpe que saldria por suerte tras n_trials variantes.
    trial_sharpes: Sharpes (por periodo) de todas las variantes probadas, para estimar su varianza.
    Sin ellos se usa la varianza teorica de un Sharpe estimado con n observaciones (~1/n)."""
    n = len(returns.dropna())
    if n < 3:
        return 0.0
    if trial_sharpes and len(trial_sharpes) > 1:
        var = float(np.var(trial_sharpes, ddof=1))
    else:
        var = 1.0 / n
    return probabilistic_sharpe(returns, expected_max_sharpe(n_trials, var))


def summarize(equity: pd.Series, trades_pl: list[float], timeframe: str,
              n_trials: int = 1, trial_sharpes: list[float] | None = None) -> dict:
    """Resumen completo a partir de la curva de equity (una muestra por vela)."""
    equity = equity.dropna()
    rets = equity.pct_change().dropna()
    ann = bars_per_year(timeframe)
    years = len(equity) / ann if ann else 0
    total = float(equity.iloc[-1] / equity.iloc[0] - 1) if len(equity) > 1 else 0.0
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else -1.0
    dd = max_drawdown(equity)
    downside = rets[rets < 0].std()
    wins = [t for t in trades_pl if t > 0]
    losses = [t for t in trades_pl if t <= 0]
    gross_loss = abs(sum(losses))
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe(rets) * math.sqrt(ann),
        "sortino": float(rets.mean() / downside * math.sqrt(ann)) if downside and downside > 0 else 0.0,
        "max_drawdown": dd,
        "calmar": cagr / dd if dd > 0 else 0.0,
        "trades": len(trades_pl),
        "win_rate": len(wins) / len(trades_pl) if trades_pl else 0.0,
        "profit_factor": float(sum(wins) / gross_loss) if gross_loss > 0 else (float("inf") if wins else 0.0),
        "expectancy": float(np.mean(trades_pl)) if trades_pl else 0.0,
        "psr": probabilistic_sharpe(rets),
        "dsr": deflated_sharpe(rets, n_trials, trial_sharpes),
    }
