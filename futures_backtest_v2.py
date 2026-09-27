"""
Backtest FUTUROS XRP v2 - Tecnicas de la comunidad (Freqtrade/NFI + Jesse).
- Stop dinamico por ATR (no fijo)  -> NostalgiaForInfinity style
- Trailing por ATR                  -> deja correr ganadores
- Riesgo fijo ~1 USD por trade      -> tamano calculado por distancia de stop
- VALIDACION WALK-FORWARD: 1a mitad (in-sample) vs 2a mitad (out-of-sample)

SIMULACION. No ejecuta ordenes reales.
"""
import pandas as pd
from src.data_downloader import DataDownloader
from src.indicators import add_all_indicators

RISK_USD = 1.00
ATR_STOP = 1.5     # stop inicial = 1.5 x ATR
ATR_TRAIL = 2.0    # trailing = 2.0 x ATR (deja correr)
BREAKEVEN_R = 1.0  # a +1R mueve stop a breakeven
COMMISSION = 0.0005
TIMEOUT_CANDLES = 576  # 48h


def run(df, label, start_capital=50.0):
    equity = start_capital
    peak = equity
    max_dd = 0.0
    trades = []
    pos = None
    idx = df.index.tolist()

    for k in range(200, len(df)):
        row = df.iloc[k]
        prev = df.iloc[k - 1]
        price = row["close"]
        atr = row["atr"]
        if pos:
            side = pos["side"]
            if side == "long":
                prof = (price - pos["entry"]) / pos["entry"]
                pos["max_price"] = max(pos["max_price"], price)
                if prof >= BREAKEVEN_R * pos["risk_pct"] and pos["stop"] < pos["entry"]:
                    pos["stop"] = pos["entry"]
                trail = pos["max_price"] - ATR_TRAIL * atr
                pos["stop"] = max(pos["stop"], trail)
                hit = price <= pos["stop"]
            else:
                prof = (pos["entry"] - price) / pos["entry"]
                pos["min_price"] = min(pos["min_price"], price)
                if prof >= BREAKEVEN_R * pos["risk_pct"] and pos["stop"] > pos["entry"]:
                    pos["stop"] = pos["entry"]
                trail = pos["min_price"] + ATR_TRAIL * atr
                pos["stop"] = min(pos["stop"], trail)
                hit = price >= pos["stop"]

            timeout = (k - pos["k"]) > TIMEOUT_CANDLES
            if hit or timeout:
                ex = pos["stop"] if hit else price
                gross = (ex - pos["entry"]) * pos["qty"] if side == "long" else (pos["entry"] - ex) * pos["qty"]
                fees = (pos["entry"] + ex) * pos["qty"] * COMMISSION
                net = gross - fees
                equity += net
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                trades.append({"side": side, "pl": net})
                pos = None
            continue

        # entradas
        up = row["ema_9"] > row["ema_21"] > row["ema_50"] and price > row["ema_200"]
        dn = row["ema_9"] < row["ema_21"] < row["ema_50"] and price < row["ema_200"]
        adx_ok = row["adx"] > 20
        long_sig = (up and adx_ok and 40 < row["rsi"] < 62 and row["rsi"] > prev["rsi"]
                    and row["macd_hist"] > prev["macd_hist"] and row["adx_pos"] > row["adx_neg"])
        short_sig = (dn and adx_ok and 38 < row["rsi"] < 60 and row["rsi"] < prev["rsi"]
                     and row["macd_hist"] < prev["macd_hist"] and row["adx_neg"] > row["adx_pos"])

        if (long_sig or short_sig) and atr > 0:
            stop_dist = ATR_STOP * atr
            qty = RISK_USD / stop_dist          # arriesga ~1 USD
            risk_pct = stop_dist / price
            if long_sig:
                pos = {"side": "long", "entry": price, "qty": qty, "stop": price - stop_dist,
                       "max_price": price, "k": k, "risk_pct": risk_pct}
            else:
                pos = {"side": "short", "entry": price, "qty": qty, "stop": price + stop_dist,
                       "min_price": price, "k": k, "risk_pct": risk_pct}

    n = len(trades)
    wins = [t for t in trades if t["pl"] > 0]
    gw = sum(t["pl"] for t in wins)
    gl = abs(sum(t["pl"] for t in trades if t["pl"] <= 0))
    worst = min((t["pl"] for t in trades), default=0)
    best = max((t["pl"] for t in trades), default=0)
    print(f"\n--- {label} ---")
    print(f"  Capital: {start_capital:.2f} -> {equity:.2f} USD  ({(equity/start_capital-1)*100:+.1f}%)")
    print(f"  Trades: {n} | Win: {len(wins)/n*100 if n else 0:.1f}% | "
          f"PF: {gw/gl if gl else 0:.2f} | Peor: {worst:+.2f} | Mejor: {best:+.2f} | MaxDD: {max_dd:.1f}%")
    return {"ret": (equity/start_capital-1)*100, "pf": gw/gl if gl else 0, "n": n, "dd": max_dd}


dl = DataDownloader()
df = dl.load_local("XRP/USDT", "5m")
df = add_all_indicators(df)
df.dropna(inplace=True)
df.reset_index(drop=True, inplace=True)

half = len(df) // 2
print("=" * 60)
print("  XRP FUTUROS v2 (ATR) - VALIDACION WALK-FORWARD")
print("=" * 60)
full = run(df, "AÑO COMPLETO")
ins = run(df.iloc[:half].reset_index(drop=True), "1a MITAD (in-sample / ajuste)")
oos = run(df.iloc[half:].reset_index(drop=True), "2a MITAD (OUT-OF-SAMPLE / no vista)")

print("\n" + "=" * 60)
print("  VEREDICTO DE ROBUSTEZ")
print("=" * 60)
if oos["n"] < 10:
    print("  Muy pocos trades fuera de muestra para concluir.")
elif oos["ret"] > 0 and oos["pf"] > 1.0:
    print(f"  OK: gana TAMBIEN fuera de muestra (+{oos['ret']:.1f}%, PF {oos['pf']:.2f}).")
    print("  Senal de que NO es solo overfitting. Aun asi vigilar drawdown.")
else:
    print(f"  ALERTA: fuera de muestra NO gana ({oos['ret']:+.1f}%, PF {oos['pf']:.2f}).")
    print("  Probable overfitting: se veia bien en el ano completo pero no generaliza.")
print("=" * 60)
