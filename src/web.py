"""
Dashboard web en tiempo real para SafeTraderBot.
API REST + WebSocket-like con Server-Sent Events para actualizaciones en vivo.
"""

import json
import logging
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, Response, request
from src.database import Database
from src.config import Config

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

# Referencias globales al bot (se setean desde bot.py)
_bot_instance = None
_events = []
_markets_cache = []


def set_bot(bot):
    global _bot_instance
    _bot_instance = bot


def push_event(event_type: str, data: dict):
    """Enviar evento a los clientes conectados."""
    _events.append({
        "type": event_type,
        "data": data,
        "time": datetime.now().isoformat(),
    })
    # Mantener solo los ultimos 100 eventos
    if len(_events) > 100:
        _events.pop(0)


@app.route("/")
def dashboard():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    stats = _bot_instance.risk_manager.get_stats()
    stats["mode"] = "LIVE" if Config.is_live() else "DRY RUN"
    if Config.is_exness():
        stats["exchange"] = "EXNESS"
        stats["pairs"] = Config.EXNESS_SYMBOLS
        stats["broker_ready"] = getattr(_bot_instance, "exness_ready", False)
    else:
        stats["exchange"] = Config.EXCHANGE_NAME.upper()
        stats["pairs"] = Config.PAIR_WHITELIST
        stats["broker_ready"] = True
    stats["timeframe"] = Config.TIMEFRAME
    stats["uptime"] = str(datetime.now() - _bot_instance.start_time).split(".")[0]
    stats["stake_amount"] = Config.STAKE_AMOUNT
    stats["max_open_trades"] = Config.MAX_OPEN_TRADES
    stats["running"] = _bot_instance.running
    return jsonify(stats)


# =============================================================
#  CONTROL EN TIEMPO REAL
# =============================================================
@app.route("/api/control/state")
def api_control_state():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    return jsonify(_bot_instance.get_control_state())


@app.route("/api/control/pause", methods=["POST"])
def api_pause():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    return jsonify(_bot_instance.pause("Pausado desde el panel web"))


@app.route("/api/control/resume", methods=["POST"])
def api_resume():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    return jsonify(_bot_instance.resume())


@app.route("/api/control/stop", methods=["POST"])
def api_stop():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    return jsonify(_bot_instance.stop())


@app.route("/api/control/stake", methods=["POST"])
def api_set_stake():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    try:
        value = (request.get_json(force=True) or {}).get("value")
        return jsonify({"ok": True, "stake_amount": _bot_instance.set_stake(value)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/control/max_trades", methods=["POST"])
def api_set_max_trades():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    try:
        value = (request.get_json(force=True) or {}).get("value")
        return jsonify({"ok": True, "max_open_trades": _bot_instance.set_max_trades(value)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/control/pairs", methods=["POST"])
def api_set_pairs():
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    try:
        pairs = (request.get_json(force=True) or {}).get("pairs", [])
        return jsonify({"ok": True, "pairs": _bot_instance.set_pairs(pairs)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/markets")
def api_markets():
    """Lista de mercados disponibles para elegir (cacheada)."""
    global _markets_cache
    if not _bot_instance:
        return jsonify([])
    if not _markets_cache:
        _markets_cache = _bot_instance.exchange.list_markets(quote="USDT")
    return jsonify(_markets_cache)


@app.route("/api/trades/open")
def api_open_trades():
    if not _bot_instance:
        return jsonify([])
    trades = _bot_instance.db.get_open_trades()
    # Agregar precio actual y profit en vivo
    for trade in trades:
        try:
            ticker = _bot_instance.exchange.fetch_ticker(trade["symbol"])
            current_price = ticker["last"]
            trade["current_price"] = current_price
            trade["current_profit_pct"] = round(
                ((current_price / trade["entry_price"]) - 1) * 100, 2
            )
            trade["current_profit"] = round(
                (current_price - trade["entry_price"]) * trade["amount"], 2
            )
        except Exception:
            trade["current_price"] = 0
            trade["current_profit_pct"] = 0
            trade["current_profit"] = 0
    return jsonify(trades)


@app.route("/api/trades/history")
def api_trade_history():
    if not _bot_instance:
        return jsonify([])
    return jsonify(_bot_instance.db.get_trade_history(50))


@app.route("/api/prices")
def api_prices():
    """Precios actuales de todos los pares."""
    if not _bot_instance:
        return jsonify({})
    prices = {}
    for pair in Config.PAIR_WHITELIST:
        pair = pair.strip()
        try:
            ticker = _bot_instance.exchange.fetch_ticker(pair)
            prices[pair] = {
                "last": ticker["last"],
                "change": ticker.get("percentage", 0),
                "high": ticker.get("high", 0),
                "low": ticker.get("low", 0),
                "volume": ticker.get("baseVolume", 0),
            }
        except Exception:
            prices[pair] = {"last": 0, "change": 0}
    return jsonify(prices)


@app.route("/api/balance")
def api_balance():
    """Balance REAL de la cuenta (virtual en dry_run)."""
    if not _bot_instance:
        return jsonify({"error": "Bot no iniciado"}), 503
    try:
        data = _bot_instance.exchange.get_balance_summary()
        data["mode"] = "LIVE" if Config.is_live() else "DRY RUN"
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/market/trades")
def api_market_trades():
    """Operaciones reales recientes del mercado (pares activos)."""
    if not _bot_instance:
        return jsonify([])
    out = []
    for pair in Config.PAIR_WHITELIST[:4]:  # limitar para no saturar
        out += _bot_instance.exchange.fetch_recent_trades(pair.strip(), limit=8)
    out = sorted(out, key=lambda x: x.get("time") or 0, reverse=True)[:30]
    return jsonify(out)


@app.route("/api/events")
def api_events():
    """Stream de eventos en tiempo real (Server-Sent Events)."""
    def generate():
        last_index = len(_events)
        while True:
            if len(_events) > last_index:
                for event in _events[last_index:]:
                    yield f"data: {json.dumps(event)}\n\n"
                last_index = len(_events)
            else:
                # Heartbeat cada 5 segundos
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
            import time
            time.sleep(5)

    return Response(generate(), mimetype="text/event-stream")


def start_web(bot, host="0.0.0.0", port=8080):
    """Iniciar el servidor web en un hilo separado."""
    set_bot(bot)
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    logger.info("Dashboard web iniciado en http://localhost:%d", port)
