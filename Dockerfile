# syntax=docker/dockerfile:1

# =============================================================
#  1) Dependencias
# =============================================================
FROM python:3.12-slim AS deps

WORKDIR /app

# gcc: compilar paquetes sin wheel | libgomp1: requerido por LightGBM (modelo ML)
# tzdata: que TZ funcione (horas de avisos y resumenes) | curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgomp1 \
    tzdata \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Dependencias Python (core), con versiones fijadas y probadas ---
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# --- Dependencias opcionales de Exness/MT5 ---
# Capa aparte y con "|| true": si fallan, el bot sigue funcionando con Binance.
COPY requirements-exness.txt .
RUN pip install --no-cache-dir -r requirements-exness.txt -c constraints.txt \
    || echo "AVISO: dependencias de Exness no instaladas (el modo Binance sigue OK)"

# --- Codigo fuente y scripts ---
COPY src/ src/
COPY main.py trend_backtest.py spot_backtest.py futures_backtest.py futures_backtest_v2.py ./

# =============================================================
#  2) Pruebas: si alguna falla, NO se construye la imagen final
#     (reglas de riesgo, estrategia, Telegram). Sin red ni credenciales.
# =============================================================
FROM deps AS test
COPY test_*.py ./
RUN set -e; for t in test_leverage.py test_cent.py test_guarda.py test_trend.py test_telegram.py; do \
        echo "== $t"; python "$t" > "/tmp/$t.log" 2>&1 || { cat "/tmp/$t.log"; exit 1; }; \
    done \
    && echo "TODAS LAS PRUEBAS PASARON" > /tmp/tests-ok

# =============================================================
#  3) Imagen final
# =============================================================
FROM deps AS runtime

# Obliga a que la etapa de pruebas se ejecute (BuildKit omite etapas no usadas)
COPY --from=test /tmp/tests-ok /app/.tests-ok

# Directorios de datos (montados como volumen en runtime)
RUN mkdir -p data/models data/market_data data/mt5

# Puerto del dashboard web
EXPOSE 8080

# Usuario no-root por seguridad
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# Logs sin buffer (para 'docker logs') y sin .pyc en el volumen
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Healthcheck: el panel web debe responder
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/stats || exit 1

CMD ["python", "main.py"]
