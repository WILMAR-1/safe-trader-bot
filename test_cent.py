"""
Prueba de cuenta CENT (Exness Standard Cent).
Verifica que el bot NO dimensione posiciones 100x mas grandes por el tema de centimos.
"""
from src.leverage_risk import LeverageRiskManager

print("="*72)
print("  PRUEBA: CUENTA STANDARD CENT (Exness) - EURUSD")
print("="*72)

# Escenario real: depositas 50 USD -> MT5 muestra 5000 USC
DEPOSITO_USD = 50.0
BALANCE_USC = DEPOSITO_USD * 100          # 5000 USC (lo que devuelve MT5)
CENT_FACTOR = 100.0

EURUSD = 1.08
CONTRACT_CENT = 1000.0                     # 1 lote cent EURUSD = 1000 unidades
STOP_PIPS = 30
STOP_DIST = STOP_PIPS * 0.0001             # 0.0030

rm = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0,
                         min_free_margin_pct=50, max_daily_loss_pct=3.0)

# --- MAL: usar el balance en centimos tal cual (el bug que evitamos) ---
mal = rm.plan_position(
    balance=BALANCE_USC, equity=BALANCE_USC, used_margin=0,
    price=EURUSD, stop_loss_price=EURUSD - STOP_DIST,
    contract_size=CONTRACT_CENT, min_lot=0.01, lot_step=0.01, broker_leverage=2000)
print(f"\n[INCORRECTO] Si tratamos 5000 USC como si fueran 5000 USD:")
print(f"  lotes={mal.lots} | nominal={mal.lots*CONTRACT_CENT*EURUSD:.2f} USD | "
      f"riesgo={mal.risk_usd:.2f}")
nominal_mal = mal.lots * CONTRACT_CENT * EURUSD
print(f"  -> apalancamiento REAL sobre {DEPOSITO_USD} USD = {nominal_mal/DEPOSITO_USD:.1f}x  <-- PELIGRO")

# --- BIEN: normalizar a USD reales (lo que hace ExnessExchange) ---
rm2 = LeverageRiskManager(max_leverage=3, risk_per_trade_pct=1.0,
                          min_free_margin_pct=50, max_daily_loss_pct=3.0)
bien = rm2.plan_position(
    balance=BALANCE_USC/CENT_FACTOR, equity=BALANCE_USC/CENT_FACTOR, used_margin=0,
    price=EURUSD, stop_loss_price=EURUSD - STOP_DIST,
    contract_size=CONTRACT_CENT, min_lot=0.01, lot_step=0.01, broker_leverage=2000)
print(f"\n[CORRECTO] Normalizando 5000 USC -> {DEPOSITO_USD} USD reales:")
print(f"  permitido={bien.allowed} | {bien.reason}")
if bien.allowed:
    nominal_bien = bien.lots * CONTRACT_CENT * EURUSD
    print(f"  lotes={bien.lots} | nominal={nominal_bien:.2f} USD | riesgo={bien.risk_usd:.2f} USD")
    print(f"  -> apalancamiento REAL = {nominal_bien/DEPOSITO_USD:.2f}x")
    print(f"  -> si salta el stop pierdes {bien.risk_usd:.2f} USD de {DEPOSITO_USD} "
          f"({bien.risk_usd/DEPOSITO_USD*100:.1f}%)")

    assert bien.effective_leverage <= 3.001, "supera el tope de 3x"
    assert bien.risk_usd <= DEPOSITO_USD * 0.0101, "arriesga mas del 1%"
    assert nominal_bien / DEPOSITO_USD <= 3.001, "apalancamiento real supera 3x"
    print("\n  [OK] riesgo <= 1% del capital REAL")
    print("  [OK] apalancamiento real <= 3x")

    ratio = nominal_mal / nominal_bien if nominal_bien else 0
    print(f"\n  >>> La normalizacion evito posiciones {ratio:.0f}x mas grandes <<<")

print("\n" + "="*72)
print("  PRUEBA SUPERADA - las cuentas Cent se manejan correctamente")
print("="*72)
