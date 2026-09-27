# SafeTraderBot

Bot de trading automático con gestión de riesgo estricta, panel web en tiempo real y control por Telegram.

Soporta dos brokers:

| Broker | Mercados | Apalancamiento | Cómo conecta |
|---|---|---|---|
| **Binance** | Cripto spot | Ninguno | API REST (CCXT) |
| **Exness** | Forex, metales, cripto (CFD) | Con tope configurable | MetaTrader 5 |

---

## ⚠️ Aviso importante

Este es un **proyecto personal de aprendizaje**. El trading automático puede hacerte **perder todo tu dinero**.

- La estrategia incluida **no está validada como rentable**. En las pruebas walk-forward sobre datos históricos perdió dinero (ver `spot_backtest.py` y `futures_backtest_v2.py`).
- Lo que **sí está probado** es la **gestión de riesgo**: hay tests automáticos que verifican los límites de apalancamiento, el dimensionado de posición y las pausas de protección.
- Empieza siempre en `dry_run` y con una cuenta demo.
- Esto no es asesoramiento financiero.

---

## Gestión de riesgo

El corazón del proyecto. Toda orden pasa por estas reglas antes de enviarse:

| Regla | Por defecto | Qué hace |
|---|---|---|
| Riesgo por operación | 1% | Calcula los lotes para que el stop cueste exactamente eso |
| Tope de apalancamiento | 3x | Aunque el broker ofrezca 1:2000, el bot se limita |
| Stop loss | Obligatorio | Sin stop, la orden se rechaza |
| Margen libre mínimo | 50% | No abre si te dejaría sin colchón |
| Margin level mínimo | 300% | Rechaza si te acercaría a liquidación |
| Pérdida diaria máxima | 3% | Se autopausa y reanuda al día siguiente |
| Guarda final | — | Recalcula el nominal justo antes de enviar y bloquea si excede el tope |

Las cuentas **Cent** (saldo en USC/EUC/GBC/AUC) se normalizan a su valor real para que los cálculos no salgan 100x inflados.

```bash
python test_leverage.py    # 7 pruebas de las reglas de apalancamiento
python test_cent.py        # normalización de cuentas cent
```

---

## Panel web

`http://localhost:8080`

- Balance real de la cuenta (libre, en órdenes, total)
- Indicador **TEST** vs **DINERO REAL**
- Pausar / reanudar / detener
- Editar stake, máximo de operaciones y mercados en caliente
- Actividad real del mercado en vivo
- Operaciones abiertas e historial

## Telegram

| Comando | Qué hace |
|---|---|
| `/status` | Estado, balance, profit, win rate |
| `/trades` | Operaciones abiertas con P/L |
| `/history` | Últimas operaciones cerradas |
| `/risk` | Margen y riesgo de liquidación |
| `/login` | Conectar a Exness (MT5) |
| `/pause` `/resume` `/stop` | Control del bot |
| `/setstake` `/setmaxtrades` | Cambiar parámetros |
| `/pairs` `/addpair` `/removepair` | Gestionar mercados |

---

## Puesta en marcha

```bash
cp .env.example .env     # y edita tus credenciales
```

### Binance (spot, sin apalancamiento)

```bash
docker compose up -d
```

### Exness (MetaTrader 5)

```bash
docker compose --profile exness up -d
```

Levanta dos contenedores: la terminal MT5 bajo Wine (accesible por VNC en `http://localhost:3000`) y el bot, que le habla por RPyC.

> **Primer arranque:** MetaTrader exige iniciar sesión una vez desde su ventana gráfica. Entra por VNC, haz el login, y a partir de ahí el bot se conecta solo. `Dockerfile.mt5` instala la terminal *con marca del broker*, necesaria para que el canal IPC funcione.

---

## Backtesting

```bash
python main.py download 365   # descargar datos históricos
python main.py backtest       # backtest sobre datos locales
python spot_backtest.py       # spot con validación walk-forward
python futures_backtest_v2.py # futuros con ATR + walk-forward
```

Los backtests dividen los datos en dos mitades: se ajusta en la primera y se valida en la segunda. Si solo gana en la primera, es sobreajuste.

---

## Estructura

```
src/
├── config.py          Configuración desde .env
├── exchange.py        Binance via CCXT
├── exness.py          Exness via MetaTrader 5
├── leverage_risk.py   Reglas de apalancamiento (con tests)
├── risk_manager.py    Gestión de riesgo spot
├── strategy.py        Señales de entrada y salida
├── indicators.py      EMA, RSI, MACD, ADX, Bollinger, ATR
├── backtester.py      Motor de backtesting
├── ml_engine.py       Modelo de ML opcional
├── database.py        SQLite local
├── web.py             API y panel
├── telegram_bot.py    Comandos remotos
└── bot.py             Motor principal
```

## Seguridad

- API keys **sin permiso de retiro**, con IP restringida
- `.env` fuera de git; `.env.example` solo con marcadores
- Contenedor con usuario no-root
- Datos en SQLite local, sin telemetría

## Licencia

MIT
