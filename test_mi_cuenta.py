"""Que hara el bot con TU saldo real: 1575 USC = 15.75 USD."""
from src.leverage_risk import LeverageRiskManager

SALDO_USC = 1575.0
CENT = 100.0
SALDO_USD = SALDO_USC / CENT

print("="*74)
print(f"  TU CUENTA: {SALDO_USC:.0f} USC = {SALDO_USD:.2f} USD reales | Exness 1:2000")
print("="*74)

# (simbolo, precio aprox, contract_size cuenta CENT, pip, stop en pips)
CASOS = [
    ("EURUSD", 1.0800, 1000, 0.0001, 30),
    ("GBPUSD", 1.2700, 1000, 0.0001, 30),
    ("USDJPY", 150.00, 1000, 0.01,   30),
    ("XAUUSD", 4000.0,    1, 0.10,  100),   # oro: contrato cent = 1 onza (VERIFICAR en MT5)
]

print(f"\n{'Simbolo':<9}{'Lotes':>8}{'Nominal':>11}{'Apal.':>8}{'Riesgo':>10}{'% cuenta':>10}  Estado")
print("-"*74)

for sym, price, contract, pip, stop_pips in CASOS:
    rm = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0,
                             min_free_margin_pct=50, max_daily_loss_pct=3.0)
    stop_dist = stop_pips * pip
    p = rm.plan_position(
        balance=SALDO_USD, equity=SALDO_USD, used_margin=0,
        price=price, stop_loss_price=price - stop_dist,
        contract_size=contract, min_lot=0.01, lot_step=0.01,
        broker_leverage=2000)
    if p.allowed:
        nominal = p.lots * contract * price
        print(f"{sym:<9}{p.lots:>8.2f}{nominal:>10.2f}${p.effective_leverage:>7.2f}x"
              f"{p.risk_usd:>9.2f}${p.risk_usd/SALDO_USD*100:>9.1f}%  OK")
    else:
        motivo = p.reason[:34]
        print(f"{sym:<9}{'-':>8}{'-':>11}{'-':>8}{'-':>10}{'-':>10}  RECHAZADO: {motivo}")

print("-"*74)

# Detalle del caso principal
rm = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0,
                         min_free_margin_pct=50, max_daily_loss_pct=3.0)
p = rm.plan_position(balance=SALDO_USD, equity=SALDO_USD, used_margin=0,
                     price=1.08, stop_loss_price=1.08-0.0030,
                     contract_size=1000, min_lot=0.01, lot_step=0.01, broker_leverage=2000)
print(f"\nDETALLE EURUSD (stop de 30 pips):")
print(f"  Si el trade SALE MAL -> pierdes {p.risk_usd:.2f} USD "
      f"({p.risk_usd/SALDO_USD*100:.1f}% de tu cuenta)")
print(f"  Si el trade SALE BIEN (TP 1:2) -> ganas ~{p.risk_usd*2:.2f} USD")
print(f"  Limite diario: el bot se pausa al perder {SALDO_USD*0.03:.2f} USD (3%)")
print(f"  Operaciones seguidas malas hasta la pausa: ~{int(SALDO_USD*0.03/p.risk_usd)}")
print("\n" + "="*74)
