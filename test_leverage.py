"""Pruebas de las reglas de apalancamiento. Verifica que protegen la cuenta."""
from src.leverage_risk import LeverageRiskManager

BTC = 60000.0
CONTRACT = 1.0  # 1 lote BTCUSD en Exness = 1 BTC

def show(t, p):
    print(f"\n{t}")
    d = p.as_dict()
    print(f"  permitido={d['allowed']} | {d['reason']}")
    if d['allowed']:
        print(f"  lotes={d['lots']} | riesgo={d['risk_usd']}$ | margen={d['margin_required']}$ "
              f"| apal={d['effective_leverage']}x | margin_level={d['projected_margin_level']}%")

print("="*70); print("  PRUEBAS DE REGLAS DE APALANCAMIENTO"); print("="*70)

# --- 1. Cuenta 1000$, stop 1% -> riesgo debe ser 10$ (1%) ---
rm = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0, min_free_margin_pct=50, max_daily_loss_pct=3)
p = rm.plan_position(balance=1000, equity=1000, used_margin=0, price=BTC,
                     stop_loss_price=BTC*0.99, contract_size=CONTRACT, min_lot=0.01, lot_step=0.01)
show("1) Cuenta 1000$, stop -1% (riesgo objetivo: 10$ = 1%)", p)
assert p.allowed, "deberia permitir"
assert p.risk_usd <= 10.01, f"riesgo {p.risk_usd} supera el 1%"
assert p.effective_leverage <= 3.001, f"apalancamiento {p.effective_leverage} supera 3x"
print("  [OK] riesgo <= 1% y apalancamiento <= 3x")

# --- 2. SIN stop loss -> debe RECHAZAR ---
p = rm.plan_position(balance=1000, equity=1000, used_margin=0, price=BTC,
                     stop_loss_price=0, contract_size=CONTRACT)
show("2) Sin stop loss (debe rechazar)", p)
assert not p.allowed
print("  [OK] rechaza operar sin stop loss")

# --- 3. Cuenta pequena 30$ -> lote minimo no alcanzable, debe rechazar ---
rm3 = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0)
p = rm3.plan_position(balance=30, equity=30, used_margin=0, price=BTC,
                      stop_loss_price=BTC*0.99, contract_size=CONTRACT, min_lot=0.01, lot_step=0.01)
show("3) Cuenta de 30$ en BTC (lote minimo 0.01 = 600$ nominal)", p)
assert not p.allowed
print("  [OK] rechaza en vez de sobreapalancar una cuenta pequena")

# --- 4. Exness da 1:2000 y el stop es ultra-cercano -> debe recortar a NUESTRO tope 3x ---
rm4 = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0)
p = rm4.plan_position(balance=10000, equity=10000, used_margin=0, price=BTC,
                      stop_loss_price=BTC*0.9999, contract_size=CONTRACT, min_lot=0.01,
                      lot_step=0.01, broker_leverage=2000)
show("4) Exness ofrece 1:2000 y stop ultra-cercano -> debe recortar a NUESTRO tope 3x", p)
assert p.allowed, f"deberia permitir: {p.reason}"
assert p.effective_leverage <= 3.001, f"NO respeto el tope: {p.effective_leverage}x"
print(f"  [OK] el broker permitia 2000x, el bot se limito a {p.effective_leverage:.2f}x")

# --- 4b. El tope de exposicion nos protege del riesgo real ---
perdida_si_cae_10pct = p.lots * CONTRACT * BTC * 0.10
print(f"  [OK] si BTC cayera 10%: perderias {perdida_si_cae_10pct:.0f}$ de 10000$ "
      f"({perdida_si_cae_10pct/10000*100:.0f}%) - sin el tope serian miles")
assert perdida_si_cae_10pct < 10000, "el tope deberia evitar perdidas catastroficas"

# --- 5. Margen ya usado alto -> debe rechazar por margin level ---
rm5 = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0, min_free_margin_pct=50)
p = rm5.plan_position(balance=10000, equity=10000, used_margin=9000, price=BTC,
                      stop_loss_price=BTC*0.99, contract_size=CONTRACT)
show("5) Ya hay 9000$ de margen usado de 10000$ (debe rechazar)", p)
assert not p.allowed
print("  [OK] protege el margen libre / margin level")

# --- 6. Limite de perdida diaria ---
rm6 = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0, max_daily_loss_pct=3.0)
rm6.plan_position(balance=1000, equity=1000, used_margin=0, price=BTC,
                  stop_loss_price=BTC*0.99, contract_size=CONTRACT)  # inicializa el dia
rm6.register_close(-10); rm6.register_close(-10); rm6.register_close(-11)  # -31$ = -3.1%
p = rm6.plan_position(balance=969, equity=969, used_margin=0, price=BTC,
                      stop_loss_price=BTC*0.99, contract_size=CONTRACT)
show("6) Tras perder 31$ (-3.1%) en el dia (limite 3%)", p)
assert not p.allowed and "diaria" in p.reason.lower()
print("  [OK] se pausa solo al llegar al limite diario")

# --- 7. Alerta de liquidacion en posiciones abiertas ---
rm7 = LeverageRiskManager()
ok, msg = rm7.check_open_positions(equity=1000, used_margin=800)   # level 125%
print(f"\n7) Margin level bajo -> {msg}")
assert not ok
ok2, msg2 = rm7.check_open_positions(equity=1000, used_margin=100)  # level 1000%
print(f"   Margin level sano -> {msg2}")
assert ok2
print("  [OK] detecta riesgo de liquidacion")

print("\n" + "="*70)
print("  TODAS LAS PRUEBAS PASARON - las reglas protegen la cuenta")
print("="*70)
