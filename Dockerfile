FROM python:3.12-slim

WORKDIR /app

# Dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Dependencias Python (core) ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Dependencias opcionales de Exness/MT5 ---
# Se instalan en una capa aparte y con "|| true": si fallan, el bot sigue
# funcionando con Binance en vez de romper el build entero.
COPY requirements-exness.txt .
RUN pip install --no-cache-dir -r requirements-exness.txt \
    || echo "AVISO: dependencias de Exness no instaladas (el modo Binance sigue OK)"

# --- Codigo fuente ---
COPY src/ src/
COPY main.py .

# Directorios de datos (montados como volumen en runtime)
RUN mkdir -p data/models data/market_data data/mt5

# Puerto del dashboard web
EXPOSE 8080

# Usuario no-root por seguridad
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# Salida de logs sin buffer (para ver el progreso en 'docker logs')
ENV PYTHONUNBUFFERED=1

# Healthcheck: el panel web debe responder
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8080/api/stats || exit 1

CMD ["python", "main.py"]
