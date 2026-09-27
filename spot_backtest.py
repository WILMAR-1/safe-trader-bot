"""
Backtest SPOT (solo largos) - estrategia SIMPLE y validada walk-forward.
Filosofia: comprar caidas dentro de tendencia alcista, fuera en bajistas.

Reglas (pocas, a proposito):
  ENTRADA: close > EMA200 (macro alcista) Y ema9 > ema21 (impulso)
           Y RSI cruza al alza el 45 (rebote desde la caida)
  SALIDA : stop -3% | trailing 3% tras +3% | RSI > 72
Sin apalancamiento. Comision spot 0.1%/lado.
Compara contra BUY & HOLD (comprar y aguantar).
"""
import pandas as pd
from src.data_downloader import DataDownloader
from src.indicators import add_all_indicators

PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
STOP = 0.03
TRAIL = 0.03
TRAIL_ACT = 0.03
COMM = 0.001


def run_pair(df):
    cap = 100.0
    pos = None
    trades = []
    for k in range(200, len(df)):
        row = df.iloc[k]
        prev = df.iloc[k - 1]
        price = row["close"]
        if pos:
            prof = (price - pos["entry"]) / pos["entry"]
            pos["max"] = max(pos["max"], price)
            stop_price = pos["entry"] * (1 - STOP)
            if prof >= TRAIL_ACT:
                stop_price = max(stop_price, pos["max"] * (1 - TRAIL))
            exit_now = price <= stop_price or row["rsi"] > 72
            if exit_now:
                ex = price
                cap = pos["units"] * ex * (1 - COMM)
                trades.append((ex - pos["entry"]) / pos["entry"] * 100)
                pos = None
            continue
        up = price > row["ema_200"] and row["ema_9"] > row["ema_21"]
        rsi_cross = prev["rsi"] < 45 <= row["rsi"]
        if up and rsi_cross:
            units = (cap * (1 - COMM)) / price
            pos = {"entry": price, "units": units, "max": price}
    if pos:
        cap = pos["units"] * df.iloc[-1]["close"] * (1 - COMM)
    return (cap / 100.0 - 1) * 100, trades


def buy_hold(df):
    return (df.iloc[-1]["close"] / df.iloc[200]["close"] - 1) * 100


def evaluate(slice_fn, label):
    strat_rets, bh_rets, all_trades = [], [], []
    for p in PAIRS:
        df = slice_fn(DATA[p])
        if len(df) < 300:
            continue
        r, tr = run_pair(df)
        strat_rets.append(r)
        bh_rets.append(buy_hold(df))
        all_trades += tr
    n = len(all_trades)
    wins = [t for t in all_trades if t > 0]
    avg_s = sum(strat_rets) / len(strat_rets)
    avg_bh = sum(bh_rets) / len(bh_rets)
    print(f"\n--- {label} ---")
    print(f"  Estrategia (prom 5 monedas): {avg_s:+.1f}%")
    print(f"  Buy & Hold (prom 5 monedas): {avg_bh:+.1f}%")
    print(f"  {'GANA a buy&hold' if avg_s > avg_bh else 'PIERDE vs buy&hold'}")
    print(f"  Trades: {n} | Win rate: {len(wins)/n*100 if n else 0:.1f}%")
    return avg_s, avg_bh, n


print("Cargando datos...")
DATA = {}
for p in PAIRS:
    d = DataDownloader().load_local(p, "5m")
    d = add_all_indicators(d)
    d.dropna(inplace=True)
    d.reset_index(drop=True, inplace=True)
    DATA[p] = d

print("=" * 60)
print("  SPOT - ESTRATEGIA SIMPLE vs BUY&HOLD - WALK-FORWARD")
print("=" * 60)
evaluate(lambda d: d, "AÑO COMPLETO")
half = {p: len(DATA[p]) // 2 for p in PAIRS}
evaluate(lambda d: d.iloc[:len(d)//2].reset_index(drop=True), "1a MITAD (in-sample)")
s_oos, bh_oos, n_oos = evaluate(lambda d: d.iloc[len(d)//2:].reset_index(drop=True), "2a MITAD (OUT-OF-SAMPLE)")

print("\n" + "=" * 60)
print("  VEREDICTO")
print("=" * 60)
if n_oos < 10:
    print("  Pocos trades fuera de muestra.")
elif s_oos > 0 and s_oos > bh_oos:
    print(f"  BUENO: fuera de muestra gana ({s_oos:+.1f}%) y supera a buy&hold ({bh_oos:+.1f}%).")
elif s_oos > 0:
    print(f"  ACEPTABLE: gana ({s_oos:+.1f}%) pero NO supera a buy&hold ({bh_oos:+.1f}%).")
    print("  -> Mejor comprar y aguantar que usar el bot.")
else:
    print(f"  MALO: pierde fuera de muestra ({s_oos:+.1f}%).")
print("=" * 60)
