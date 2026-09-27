"""
Backtest de FUTUROS XRP/USDT - Riesgo fijo ~1 USD por trade, ganancia abierta.
SIMULACION sobre datos historicos reales. NO ejecuta ordenes reales.

Modelo:
- Riesgo fijo: 1.00 USD por operacion (tamano calculado por distancia al stop)
- Apalancamiento: 3x aislado (liquidacion lejos -> el stop manda)
- Stop inicial: 1.0% en contra  => perdida ~1 USD
- Trailing stop: tras +1R mueve a breakeven, luego deja correr (trailing 1.2%)
- Largos y cortos (aprovecha subidas y bajadas)
- Comision futuros 0.05% por lado (incluida)
"""
import pandas as pd
from src.data_downloader import DataDownloader
from src.indicators import add_all_indicators

# --- Parametros de riesgo ---
START_CAPITAL = 50.0
RISK_USD = 1.00          # perdida objetivo por trade
STOP_PCT = 0.010         # 1.0% distancia al stop
TRAIL_PCT = 0.012        # trailing 1.2%
BREAKEVEN_AT = 0.010     # a +1% mueve stop a entrada
COMMISSION = 0.0005      # 0.05% por lado (futuros taker)
TIMEOUT_CANDLES = 576    # 48h en 5m -> evita sangrar funding

dl = DataDownloader()
df = dl.load_local("XRP/USDT", "5m")
df = add_all_indicators(df)
df.dropna(inplace=True)
df.reset_index(drop=True, inplace=True)

equity = START_CAPITAL
peak = equity
max_dd = 0.0
trades = []
pos = None  # posicion abierta

for i in range(200, len(df)):
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    price = row["close"]

    # ---- Gestionar posicion abierta ----
    if pos:
        side = pos["side"]
        if side == "long":
            profit_pct = (price - pos["entry"]) / pos["entry"]
            # actualizar maximo y trailing
            if price > pos["max_price"]:
                pos["max_price"] = price
            if profit_pct >= BREAKEVEN_AT and pos["stop"] < pos["entry"]:
                pos["stop"] = pos["entry"]  # breakeven
            trail = pos["max_price"] * (1 - TRAIL_PCT)
            if trail > pos["stop"]:
                pos["stop"] = trail
            hit_stop = price <= pos["stop"]
        else:  # short
            profit_pct = (pos["entry"] - price) / pos["entry"]
            if price < pos["min_price"]:
                pos["min_price"] = price
            if profit_pct >= BREAKEVEN_AT and pos["stop"] > pos["entry"]:
                pos["stop"] = pos["entry"]
            trail = pos["min_price"] * (1 + TRAIL_PCT)
            if trail < pos["stop"]:
                pos["stop"] = trail
            hit_stop = price >= pos["stop"]

        timeout = (i - pos["entry_idx"]) > TIMEOUT_CANDLES

        if hit_stop or timeout:
            exit_price = pos["stop"] if hit_stop else price
            if side == "long":
                gross = (exit_price - pos["entry"]) * pos["qty"]
            else:
                gross = (pos["entry"] - exit_price) * pos["qty"]
            fees = (pos["entry"] + exit_price) * pos["qty"] * COMMISSION
            net = gross - fees
            equity += net
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
            trades.append({"side": side, "pl": net, "reason": "stop" if hit_stop else "timeout"})
            pos = None
        continue

    # ---- Buscar entrada (solo si no hay posicion) ----
    uptrend = row["ema_9"] > row["ema_21"] > row["ema_50"] and price > row["ema_200"]
    downtrend = row["ema_9"] < row["ema_21"] < row["ema_50"] and price < row["ema_200"]
    rsi_mid = 40 < row["rsi"] < 62
    adx_ok = row["adx"] > 20

    go_long = (uptrend and adx_ok and rsi_mid
               and row["rsi"] > prev["rsi"] and row["macd_hist"] > prev["macd_hist"]
               and row["adx_pos"] > row["adx_neg"])
    go_short = (downtrend and adx_ok and 38 < row["rsi"] < 60
                and row["rsi"] < prev["rsi"] and row["macd_hist"] < prev["macd_hist"]
                and row["adx_neg"] > row["adx_pos"])

    if go_long or go_short:
        qty = RISK_USD / (price * STOP_PCT)   # tamano para arriesgar ~1 USD
        notional = qty * price
        if go_long:
            pos = {"side": "long", "entry": price, "qty": qty,
                   "stop": price * (1 - STOP_PCT), "max_price": price, "entry_idx": i}
        else:
            pos = {"side": "short", "entry": price, "qty": qty,
                   "stop": price * (1 + STOP_PCT), "min_price": price, "entry_idx": i}

# ---- Resultados ----
n = len(trades)
wins = [t for t in trades if t["pl"] > 0]
losses = [t for t in trades if t["pl"] <= 0]
gross_w = sum(t["pl"] for t in wins)
gross_l = abs(sum(t["pl"] for t in losses))
worst = min((t["pl"] for t in trades), default=0)
best = max((t["pl"] for t in trades), default=0)

print("=" * 60)
print("  BACKTEST FUTUROS XRP/USDT (1 ano, 5m) - SIMULACION")
print("=" * 60)
print(f"  Capital inicial:   {START_CAPITAL:.2f} USD")
print(f"  Capital final:     {equity:.2f} USD")
print(f"  Profit neto:       {equity - START_CAPITAL:+.2f} USD ({(equity/START_CAPITAL-1)*100:+.1f}%)")
print("-" * 60)
print(f"  Total trades:      {n}")
if n:
    print(f"  Ganadores:         {len(wins)} ({len(wins)/n*100:.1f}%)")
    print(f"  Perdedores:        {len(losses)} ({len(losses)/n*100:.1f}%)")
    print(f"  Largos / Cortos:   {sum(1 for t in trades if t['side']=='long')} / {sum(1 for t in trades if t['side']=='short')}")
    print(f"  Mejor trade:       {best:+.2f} USD")
    print(f"  PEOR trade:        {worst:+.2f} USD   <-- control del riesgo")
    print(f"  Profit factor:     {(gross_w/gross_l if gross_l else 0):.2f}")
    print(f"  Promedio/trade:    {sum(t['pl'] for t in trades)/n:+.3f} USD")
print(f"  Max drawdown:      {max_dd:.1f}%")
print("=" * 60)
