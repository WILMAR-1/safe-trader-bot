"""Pruebas de TrendStrategy, protecciones, metricas y del motor de backtest.
Usa datos sinteticos: no necesita conexion ni datos descargados."""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from src.indicators import add_all_indicators
from src.performance import deflated_sharpe, max_drawdown, probabilistic_sharpe
from src.protections import Protections
from src.trend_backtest import Costs, prepare_data, simulate
from src.trend_strategy import TrendStrategy


def candles(seed, n=3000, drift=0.0, minutes_per_bar=60):
    """Velas horarias construidas desde un camino de 1 minuto (mechas realistas)."""
    rng = np.random.default_rng(seed)
    sig = 0.008 / np.sqrt(minutes_per_bar)
    steps = rng.normal(drift / minutes_per_bar - sig ** 2 / 2, sig, n * minutes_per_bar)
    x = 100 * np.exp(np.cumsum(steps)).reshape(n, minutes_per_bar)
    o = np.r_[100.0, x[:-1, -1]]
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open": o, "high": np.maximum(x.max(1), o), "low": np.minimum(x.min(1), o),
        "close": x[:, -1], "volume": 1.0,
    })


print("=" * 70); print("  PRUEBAS DE TREND STRATEGY"); print("=" * 70)
s = TrendStrategy()

# --- 1. Sin mirar al futuro: la senal de la vela k no cambia si se borra el futuro ---
df = prepare_data(candles(1, drift=0.0005), s)
full = s.signals(df)
for k in (400, 900, 1500, 2500):
    cut = prepare_data(candles(1, drift=0.0005).iloc[:k + 1], s)
    assert s.signals(cut).iloc[-1] == full.iloc[k], f"senal en {k} depende del futuro"
assert full.sum() > 0, "con tendencia alcista deberia haber senales"
print("1) [OK] senales sin lookahead y presentes en tendencia")

# --- 2. Sin ventaja en paseo aleatorio (si ganara, el motor haria trampa) ---
rets = []
for seed in range(40):
    d = prepare_data(candles(500 + seed), s)
    eq, _ = simulate(d, s, 1000, allow_short=True, max_leverage=3, costs=Costs(0, 0), start=250)
    rets.append(eq.iloc[-1] / 1000 - 1)
m, se = np.mean(rets), np.std(rets) / np.sqrt(len(rets))
assert abs(m) < 3 * se + 0.02, f"ventaja imposible en paseo aleatorio: {m:+.2%} +- {se:.2%}"
print(f"2) [OK] paseo aleatorio sin ventaja: {m:+.2%} +- {se:.2%}")

# --- 3. Los costes siempre restan ---
d = prepare_data(candles(7, drift=0.0005), s)
eq0, _ = simulate(d, s, 1000, costs=Costs(0, 0), start=250)
eq1, _ = simulate(d, s, 1000, costs=Costs(), start=250)
assert eq1.iloc[-1] < eq0.iloc[-1]
print(f"3) [OK] costes restan: {eq0.iloc[-1]:.0f} -> {eq1.iloc[-1]:.0f}")

# --- 4. Tamano por volatilidad: el stop cuesta el % pedido y respeta el tope ---
stake = s.position_size(equity=1000, price=100, atr=2, risk_pct=1, max_stake=10_000)
assert abs(stake * (2 * 2 / 100) - 10) < 1e-9, stake  # stop 2 ATR = 4% -> 10$ de riesgo
assert s.position_size(1000, 100, 2, 1, max_stake=50) == 50
assert s.position_size(1000, 100, 0.01, 1, max_stake=1e9) == 1000  # nunca > equity (spot)
assert s.position_size(100, 100, 50, 1, max_stake=1e9) == 0.0      # por debajo del minimo
print("4) [OK] tamano por riesgo, con tope y minimo")

# --- 5. Salida: el trailing nunca se afloja y cierra al tocarse ---
up = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=5, freq="1h"),
                   "high": [101, 110, 120, 118, 115.0], "low": [99, 108, 117, 110, 100.0],
                   "atr": [2.0] * 5})
stop = s.stop_price(up.iloc[1:], entry_price=100, entry_atr=2)
assert stop == 120 - 3 * 2, stop
assert s.stop_price(up.iloc[1:3], 100, 2) == stop  # mismo maximo -> mismo stop
bars = add_all_indicators(candles(3, n=600, drift=0.002))
bars = s.prepare(bars)
entry_t = bars["timestamp"].iloc[300]
ok, why = s.check_exit(bars, float(bars["close"].iloc[300]), entry_t, price=1.0)
assert ok and why.startswith(("STOP", "TRAILING")), why
print("5) [OK] chandelier sube con el maximo y cierra al tocarse")

# --- 6. Protecciones ---
pr = Protections(cooldown_minutes=60, stoploss_guard_count=2, stoploss_guard_hours=24,
                 stoploss_guard_lock_hours=12)
t0 = datetime(2024, 1, 1, 10)
pr.register_exit("BTC/USDT", is_stoploss=False, now=t0)
assert not pr.can_enter("BTC/USDT", t0 + timedelta(minutes=30))[0]
assert pr.can_enter("ETH/USDT", t0 + timedelta(minutes=30))[0]
assert pr.can_enter("BTC/USDT", t0 + timedelta(minutes=61))[0]
pr.register_exit("ETH/USDT", is_stoploss=True, now=t0)
pr.register_exit("SOL/USDT", is_stoploss=True, now=t0 + timedelta(hours=1))
assert not pr.can_enter("XRP/USDT", t0 + timedelta(hours=5))[0]
assert pr.can_enter("XRP/USDT", t0 + timedelta(hours=14))[0]
print("6) [OK] cooldown por par y StoplossGuard global")

# --- 7. Metricas ---
assert abs(max_drawdown(pd.Series([100, 120, 90, 130.0])) - 0.25) < 1e-12
rng = np.random.default_rng(0)
good = pd.Series(rng.normal(0.002, 0.01, 2000))
noise = pd.Series(rng.normal(0.0, 0.01, 2000))
assert probabilistic_sharpe(good) > 0.99
assert deflated_sharpe(good, n_trials=100) < probabilistic_sharpe(good)
assert deflated_sharpe(noise, n_trials=100) < 0.5
print("7) [OK] drawdown, PSR y DSR (probar mas variantes sube el liston)")

print("\n" + "=" * 70)
print("  TODAS LAS PRUEBAS PASARON")
print("=" * 70)
