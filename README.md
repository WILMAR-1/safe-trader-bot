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

### ¿Por qué el bot no abre operaciones en Exness?

Casi siempre es el tamaño de la cuenta. Con 15 USD, arriesgar un 1% son 0,15 USD por operación, y el **lote mínimo (0,01)** de BTCUSD, ETHUSD o XAUUSD ya pierde más que eso al tocar el stop: el bot rechaza la orden en vez de arriesgar de más. Ahora:

- Cada señal rechazada llega por Telegram con el motivo (como mucho un aviso cada 6 h por símbolo).
- `/diagnostico` (o `/diagnostico EURUSDc XAUUSDc`) indica, símbolo por símbolo, si tu cuenta puede operarlo, con cuántos lotes y qué riesgo.
- Con saldos pequeños, usa símbolos de lote pequeño (forex en cuenta cent, p. ej. `EURUSDc`) y añádelos desde **🩺 Diagnóstico** (botón ➕) o con `/addpair EURUSDc`. Subir `RISK_PER_TRADE_PCT` también lo desbloquea, pero cada operación perdida pesa más.

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

Escribe `/start` y todo se maneja con **botones**:

- **Menú principal** con estado, balance y accesos directos. Al navegar, la pantalla se actualiza en el mismo mensaje en vez de llenar el chat, y el botón fijo **📋 Menú** te devuelve siempre al inicio.
- **💼 Posiciones** (Binance y Exness) con resultado en vivo, stop y un botón para **cerrar a mercado**, que pide confirmación.
- **📜 Historial** paginado, **💰 Ganancias** (24 h, 7 días y por mercado) y **🛡 Riesgo** con las reglas activas.
- **🩺 Diagnóstico** (Exness): explica símbolo por símbolo si tu cuenta puede operarlo y lo añade con un botón.
- **⚙️ Ajustes** con botones +/−: inversión, máximo de posiciones y mercados. En Exness se valida el nombre exacto del símbolo (`eurusdc` → `EURUSDc`) y se sugieren nombres parecidos.
- **🔔 Avisos** a tu gusto: operaciones, rechazos por riesgo, resumen diario, horario o ninguno, y modo sin sonido. Las alertas de riesgo suenan siempre.
- **Detener** y **cerrar posición** siempre piden confirmación. Solo responde a tu `TELEGRAM_CHAT_ID`.

Los comandos de texto siguen disponibles:

| Comando | Qué hace |
|---|---|
| `/menu` `/estado` `/posiciones` `/historial` | Pantallas principales |
| `/ganancias` `/riesgo` `/diagnostico [SÍMBOLOS]` | Resultados, límites, por qué no opera |
| `/ajustes` `/avisos` `/mercados` | Configuración |
| `/pausar` `/reanudar` `/detener` | Control del bot |
| `/setstake 50` `/setmaxtrades 3` `/addpair SOL` `/removepair SOL` | Atajos |
| `/login` | Conectar a Exness (MT5) |

Los nombres antiguos en inglés (`/status`, `/trades`, `/risk`…) siguen funcionando.

```bash
python test_telegram.py   # recorre todas las pantallas y botones con una API de Telegram simulada
```

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

## Estrategia `trend`: lo que ha funcionado en público

Con `STRATEGY=trend` el bot usa **seguimiento de tendencia**, la familia de estrategias con más evidencia pública, auditada y fuera de muestra:

| Caso público | Qué demostró | Cómo se aplica aquí |
|---|---|---|
| Tortugas de Dennis/Eckhardt (1983-88) | Rupturas de canal + tamaño por volatilidad + stop a 2N | Entrada por ruptura Donchian de 55 velas, stop inicial a 2×ATR, salida por canal de 20 |
| Fondos CTA / managed futures (Winton, AQR, Man AHL) | Décadas de track record con reglas sistemáticas y simples | Pocas reglas, pocos parámetros, sin take profit fijo |
| *Time Series Momentum* (Moskowitz, Ooi, Pedersen, 2012) y *A Century of Evidence on Trend-Following* (Hurst, Ooi, Pedersen, 2017) | El retorno pasado de un activo predice el siguiente | Filtro: solo largos si el momentum de 90 velas es positivo |
| Liu & Tsyvinski (2021) | Momentum de series temporales también en cripto | Mismo filtro en BTC/ETH/etc. |
| *Volatility-Managed Portfolios* (Moreira & Muir, 2017) | Reducir la exposición cuando sube la volatilidad mejora el Sharpe | Posición = riesgo fijo / distancia del stop (más volatilidad → posición más pequeña) |
| Freqtrade (*protections*) | Cooldown y StoplossGuard evitan rachas de pérdidas en bucle | `COOLDOWN_MINUTES`, `STOPLOSS_GUARD_COUNT` |
| Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014) | La mayoría de backtests "ganadores" son suerte por probar muchas variantes | El backtest informa el nº de variantes y el Sharpe deflactado (DSR) |

Qué esperar: un **win rate bajo (35-45 %)** es normal; gana con pocas operaciones grandes. Úsala en **1h o 4h**: en 5m las comisiones se comen la ventaja. En Exness abre largos y cortos, y el trailing cierra solo las posiciones abiertas por el bot (se identifican por su *magic number*).

```bash
python main.py download 1095     # 3 años de datos (incluye 1h y 4h)
python trend_backtest.py 1h      # walk-forward: ajusta con el pasado, valida en el futuro
python trend_backtest.py 4h --short
python test_trend.py             # sin lookahead, sin ventaja en ruido, costes, tamaño, protecciones
```

El backtest ejecuta la señal en la apertura de la vela siguiente, cobra comisión y slippage, rellena los stops con hueco al peor precio y lo compara con buy & hold en el mismo periodo. **Activa `trend` con dinero real solo si el veredicto fuera de muestra es positivo con tus datos**, y después de semanas en `dry_run`.

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
├── strategy.py        Estrategia original (safe)
├── trend_strategy.py  Seguimiento de tendencia (trend)
├── trend_backtest.py  Backtest realista + walk-forward
├── performance.py     Sharpe, Sortino, Calmar, PSR, DSR
├── protections.py     Cooldown y StoplossGuard
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
