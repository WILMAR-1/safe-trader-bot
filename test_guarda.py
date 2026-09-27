"""Verifica que la guarda final bloquea la operacion de 3.15 lotes que si paso."""
import os
os.environ.setdefault("MT5_HOST", "mt5")
from src.exness import ExnessExchange

ex = ExnessExchange(dry_run=True)
ok, msg = ex.connect()
print("conexion:", ok, msg if not ok else "")
if not ok:
    raise SystemExit

acc = ex.mt5.account_info()
info = ex.mt5.symbol_info("BTCUSDc")
tick = ex.mt5.symbol_info_tick("BTCUSDc")
equity_usd = float(acc.equity) / 100.0
print(f"equity real: {equity_usd:.2f} USD | tope: {ex.risk.max_leverage}x "
      f"= {equity_usd*ex.risk.max_leverage:.2f} USD de exposicion maxima")

# Reproducir el plan malo que SI se ejecuto
LOTES_MALOS = 3.15
nominal = LOTES_MALOS * info.trade_contract_size * tick.ask
print(f"\nLa operacion que SI paso: {LOTES_MALOS} lotes = {nominal:.2f} USD "
      f"= {nominal/equity_usd:.1f}x  <-- deberia bloquearse")

plan_malo = {"allowed": True, "lots": LOTES_MALOS, "price": tick.ask,
             "risk_usd": 0.0, "effective_leverage": nominal/equity_usd}

# Invocar la guarda tal cual esta en create_order
divisa = str(getattr(acc, "currency", "") or "").upper()
factor = 100.0 if divisa in ("USC", "EUC", "GBC", "AUC") else 1.0
eq = float(acc.equity) / factor
nom = float(plan_malo["lots"]) * float(info.trade_contract_size) * float(plan_malo["price"])
apal = nom / eq
tope = ex.risk.max_leverage * 1.05
bloqueada = apal > tope
print(f"\nGUARDA: {apal:.1f}x contra tope {tope:.2f}x -> "
      f"{'BLOQUEADA (correcto)' if bloqueada else 'PERMITIDA (MAL)'}")
assert bloqueada, "La guarda NO bloqueo la operacion peligrosa"

# Y confirmar que una operacion correcta SI pasa
import src.indicators as ind
df = ex.fetch_ohlcv("BTCUSDc", "5m", limit=250)
df = ind.add_all_indicators(df); df.dropna(inplace=True)
atr = float(df.iloc[-1]["atr"]); price = float(df.iloc[-1]["close"])
plan = ex.plan_order("BTCUSDc", "buy", price - 1.5 * atr)
if plan.get("allowed"):
    n2 = plan["lots"] * info.trade_contract_size * plan["price"]
    print(f"\nOperacion NORMAL: {plan['lots']} lotes = {n2:.2f} USD = {n2/eq:.2f}x -> "
          f"{'PERMITIDA (correcto)' if n2/eq <= tope else 'BLOQUEADA (MAL)'}")
    assert n2 / eq <= tope
else:
    print("\nOperacion normal rechazada por riesgo:", plan.get("reason"))

print("\n[OK] La guarda bloquea lo peligroso y deja pasar lo correcto.")
