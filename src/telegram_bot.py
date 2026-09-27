"""
Bot de Telegram: control completo del bot de trading desde el movil.

Experiencia de usuario:
- Menu con botones (teclado inline). Cada pantalla se actualiza EN EL MISMO
  mensaje al navegar, sin llenar el chat de mensajes.
- Boton fijo "Menu" abajo (teclado persistente) para volver siempre al inicio.
- Confirmacion antes de cualquier accion delicada (detener, cerrar posicion).
- Ajustes con botones (+/-), sin tener que recordar comandos.
- Avisos claros con emoji, en lenguaje humano, con botones de accion.
- Preferencias de avisos (operaciones, rechazos, resumen diario/horario, silencio).
- Formato HTML con escape de todo lo dinamico: un "_" en un motivo ya no
  rompe el mensaje (con Markdown, Telegram lo rechazaba en silencio).

Los comandos de texto siguen funcionando (/estado, /posiciones, /setstake 50...),
incluidos los antiguos en ingles (/status, /trades...).
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

import requests

from src.config import Config

logger = logging.getLogger(__name__)

PREFS_PATH = "data/telegram_prefs.json"
DEFAULT_PREFS = {"trades": True, "rejections": True, "summary": "daily", "silent": False}
MAX_LEN = 4096
HISTORY_PAGE = 5

# Motivos de cierre -> lenguaje humano
EXIT_REASONS = {
    "STOPLOSS": "Stop loss",
    "STOP_ATR": "Stop loss",
    "TRAILING_ATR": "Stop dinámico (protege ganancias)",
    "TRAILING_STOP": "Stop dinámico (protege ganancias)",
    "CANAL_SALIDA": "Fin de la tendencia",
    "TAKE_PROFIT": "Toma de ganancias",
    "TAKE_PROFIT_HIGH": "Toma de ganancias",
    "TAKE_PROFIT_MID": "Toma de ganancias",
    "SELL_SIGNAL": "Señal de venta",
    "TIMEOUT": "Tiempo máximo",
    "TIMEOUT_48H": "Tiempo máximo (48 h)",
    "TIMEOUT_24H_NO_PROFIT": "24 h sin ganancia",
    "MANUAL": "Cierre manual",
}


# =====================================================================
#  UTILIDADES DE FORMATO
# =====================================================================
def esc(value) -> str:
    """Escapar texto dinamico para parse_mode=HTML."""
    return html.escape(str(value), quote=False)


def human_reason(reason: str) -> str:
    reason = reason or ""
    key = reason.split(" (")[0].strip()
    detail = reason[len(key):].strip()
    label = EXIT_REASONS.get(key, key.replace("_", " ").capitalize() or "—")
    detail = detail.strip("() ")
    return f"{label} · {detail}" if detail else label


def fmt_price(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v == 0:
        return "0"
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    if abs(v) >= 10:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if abs(v) >= 1:
        return f"{v:.5f}".rstrip("0").rstrip(".")  # forex: 5 decimales
    return f"{v:.6f}".rstrip("0").rstrip(".")


def fmt_money(v, cur: str = "") -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{v:,.2f} {cur}".strip()


def fmt_signed(v, cur: str = "", pct: bool = False) -> str:
    v = float(v or 0)
    body = f"{v:+.2f}%" if pct else f"{v:+,.2f} {cur}".strip()
    return body


def pl_icon(v) -> str:
    v = float(v or 0)
    return "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")


def bar(value: float, maximum: float, width: int = 10) -> str:
    """Barra de progreso ▰▱ (p.ej. drawdown frente a su limite)."""
    if maximum <= 0:
        return ""
    filled = max(0, min(width, round(value / maximum * width)))
    return "▰" * filled + "▱" * (width - filled)


def human_duration(since) -> str:
    try:
        start = since if isinstance(since, datetime) else datetime.fromisoformat(str(since))
    except (TypeError, ValueError):
        return "—"
    secs = max(0, int((datetime.now() - start).total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def button(text: str, data: str) -> dict:
    assert len(data.encode()) <= 64, data  # limite de Telegram para callback_data
    return {"text": text, "callback_data": data}


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Trocear por lineas para no pasar el limite de 4096 caracteres."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


# =====================================================================
#  BOT DE TELEGRAM
# =====================================================================
class TelegramCommander:
    """Menu interactivo + comandos + notificaciones."""

    MENU_KEYBOARD = {
        "keyboard": [[{"text": "📋 Menú"}, {"text": "📊 Estado"}, {"text": "💼 Posiciones"}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }
    REPLY_BUTTONS = {"📋 Menú": "m", "📊 Estado": "s", "💼 Posiciones": "p"}

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.bot_instance = None
        self.running = False
        self.last_update_id = 0
        self._thread = None
        self._args: list[str] = []
        self._awaiting: tuple[str, datetime] | None = None  # (que dato, caduca)
        self._last_summary: datetime | None = None
        self._quiet_control = False  # la accion vino de Telegram: no repetir el aviso
        self.prefs = self._load_prefs()

    # ------------------------------------------------------------------
    #  CICLO DE VIDA
    # ------------------------------------------------------------------
    def set_bot(self, bot):
        self.bot_instance = bot

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._set_commands()
        logger.info("Telegram Commander iniciado - escuchando comandos")

    def stop(self):
        self.running = False

    # ------------------------------------------------------------------
    #  TRANSPORTE (API de Telegram)
    # ------------------------------------------------------------------
    def _api(self, method: str, payload: dict) -> dict:
        """Llamada a la API con reintento ante limite de frecuencia (429)."""
        for _ in range(3):
            try:
                r = requests.post(f"{self.base_url}/{method}", json=payload, timeout=15)
                data = r.json()
            except Exception as e:
                logger.error("Telegram %s fallo: %s", method, e)
                return {"ok": False, "description": str(e)}
            if data.get("ok"):
                return data
            retry = (data.get("parameters") or {}).get("retry_after")
            if data.get("error_code") == 429 and retry:
                time.sleep(min(float(retry), 30))
                continue
            return data
        return {"ok": False, "description": "demasiados reintentos"}

    def send(self, text: str, buttons: list | None = None, silent: bool | None = None,
             parse_mode: str = "HTML", reply_keyboard: dict | None = None) -> int | None:
        """Enviar mensaje. Devuelve el message_id del ultimo trozo.
        Si Telegram no puede interpretar el formato, reenvia como texto plano
        (antes esos mensajes se perdian sin que nadie se enterara)."""
        if silent is None:
            silent = self.prefs.get("silent", False)
        chunks = split_message(text)
        msg_id = None
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": self.chat_id, "text": chunk, "parse_mode": parse_mode,
                       "disable_web_page_preview": True, "disable_notification": bool(silent)}
            last = i == len(chunks) - 1
            if last and buttons:
                payload["reply_markup"] = {"inline_keyboard": buttons}
            elif last and reply_keyboard:
                payload["reply_markup"] = reply_keyboard
            data = self._api("sendMessage", payload)
            if not data.get("ok") and "parse" in str(data.get("description", "")).lower():
                payload.pop("parse_mode", None)
                payload["text"] = re.sub(r"<[^>]+>", "", chunk)
                data = self._api("sendMessage", payload)
            if not data.get("ok"):
                logger.error("Telegram no entrego el mensaje: %s", data.get("description"))
            else:
                msg_id = data["result"]["message_id"]
        return msg_id

    def edit(self, message_id: int, text: str, buttons: list | None = None):
        payload = {"chat_id": self.chat_id, "message_id": message_id, "text": text[:MAX_LEN],
                   "parse_mode": "HTML", "disable_web_page_preview": True,
                   "reply_markup": {"inline_keyboard": buttons or []}}
        data = self._api("editMessageText", payload)
        desc = str(data.get("description", ""))
        if not data.get("ok") and "not modified" not in desc:
            # Mensaje demasiado antiguo o borrado: enviar uno nuevo
            self.send(text, buttons)

    def _answer(self, callback_id: str, text: str = "", alert: bool = False):
        self._api("answerCallbackQuery", {"callback_query_id": callback_id,
                                          "text": text[:200], "show_alert": alert})

    def _typing(self):
        self._api("sendChatAction", {"chat_id": self.chat_id, "action": "typing"})

    def _set_commands(self):
        commands = [
            ("menu", "📋 Menú principal"),
            ("estado", "📊 Estado y balance"),
            ("posiciones", "💼 Posiciones abiertas"),
            ("historial", "📜 Operaciones cerradas"),
            ("ganancias", "💰 Ganancias por mercado"),
            ("riesgo", "🛡 Riesgo y límites"),
            ("diagnostico", "🩺 Por qué no opera"),
            ("ajustes", "⚙️ Inversión, mercados y avisos"),
            ("pausar", "⏸ Pausar nuevas entradas"),
            ("reanudar", "▶️ Reanudar"),
            ("login", "🔑 Conectar a Exness (MT5)"),
            ("ayuda", "❓ Ayuda"),
        ]
        self._api("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})

    def _poll_loop(self):
        while self.running:
            try:
                r = requests.get(f"{self.base_url}/getUpdates", params={
                    "offset": self.last_update_id + 1, "timeout": 30,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                }, timeout=35)
                data = r.json()
                for update in data.get("result", []) if data.get("ok") else []:
                    self.last_update_id = update["update_id"]
                    try:
                        self._handle_update(update)
                    except Exception as e:
                        logger.error("Error procesando update de Telegram: %s", e, exc_info=True)
            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                logger.error("Error en Telegram poll: %s", e)
                time.sleep(5)

    # ------------------------------------------------------------------
    #  PREFERENCIAS
    # ------------------------------------------------------------------
    def _load_prefs(self) -> dict:
        try:
            with open(PREFS_PATH, encoding="utf-8") as f:
                return {**DEFAULT_PREFS, **json.load(f)}
        except Exception:
            return dict(DEFAULT_PREFS)

    def _save_prefs(self):
        try:
            os.makedirs(os.path.dirname(PREFS_PATH), exist_ok=True)
            with open(PREFS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.prefs, f)
        except Exception as e:
            logger.error("No se pudieron guardar las preferencias de Telegram: %s", e)

    # ------------------------------------------------------------------
    #  ENRUTADO
    # ------------------------------------------------------------------
    COMMANDS = {
        "/start": "start", "/menu": "m",
        "/estado": "s", "/status": "s",
        "/posiciones": "p", "/trades": "p",
        "/historial": "h:0", "/history": "h:0",
        "/ganancias": "g", "/profit": "g",
        "/riesgo": "r", "/risk": "r",
        "/diagnostico": "d",
        "/ajustes": "cfg", "/config": "cfg",
        "/mercados": "mk", "/pairs": "mk",
        "/avisos": "n",
        "/pausar": "pause", "/pause": "pause",
        "/reanudar": "resume", "/resume": "resume",
        "/detener": "stop?", "/stop": "stop?",
        "/precios": "px", "/prices": "px",
        "/ayuda": "hlp", "/help": "hlp",
        "/login": "login", "/ml": "ml",
        "/setstake": "cmd_stake", "/setmaxtrades": "cmd_max",
        "/addpair": "cmd_add", "/removepair": "cmd_rm",
    }

    def _handle_update(self, update: dict):
        if "callback_query" in update:
            cq = update["callback_query"]
            chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
            if chat != self.chat_id:
                logger.warning("Boton pulsado desde chat no autorizado: %s", chat)
                return
            self._on_callback(cq)
            return

        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        if chat != self.chat_id:
            logger.warning("Mensaje de chat no autorizado: %s", chat)
            return
        text = (message.get("text") or "").strip()
        if not text:
            return

        if text in self.REPLY_BUTTONS:
            self._show(self.REPLY_BUTTONS[text])
            return

        if not text.startswith("/"):
            if self._awaiting and datetime.now() < self._awaiting[1]:
                self._on_input(self._awaiting[0], text)
            else:
                self._awaiting = None
                self.send("No entendí ese mensaje. Usa los botones 👇",
                          [[button("📋 Abrir menú", "m")]])
            return

        parts = text.split()
        command = parts[0].lower().split("@")[0]
        self._args = parts[1:]
        action = self.COMMANDS.get(command)
        if not action:
            self.send(f"No conozco el comando <code>{esc(command)}</code>.",
                      [[button("📋 Menú", "m"), button("❓ Ayuda", "hlp")]])
            return
        try:
            self._run_command(action)
        except Exception as e:
            logger.error("Error en comando %s: %s", command, e, exc_info=True)
            self.send(f"⚠️ No pude completar <code>{esc(command)}</code>: {esc(e)}")

    def _run_command(self, action: str):
        self._quiet_control = True
        try:
            self._command(action)
        finally:
            self._quiet_control = False

    def _command(self, action: str):
        if action == "start":
            self.send(self._welcome_text(), reply_keyboard=self.MENU_KEYBOARD)
            self._show("m")
        elif action == "cmd_stake":
            if self._args:
                self._apply_stake(self._args[0])
            else:
                self._show("cfg")
        elif action == "cmd_max":
            if self._args:
                self._apply_max(self._args[0])
            else:
                self._show("cfg")
        elif action == "cmd_add":
            if self._args:
                self._add_market(self._args[0])
            else:
                self._ask("add", "✍️ Escribe el mercado que quieres añadir "
                                 "(p. ej. <code>SOL</code> o <code>EURUSDc</code>):")
        elif action == "cmd_rm":
            if self._args:
                self._remove_market(self._args[0])
            else:
                self._show("mk")
        elif action == "d" and self._args:
            self._run_diagnosis(None, symbols=list(self._args))
        elif action == "login":
            self._login()
        else:
            self._show(action)

    def _show(self, data: str, message_id: int | None = None):
        """Pintar una pantalla: editando el mensaje del boton o enviando uno nuevo."""
        if data == "d":
            self._run_diagnosis(message_id)
            return
        screen = self._screen(data)
        if screen is None:
            return
        text, buttons = screen
        if message_id:
            self.edit(message_id, text, buttons)
        else:
            self.send(text, buttons, silent=True)

    def _on_callback(self, cq: dict):
        data = cq.get("data", "")
        msg_id = (cq.get("message") or {}).get("message_id")
        cid = cq["id"]
        try:
            toast = self._on_action(data, msg_id)
            self._answer(cid, toast or "")
        except Exception as e:
            logger.error("Error en boton %s: %s", data, e, exc_info=True)
            self._answer(cid, f"⚠️ {e}", alert=True)

    def _on_action(self, data: str, msg_id: int) -> str | None:
        """Acciones de los botones. Devuelve un aviso breve (toast) o None."""
        self._quiet_control = True
        try:
            return self._dispatch(data, msg_id)
        finally:
            self._quiet_control = False

    def _dispatch(self, data: str, msg_id: int) -> str | None:
        bot = self.bot_instance
        if data == "pause":
            self._require_bot().pause("Pausado desde Telegram")
            self._show("m", msg_id)
            return "⏸ Pausado: no abrirá nuevas operaciones"
        if data == "resume":
            self._require_bot().resume()
            self._show("m", msg_id)
            return "▶️ Reanudado"
        if data == "stop!":
            self._require_bot()
            stats = bot.risk_manager.get_stats()
            self.edit(msg_id, "⏹ <b>Bot detenido</b>\n\n"
                              f"Balance final: <b>{esc(fmt_money(stats['balance'], self._currency()))}</b>\n"
                              "Para volver a arrancarlo: <code>docker compose up -d</code>")
            bot.stop()
            return "Bot detenido"
        if data.startswith("cl!:"):
            self._close_position(data[4:], msg_id)
            return None
        if data.startswith("st:"):
            self._apply_stake(Config.STAKE_AMOUNT + float(data[3:]), msg_id)
            return None
        if data.startswith("mx:"):
            self._apply_max(Config.MAX_OPEN_TRADES + int(data[3:]), msg_id)
            return None
        if data == "st?":
            self._ask("stake", f"✍️ Escribe la nueva inversión por operación "
                               f"(ahora {esc(fmt_money(Config.STAKE_AMOUNT, Config.STAKE_CURRENCY))}):")
            return None
        if data == "add":
            self._ask("add", "✍️ Escribe el mercado que quieres añadir "
                             "(p. ej. <code>SOL</code> o <code>EURUSDc</code>):")
            return None
        if data.startswith("rm:"):
            self._remove_market(data[3:], msg_id)
            return None
        if data.startswith("da:"):
            self._add_market(data[3:])
            return None
        if data.startswith("n:"):
            key = data[2:]
            if key == "summary":
                order = ["daily", "hourly", "off"]
                cur = self.prefs.get("summary", "daily")
                self.prefs["summary"] = order[(order.index(cur) + 1) % len(order)] if cur in order else "daily"
            else:
                self.prefs[key] = not self.prefs.get(key, True)
            self._save_prefs()
            self._show("n", msg_id)
            return "Guardado"
        if data == "x":
            self._awaiting = None
            self._show("m", msg_id)
            return "Cancelado"
        if data == "login":
            self._login()
            return None
        self._show(data, msg_id)
        return None

    def _require_bot(self):
        if not self.bot_instance:
            raise RuntimeError("El bot aún no ha arrancado")
        return self.bot_instance

    def _ask(self, what: str, prompt: str):
        self._awaiting = (what, datetime.now() + timedelta(minutes=5))
        self.send(prompt, [[button("✖️ Cancelar", "x")]])

    def _on_input(self, what: str, text: str):
        self._awaiting = None
        self._quiet_control = True
        try:
            if what == "stake":
                self._apply_stake(text)
            elif what == "add":
                self._add_market(text)
        finally:
            self._quiet_control = False

    # ------------------------------------------------------------------
    #  PANTALLAS
    # ------------------------------------------------------------------
    def _currency(self) -> str:
        if Config.is_exness():
            return "USD"
        return Config.STAKE_CURRENCY

    def _open_count(self) -> int:
        """Posiciones abiertas reales (en Exness viven en MT5, no en la base de datos)."""
        bot = self.bot_instance
        try:
            if getattr(bot, "is_exness", False):
                return len(bot.exness.get_positions()) if getattr(bot, "exness_ready", False) else 0
            return len(bot.db.get_open_trades())
        except Exception:
            return bot.risk_manager.open_trades

    def _state_badge(self) -> str:
        bot = self.bot_instance
        if not bot:
            return "⚪ Arrancando"
        if not bot.running:
            return "⏹ Detenido"
        if getattr(bot, "is_exness", False) and not getattr(bot, "exness_ready", False):
            return "🟡 Esperando login en MT5"
        if bot.risk_manager.is_paused:
            return "⏸ Pausado"
        return "🟢 Activo"

    @staticmethod
    def _mode_badge() -> str:
        return "💵 DINERO REAL" if Config.is_live() else "🧪 SIMULACIÓN"

    @staticmethod
    def _stamp() -> str:
        return f"\n\n<i>Actualizado {datetime.now():%H:%M:%S}</i>"

    @staticmethod
    def _nav(*extra_rows, refresh: str | None = None) -> list:
        rows = [list(r) for r in extra_rows if r]
        last = [button("⬅️ Menú", "m")]
        if refresh:
            last.append(button("🔄 Actualizar", refresh))
        rows.append(last)
        return rows

    def _welcome_text(self) -> str:
        broker = "Exness (MetaTrader 5)" if Config.is_exness() else Config.EXCHANGE_NAME.capitalize()
        return (
            "👋 <b>¡Hola! Soy tu SafeTraderBot.</b>\n\n"
            f"Opero en <b>{esc(broker)}</b> en modo <b>{self._mode_badge()}</b>.\n"
            "Desde aquí puedes ver el estado, tus posiciones y cambiar ajustes con botones.\n"
            "El botón <b>📋 Menú</b> de abajo te trae siempre de vuelta."
        )

    def _screen(self, data: str):
        bot = self.bot_instance
        if data not in ("m", "hlp") and not bot:
            return "⏳ El bot está arrancando. Prueba en unos segundos.", [[button("🔄 Reintentar", data)]]
        if data == "m":
            return self._screen_menu()
        if data == "s":
            return self._screen_status()
        if data == "p":
            return self._screen_positions()
        if data.startswith("h:"):
            return self._screen_history(int(data[2:] or 0))
        if data == "g":
            return self._screen_profit()
        if data == "r":
            return self._screen_risk()
        if data == "cfg":
            return self._screen_settings()
        if data == "mk":
            return self._screen_markets()
        if data == "n":
            return self._screen_notifications()
        if data == "hlp":
            return self._screen_help()
        if data == "px":
            return self._screen_prices()
        if data == "ml":
            return self._screen_ml()
        if data == "stop?":
            return ("⚠️ <b>¿Detener el bot por completo?</b>\n\n"
                    "Dejará de vigilar las posiciones abiertas. Para volver a arrancarlo "
                    "hará falta reiniciar el contenedor.\n\n"
                    "Si solo quieres que no abra operaciones nuevas, usa <b>⏸ Pausar</b>.",
                    [[button("⏹ Sí, detener", "stop!"), button("✖️ Cancelar", "m")],
                     [button("⏸ Mejor pausar", "pause")]])
        if data.startswith("cl?:"):
            pid = data[4:]
            pos = next((p for p in bot.list_open_positions() if p["id"] == pid), None)
            if not pos:
                return "Esa posición ya no está abierta.", self._nav(refresh="p")
            return (f"⚠️ <b>¿Cerrar {esc(pos['symbol'])} ahora a mercado?</b>\n\n"
                    f"Resultado actual: {pl_icon(pos['pl'])} "
                    f"<b>{esc(fmt_signed(pos['pl'], self._currency()))}</b> "
                    f"({esc(fmt_signed(pos['pl_pct'], pct=True))})",
                    [[button("✅ Sí, cerrar", f"cl!:{pid}"), button("✖️ Cancelar", "p")]])
        logger.warning("Pantalla desconocida: %s", data)
        return self._screen_menu()

    def _screen_menu(self):
        bot = self.bot_instance
        lines = [f"<b>📋 SafeTraderBot</b>  ·  {self._mode_badge()}", f"Estado: <b>{self._state_badge()}</b>"]
        if bot:
            st = bot.risk_manager.get_stats()
            cur = self._currency()
            lines.append(f"Balance: <b>{esc(fmt_money(st['balance'], cur))}</b>  "
                         f"{pl_icon(st['profit_total'])} {esc(fmt_signed(st['profit_pct'], pct=True))}")
            lines.append(f"Posiciones: <b>{self._open_count()}/{Config.MAX_OPEN_TRADES}</b>  ·  "
                         f"Estrategia: <b>{esc(Config.STRATEGY)}</b> ({esc(Config.TIMEFRAME)})")
        paused = bool(bot and bot.risk_manager.is_paused)
        rows = [
            [button("📊 Estado", "s"), button("💼 Posiciones", "p")],
            [button("📜 Historial", "h:0"), button("💰 Ganancias", "g")],
            [button("🛡 Riesgo", "r"), button("🩺 Diagnóstico", "d")],
            [button("⚙️ Ajustes", "cfg"), button("🔔 Avisos", "n")],
            [button("▶️ Reanudar", "resume") if paused else button("⏸ Pausar", "pause"),
             button("⏹ Detener", "stop?")],
            [button("❓ Ayuda", "hlp")],
        ]
        if bot and getattr(bot, "is_exness", False) and not getattr(bot, "exness_ready", False):
            rows.insert(0, [button("🔑 Conectar a Exness", "login")])
        return "\n".join(lines) + self._stamp(), rows

    def _screen_status(self):
        bot = self.bot_instance
        cur = self._currency()
        if getattr(bot, "is_exness", False) and not getattr(bot, "exness_ready", False):
            return ("🟡 <b>Esperando a MetaTrader 5</b>\n\n"
                    "El bot está encendido pero todavía no conectado a Exness.\n"
                    f"Cuenta: <code>{esc(Config.EXNESS_LOGIN)}</code>\n"
                    f"Servidor: <code>{esc(Config.EXNESS_SERVER)}</code>\n\n"
                    "Inicia sesión una vez en la terminal MT5 (por VNC) o pulsa <b>Conectar</b>.",
                    self._nav([button("🔑 Conectar a Exness", "login")], refresh="s"))
        st = bot.risk_manager.get_stats()
        lines = [
            f"<b>📊 Estado</b>  ·  {self._mode_badge()}",
            f"{self._state_badge()}"
            + (f" — <i>{esc(st['pause_reason'])}</i>" if st["is_paused"] and st["pause_reason"] else ""),
            "",
            f"💰 Balance: <b>{esc(fmt_money(st['balance'], cur))}</b>",
            f"{pl_icon(st['profit_total'])} Resultado: <b>{esc(fmt_signed(st['profit_total'], cur))}</b> "
            f"({esc(fmt_signed(st['profit_pct'], pct=True))})",
        ]
        if getattr(bot, "is_exness", False):
            s = bot.exness.get_balance_summary()
            if "error" not in s:
                lines.append(f"🧾 Equity: <b>{esc(fmt_money(s.get('equity'), cur))}</b>  ·  "
                             f"Margen libre: {esc(fmt_money(s.get('free'), cur))}")
                if s.get("used"):
                    lines.append(f"📐 Margin level: <b>{s.get('margin_level', 0):.0f}%</b>")
        dd_max = bot.risk_manager.max_drawdown_pct
        lines += [
            f"📉 Drawdown: {bar(st['drawdown_pct'], dd_max)} {st['drawdown_pct']:.1f}% / {dd_max:.0f}%",
            "",
            f"🎯 Acierto: <b>{st['win_rate']:.0f}%</b> ({st['winning_trades']}✅ / {st['losing_trades']}❌)",
            f"💼 Abiertas: <b>{self._open_count()}/{Config.MAX_OPEN_TRADES}</b>",
            f"🧠 Estrategia: <b>{esc(Config.STRATEGY)}</b> · {esc(Config.TIMEFRAME)}",
            f"⏱ Encendido desde hace {esc(human_duration(bot.start_time))}",
        ]
        return "\n".join(lines) + self._stamp(), self._nav(
            [button("💼 Posiciones", "p"), button("🛡 Riesgo", "r")], refresh="s")

    def _screen_positions(self):
        bot = self.bot_instance
        cur = self._currency()
        positions = bot.list_open_positions()
        if not positions:
            return ("<b>💼 Posiciones abiertas</b>\n\nNo hay posiciones abiertas ahora mismo.\n"
                    "<i>El bot entra solo cuando todas sus condiciones se cumplen.</i>" + self._stamp(),
                    self._nav([button("🩺 ¿Por qué no opera?", "d")], refresh="p"))
        total = sum(p["pl"] for p in positions)
        lines = [f"<b>💼 Posiciones abiertas</b> ({len(positions)})",
                 f"Total: {pl_icon(total)} <b>{esc(fmt_signed(total, cur))}</b>", ""]
        rows = []
        for p in positions:
            side = "🔼 Largo" if p["side"] == "buy" else "🔽 Corto"
            size = f"{fmt_price(p['size'])} {p['size_unit']}".strip()
            lines.append(f"{pl_icon(p['pl'])} <b>{esc(p['symbol'])}</b>  {side}  ·  {esc(size)}")
            lines.append(f"   Entrada {esc(fmt_price(p['entry']))} → ahora {esc(fmt_price(p['current']))}")
            lines.append(f"   <b>{esc(fmt_signed(p['pl'], cur))}</b> ({esc(fmt_signed(p['pl_pct'], pct=True))})"
                         f"  ·  hace {esc(human_duration(p['since']))}")
            if p.get("stop"):
                lines.append(f"   🛑 Stop: {esc(fmt_price(p['stop']))}")
            if not p.get("is_bot", True):
                lines.append("   <i>Abierta manualmente (el bot no la gestiona)</i>")
            lines.append("")
            rows.append([button(f"❌ Cerrar {p['symbol']}", f"cl?:{p['id']}")])
        return "\n".join(lines).rstrip() + self._stamp(), self._nav(*rows, refresh="p")

    def _screen_history(self, page: int):
        bot = self.bot_instance
        cur = self._currency()
        trades = bot.db.get_trade_history(200)
        if getattr(bot, "is_exness", False):
            note = "\n<i>En Exness el historial completo está en MetaTrader 5.</i>"
        else:
            note = ""
        if not trades:
            return "<b>📜 Historial</b>\n\nTodavía no hay operaciones cerradas." + note, self._nav()
        pages = max(1, -(-len(trades) // HISTORY_PAGE))
        page = max(0, min(page, pages - 1))
        chunk = trades[page * HISTORY_PAGE:(page + 1) * HISTORY_PAGE]
        wins = sum(1 for t in trades if (t.get("profit") or 0) >= 0)
        total = sum(t.get("profit") or 0 for t in trades)
        lines = [f"<b>📜 Historial</b>  ·  {len(trades)} operaciones",
                 f"Total: {pl_icon(total)} <b>{esc(fmt_signed(total, cur))}</b>  ·  "
                 f"acierto {wins / len(trades) * 100:.0f}%", ""]
        for t in chunk:
            pl = t.get("profit") or 0
            when = (t.get("exit_time") or "")[:16].replace("T", " ")
            lines.append(f"{pl_icon(pl)} <b>{esc(t['symbol'])}</b>  "
                         f"<b>{esc(fmt_signed(pl, cur))}</b> ({esc(fmt_signed(t.get('profit_pct') or 0, pct=True))})")
            lines.append(f"   {esc(fmt_price(t['entry_price']))} → {esc(fmt_price(t.get('exit_price')))}"
                         f"  ·  {esc(human_reason(t.get('exit_reason') or ''))}")
            lines.append(f"   <i>{esc(when)}</i>")
        nav = []
        if page > 0:
            nav.append(button("◀️ Anteriores", f"h:{page - 1}"))
        nav.append(button(f"{page + 1}/{pages}", f"h:{page}"))
        if page < pages - 1:
            nav.append(button("Siguientes ▶️", f"h:{page + 1}"))
        return "\n".join(lines) + note, self._nav(nav)

    def _screen_profit(self):
        bot = self.bot_instance
        cur = self._currency()
        st = bot.risk_manager.get_stats()
        trades = bot.db.get_trade_history(1000)
        per = {}
        for t in trades:
            d = per.setdefault(t["symbol"], {"n": 0, "pl": 0.0, "w": 0})
            d["n"] += 1
            d["pl"] += t.get("profit") or 0
            d["w"] += (t.get("profit") or 0) >= 0
        day_ago = datetime.now() - timedelta(days=1)
        week_ago = datetime.now() - timedelta(days=7)

        def since(limit):
            return sum(t.get("profit") or 0 for t in trades
                       if t.get("exit_time") and datetime.fromisoformat(t["exit_time"]) >= limit)
        today, week = since(day_ago), since(week_ago)
        lines = [
            "<b>💰 Ganancias</b>",
            f"Inicial: {esc(fmt_money(st['initial_balance'], cur))}  →  ahora <b>{esc(fmt_money(st['balance'], cur))}</b>",
            f"{pl_icon(st['profit_total'])} Total: <b>{esc(fmt_signed(st['profit_total'], cur))}</b> "
            f"({esc(fmt_signed(st['profit_pct'], pct=True))})",
            f"{pl_icon(today)} Últimas 24 h: <b>{esc(fmt_signed(today, cur))}</b>",
            f"{pl_icon(week)} Últimos 7 días: <b>{esc(fmt_signed(week, cur))}</b>",
        ]
        if per:
            lines += ["", "<b>Por mercado</b>"]
            best = max(abs(d["pl"]) for d in per.values()) or 1
            for sym, d in sorted(per.items(), key=lambda x: -x[1]["pl"]):
                lines.append(f"{pl_icon(d['pl'])} <b>{esc(sym)}</b> {esc(fmt_signed(d['pl'], cur))}")
                lines.append(f"   {bar(abs(d['pl']), best, 8)} {d['n']} ops · acierto {d['w'] / d['n'] * 100:.0f}%")
        else:
            lines += ["", "<i>Aún no hay operaciones cerradas.</i>"]
        return "\n".join(lines) + self._stamp(), self._nav([button("📜 Historial", "h:0")], refresh="g")

    def _screen_risk(self):
        bot = self.bot_instance
        rm = bot.risk_manager
        st = rm.get_stats()
        lines = [
            "<b>🛡 Riesgo</b>",
            f"Estado: <b>{'⏸ Pausado' if st['is_paused'] else '🟢 Normal'}</b>"
            + (f" — <i>{esc(st['pause_reason'])}</i>" if st["is_paused"] and st["pause_reason"] else ""),
            "",
            f"📉 Drawdown: {bar(st['drawdown_pct'], rm.max_drawdown_pct)} "
            f"{st['drawdown_pct']:.1f}% (máx {rm.max_drawdown_pct:.0f}%)",
            f"🔻 Pérdidas seguidas: <b>{st['consecutive_losses']}</b>",
            f"📅 Pérdidas hoy: <b>{rm.daily_losses}/{rm.max_daily_losses}</b>",
            f"💼 Abiertas: <b>{self._open_count()}/{rm.max_open_trades}</b>",
            "",
            "<b>Reglas</b>",
        ]
        if Config.is_exness():
            lines += [
                f"• Riesgo por operación: <b>{Config.RISK_PER_TRADE_PCT:g}%</b> del balance",
                f"• Apalancamiento máximo: <b>{Config.MAX_LEVERAGE:g}x</b>",
                f"• Margen libre mínimo: <b>{Config.MIN_FREE_MARGIN_PCT:g}%</b>",
                f"• Pérdida diaria máxima: <b>{Config.MAX_DAILY_LOSS_PCT:g}%</b>",
                "• Stop loss obligatorio en cada orden",
            ]
            if getattr(bot, "exness_ready", False):
                s = bot.exness.get_balance_summary()
                if "error" not in s:
                    lines += ["", f"📐 {esc(s.get('margin_msg', ''))}"]
        elif Config.STRATEGY == "trend":
            lines += [
                f"• Riesgo por operación: <b>{Config.RISK_PER_TRADE_PCT:g}%</b> (tamaño según volatilidad)",
                f"• Stop inicial: <b>{Config.TREND_STOP_ATR:g}× ATR</b>, dinámico a {Config.TREND_TRAIL_ATR:g}× ATR",
                f"• Inversión máxima por operación: <b>{esc(fmt_money(Config.STAKE_AMOUNT, Config.STAKE_CURRENCY))}</b>",
            ]
        else:
            lines += [
                f"• Stop loss: <b>{Config.STOPLOSS:.1%}</b>",
                f"• Inversión por operación: <b>{esc(fmt_money(Config.STAKE_AMOUNT, Config.STAKE_CURRENCY))}</b>",
            ]
        if getattr(bot, "protections", None):
            lines.append(f"• Pausa tras cerrar un mercado: <b>{Config.COOLDOWN_MINUTES} min</b>")
            if Config.STOPLOSS_GUARD_COUNT:
                lines.append(f"• {Config.STOPLOSS_GUARD_COUNT} stops en 24 h → bloqueo de 12 h")
        extra = [button("▶️ Reanudar", "resume")] if st["is_paused"] else []
        return "\n".join(lines) + self._stamp(), self._nav(extra, [button("🩺 Diagnóstico", "d")], refresh="r")

    def _screen_settings(self):
        cur = Config.STAKE_CURRENCY
        lines = [
            "<b>⚙️ Ajustes</b>",
            "",
            f"💵 Inversión por operación: <b>{esc(fmt_money(Config.STAKE_AMOUNT, cur))}</b>",
            f"💼 Máximo de posiciones a la vez: <b>{Config.MAX_OPEN_TRADES}</b>",
            f"🌐 Mercados: <b>{len(self._markets())}</b>",
            "",
            f"<i>Estrategia {esc(Config.STRATEGY)} · {esc(Config.TIMEFRAME)} · modo {self._mode_badge()}.\n"
            "Estos tres se cambian en el archivo .env y requieren reiniciar.</i>",
        ]
        rows = [
            [button("−10", "st:-10"), button("−1", "st:-1"), button("+1", "st:1"), button("+10", "st:10")],
            [button("✏️ Escribir inversión", "st?")],
            [button("➖ Posición", "mx:-1"), button(f"Máx: {Config.MAX_OPEN_TRADES}", "cfg"),
             button("➕ Posición", "mx:1")],
            [button("🌐 Mercados", "mk"), button("🔔 Avisos", "n")],
        ]
        if Config.is_exness():
            # En Exness el tamano lo decide el riesgo por operacion, no una inversion fija
            lines[2] = (f"🎯 Riesgo por operación: <b>{Config.RISK_PER_TRADE_PCT:g}%</b> del balance "
                        "<i>(se cambia en .env)</i>")
            rows = rows[2:]
        return "\n".join(lines), self._nav(*rows)

    def _markets(self) -> list:
        if self.bot_instance:
            return self.bot_instance.active_symbols()
        return list(Config.EXNESS_SYMBOLS if Config.is_exness() else Config.PAIR_WHITELIST)

    def _screen_markets(self):
        markets = self._markets()
        lines = [f"<b>🌐 Mercados</b> ({len(markets)})", "", "Toca uno para quitarlo:"]
        rows, row = [], []
        for m in markets:
            row.append(button(f"✖️ {m}", f"rm:{m}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([button("➕ Añadir mercado", "add")])
        if Config.is_exness():
            lines.append("\n<i>Consejo: con saldo pequeño usa pares con lote pequeño "
                         "(p. ej. EURUSDc). El 🩺 Diagnóstico te dice cuáles puedes operar.</i>")
            rows.append([button("🩺 Diagnóstico", "d")])
        rows.append([button("⚙️ Ajustes", "cfg")])
        return "\n".join(lines), self._nav(*rows)

    def _screen_notifications(self):
        p = self.prefs

        def onoff(v):
            return "✅" if v else "⬜"
        summary = {"daily": "Diario", "hourly": "Cada hora", "off": "Desactivado"}.get(p["summary"], "Diario")
        text = ("<b>🔔 Avisos</b>\n\n"
                "Elige qué quieres recibir. Las alertas de riesgo llegan siempre.\n\n"
                f"{onoff(p['trades'])} Aperturas y cierres de operaciones\n"
                f"{onoff(p['rejections'])} Señales rechazadas por riesgo\n"
                f"📰 Resumen: <b>{summary}</b>\n"
                f"{onoff(p['silent'])} Silenciosos (sin sonido, salvo alertas)")
        rows = [
            [button(f"{onoff(p['trades'])} Operaciones", "n:trades")],
            [button(f"{onoff(p['rejections'])} Rechazos", "n:rejections")],
            [button(f"📰 Resumen: {summary}", "n:summary")],
            [button(f"{onoff(p['silent'])} Sin sonido", "n:silent")],
        ]
        return text, self._nav(*rows)

    def _screen_prices(self):
        lines = ["<b>📈 Precios</b>", ""]
        bot = self.bot_instance
        for sym in self._markets():
            try:
                t = bot.exchange.fetch_ticker(sym)
                last = t.get("last") or t.get("bid")
                change = t.get("percentage")
                chg = f"  {pl_icon(change)} {change:+.2f}%" if change is not None else ""
                lines.append(f"<b>{esc(sym)}</b>  {esc(fmt_price(last))}{chg}")
            except Exception:
                lines.append(f"<b>{esc(sym)}</b>  —")
        return "\n".join(lines) + self._stamp(), self._nav(refresh="px")

    def _screen_ml(self):
        eng = getattr(getattr(self.bot_instance, "strategy", None), "ml_engine", None)
        if not eng:
            return "<b>🤖 Machine Learning</b>\n\nNo está activo con la estrategia actual.", self._nav()
        s = eng.get_stats()
        return ("<b>🤖 Machine Learning</b>\n\n"
                f"Estado: <b>{'Listo' if s['is_ready'] else 'Sin entrenar'}</b>\n"
                f"Modelo: {esc(s['model_type'])}\n"
                f"Precisión (test): {s['test_accuracy']:.1f}%\n"
                f"Confianza mínima: {s['min_confidence']:.0%}\n"
                f"Último entrenamiento: {esc(s['last_train'] or 'Nunca')}", self._nav())

    def _screen_help(self):
        text = (
            "<b>❓ Ayuda</b>\n\n"
            "Casi todo se hace con los botones del <b>📋 Menú</b>. Si prefieres escribir:\n\n"
            "/estado — balance y resultado\n"
            "/posiciones — lo que tienes abierto (y cerrar)\n"
            "/historial — operaciones cerradas\n"
            "/ganancias — resultado por mercado\n"
            "/riesgo — límites y pausas\n"
            "/diagnostico — por qué no abre operaciones\n"
            "/ajustes — inversión, posiciones, mercados\n"
            "/pausar · /reanudar — parar o seguir abriendo operaciones\n\n"
            "<b>Atajos</b>\n"
            "<code>/setstake 50</code> · <code>/setmaxtrades 3</code>\n"
            "<code>/addpair SOL</code> · <code>/removepair SOL</code>\n"
            "<code>/diagnostico EURUSDc XAUUSDc</code>\n\n"
            "<b>Glosario</b>\n"
            "• <b>Stop loss</b>: precio al que se cierra para limitar la pérdida.\n"
            "• <b>Stop dinámico</b>: sube con el precio para proteger ganancias.\n"
            "• <b>Drawdown</b>: cuánto has caído desde tu máximo.\n"
            "• <b>Pausar</b>: no abre nuevas, pero sigue vigilando las abiertas."
        )
        return text, self._nav()

    # ------------------------------------------------------------------
    #  ACCIONES
    # ------------------------------------------------------------------
    def _apply_stake(self, value, msg_id: int | None = None):
        bot = self._require_bot()
        try:
            v = float(str(value).replace(",", "."))
            v = round(v, 2)
            if v < 10:
                raise ValueError("el mínimo es 10")
            bot.set_stake(v)
        except ValueError as e:
            self.send(f"⚠️ Valor no válido ({esc(e)}). Ejemplo: <code>50</code>",
                      [[button("✏️ Probar otra vez", "st?"), button("⚙️ Ajustes", "cfg")]])
            return
        if msg_id:
            self._show("cfg", msg_id)
        else:
            self.send(f"✅ Inversión por operación: <b>{esc(fmt_money(v, Config.STAKE_CURRENCY))}</b>",
                      [[button("⚙️ Ajustes", "cfg"), button("📋 Menú", "m")]])

    def _apply_max(self, value, msg_id: int | None = None):
        bot = self._require_bot()
        try:
            n = int(value)
            if n > 20:
                raise ValueError("el máximo razonable es 20")
            bot.set_max_trades(n)
        except ValueError as e:
            self.send(f"⚠️ Valor no válido ({esc(e)}). Ejemplo: <code>/setmaxtrades 3</code>")
            return
        if msg_id:
            self._show("cfg", msg_id)
        else:
            self.send(f"✅ Hasta <b>{n}</b> posiciones a la vez.", [[button("⚙️ Ajustes", "cfg")]])

    def _normalize_market(self, text: str) -> str:
        text = text.strip()
        if Config.is_exness():
            return text.replace("/", "")  # el bot resuelve mayusculas/sufijo con MT5
        text = text.upper()
        return text if "/" in text else f"{text}/{Config.STAKE_CURRENCY}"

    def _add_market(self, text: str):
        bot = self._require_bot()
        sym = self._normalize_market(text)
        markets = self._markets()
        if sym in markets:
            self.send(f"ℹ️ <b>{esc(sym)}</b> ya está en tu lista.", [[button("🌐 Mercados", "mk")]])
            return
        try:
            result = bot.set_pairs(markets + [sym])
        except ValueError as e:
            self.send(f"⚠️ {esc(e)}", [[button("✏️ Probar otro", "add"), button("🌐 Mercados", "mk")]])
            return
        added = result[-1]
        tip = (f"\n\n¿Puede operarlo tu cuenta? Pulsa 🩺 Diagnóstico." if Config.is_exness() else "")
        self.send(f"✅ Añadido <b>{esc(added)}</b>. Ahora opero {len(result)} mercados.{tip}",
                  [[button("🌐 Mercados", "mk"), button("🩺 Diagnóstico", "d")]])

    def _remove_market(self, text: str, msg_id: int | None = None):
        bot = self._require_bot()
        markets = self._markets()
        sym = text if text in markets else self._normalize_market(text)
        match = next((m for m in markets if m.lower() == sym.lower()), None)
        if not match:
            self.send(f"ℹ️ <b>{esc(sym)}</b> no estaba en la lista.", [[button("🌐 Mercados", "mk")]])
            return
        if len(markets) == 1:
            self.send("⚠️ No puedes quitar el último mercado. Añade otro primero.",
                      [[button("➕ Añadir", "add")]])
            return
        bot.set_pairs([m for m in markets if m != match])
        if msg_id:
            self._show("mk", msg_id)
        else:
            self.send(f"✅ Quitado <b>{esc(match)}</b>.", [[button("🌐 Mercados", "mk")]])

    def _close_position(self, pid: str, msg_id: int):
        bot = self._require_bot()
        self.edit(msg_id, "⏳ Cerrando posición…")

        def worker():
            try:
                ok, info = bot.close_position_manual(pid)
            except Exception as e:
                ok, info = False, f"{type(e).__name__}: {e}"
            if ok:
                self._show("p", msg_id)  # el aviso de cierre llega aparte con el resultado
            else:
                self.edit(msg_id, f"⚠️ No se pudo cerrar: {esc(info)}",
                          self._nav([button("💼 Posiciones", "p")]))
        threading.Thread(target=worker, daemon=True).start()

    def _run_diagnosis(self, msg_id: int | None, symbols: list | None = None):
        bot = self._require_bot()
        if not Config.is_exness():
            text, _ = self._screen_risk()
            self.send("🩺 El diagnóstico detallado es para Exness. En Binance el bot opera cuando "
                      "la estrategia da señal y el riesgo lo permite:\n\n" + text, self._nav())
            return
        if msg_id:
            self.edit(msg_id, "🩺 Analizando tus mercados… (unos segundos)")
        else:
            msg_id = self.send("🩺 Analizando tus mercados… (unos segundos)")
        self._typing()

        def worker():
            rows = bot.diagnose_exness(symbols)
            ok = [r for r in rows if r.get("allowed")]
            lines = [f"<b>🩺 Diagnóstico</b>  ·  estrategia {esc(Config.STRATEGY)} ({esc(Config.TIMEFRAME)})", ""]
            for r in rows:
                if r.get("allowed"):
                    sig = r.get("signal", "-")
                    sig_txt = {"buy": "🔼 señal de compra", "sell": "🔽 señal de venta"}.get(sig, "sin señal ahora")
                    lines.append(f"✅ <b>{esc(r['symbol'])}</b> — {r['lots']} lotes, "
                                 f"riesgo {r['risk_usd']} USD, {r['leverage']}x · {sig_txt}")
                else:
                    lines.append(f"❌ <b>{esc(r['symbol'])}</b> — {esc(r.get('reason', ''))}")
            buttons = []
            if not ok:
                lines += ["", "💡 <b>Tu saldo no alcanza el lote mínimo</b> con el riesgo configurado en "
                              "estos mercados. Prueba pares de lote pequeño, p. ej. "
                              "<code>/diagnostico EURUSDc GBPUSDc</code>."]
            active = self._markets()
            for r in ok:
                if r["symbol"] not in active:
                    buttons.append([button(f"➕ Añadir {r['symbol']}", f"da:{r['symbol']}")])
            self.edit(msg_id, "\n".join(lines) + self._stamp(), self._nav(*buttons, refresh="d"))
        threading.Thread(target=worker, daemon=True).start()

    def _login(self):
        bot = self._require_bot()
        if not getattr(bot, "is_exness", False):
            self.send("ℹ️ Esto solo aplica con <code>BROKER=exness</code>.")
            return
        if getattr(bot, "exness_ready", False):
            self.send("✅ Ya estás conectado a Exness.", [[button("📊 Estado", "s")]])
            return
        msg_id = self.send(f"🔑 <b>Conectando a Exness…</b>\nCuenta <code>{esc(Config.EXNESS_LOGIN)}</code> · "
                           f"{esc(Config.EXNESS_SERVER)}\n\nPuede tardar hasta 3 minutos. Te aviso al terminar.")

        def worker():
            try:
                ok, msg = bot.exness.connect()
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            if not ok:
                self.edit(msg_id, f"❌ <b>No se pudo conectar</b>\n<code>{esc(msg)}</code>\n\n"
                                  "Si dice <i>IPC timeout</i>, entra una vez en la terminal MT5 "
                                  "por VNC y vuelve a intentarlo.",
                          [[button("🔁 Reintentar", "login")]])
                return
            bot.exness_ready = True
            s = bot.exness.get_balance_summary()
            bot.risk_manager.initial_balance = s.get("balance", 0) or 0
            bot.risk_manager.current_balance = s.get("balance", 0) or 0
            self.edit(msg_id, "✅ Conectado a Exness.")
            self.notify_connected(s)
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    #  NOTIFICACIONES (las llama el bot de trading)
    # ------------------------------------------------------------------
    def notify_startup(self):
        bot = self.bot_instance
        cur = self._currency()
        bal = bot.risk_manager.initial_balance if bot else 0
        markets = ", ".join(self._markets())
        self.send(
            f"🚀 <b>Bot en marcha</b>  ·  {self._mode_badge()}\n\n"
            f"Balance: <b>{esc(fmt_money(bal, cur))}</b>\n"
            f"Estrategia: <b>{esc(Config.STRATEGY)}</b> ({esc(Config.TIMEFRAME)})\n"
            f"Mercados: {esc(markets)}",
            [[button("📋 Abrir menú", "m")]], silent=True, reply_keyboard=None)

    def notify_connected(self, s: dict):
        self.send(
            "✅ <b>Conectado a Exness</b>\n\n"
            f"Cuenta: <code>{esc(s.get('login', ''))}</code>\n"
            f"Balance: <b>{esc(fmt_money(s.get('balance', 0), s.get('currency', 'USD')))}</b>\n"
            f"Apalancamiento del broker: 1:{esc(s.get('broker_leverage', '?'))} "
            f"(el bot se limita a {Config.MAX_LEVERAGE:g}x)\n"
            f"Modo: <b>{self._mode_badge()}</b>",
            [[button("🩺 ¿Qué puedo operar?", "d"), button("📋 Menú", "m")]])

    def notify_control(self, action: str, detail: str):
        if self._quiet_control:
            return
        icons = {"PAUSADO": "⏸", "REANUDADO": "▶️", "DETENIDO": "⏹", "STAKE": "💵",
                 "MAX TRADES": "💼", "PARES": "🌐", "MERCADOS": "🌐"}
        self.send(f"{icons.get(action, '⚙️')} <b>{esc(action.capitalize())}</b>\n{esc(detail)}", silent=True)

    def notify_trade_open(self, symbol: str, price: float, amount: float, stake: float,
                          stop: float | None = None):
        if not self.prefs.get("trades", True):
            return
        cur = Config.STAKE_CURRENCY
        lines = [f"🟢 <b>COMPRA {esc(symbol)}</b>", "",
                 f"Precio: <b>{esc(fmt_price(price))}</b>",
                 f"Cantidad: {esc(fmt_price(amount))}",
                 f"Invertido: <b>{esc(fmt_money(stake, cur))}</b>"]
        if stop:
            risk = amount * (price - stop)
            lines.append(f"🛑 Stop: {esc(fmt_price(stop))} (arriesga {esc(fmt_money(risk, cur))})")
        self.send("\n".join(lines), [[button("💼 Ver posiciones", "p")]])

    def notify_exness_open(self, symbol: str, side: str, plan: dict, sl: float, tp: float):
        if not self.prefs.get("trades", True):
            return
        head = "🔼 <b>COMPRA" if side == "buy" else "🔽 <b>VENTA"
        lines = [f"{head} {esc(symbol)}</b>", "",
                 f"Lotes: <b>{esc(plan.get('lots'))}</b>  ·  apalancamiento {esc(plan.get('effective_leverage'))}x",
                 f"Arriesga: <b>{esc(plan.get('risk_usd'))} USD</b> si toca el stop",
                 f"🛑 Stop: {esc(fmt_price(sl))}"]
        lines.append(f"🎯 Objetivo: {esc(fmt_price(tp))}" if tp else "🎯 Sin objetivo fijo: el stop sube con el precio")
        self.send("\n".join(lines), [[button("💼 Ver posiciones", "p")]])

    def notify_trade_close(self, symbol: str, price: float, profit: float, profit_pct: float,
                           reason: str, entry_time=None):
        if not self.prefs.get("trades", True):
            return
        cur = self._currency()
        head = "✅ <b>GANANCIA" if profit >= 0 else "🔴 <b>PÉRDIDA"
        lines = [f"{head} {esc(symbol)}</b>", "",
                 f"Resultado: <b>{esc(fmt_signed(profit, cur))}</b> ({esc(fmt_signed(profit_pct, pct=True))})",
                 f"Precio de salida: {esc(fmt_price(price))}",
                 f"Motivo: {esc(human_reason(reason))}"]
        if entry_time:
            lines.append(f"Duración: {esc(human_duration(entry_time))}")
        self.send("\n".join(lines), [[button("💰 Ganancias", "g"), button("📜 Historial", "h:0")]])

    def notify_rejection(self, symbol: str, side: str, reason: str):
        if not self.prefs.get("rejections", True):
            return
        self.send(f"🚫 <b>Señal de {'compra' if side == 'buy' else 'venta'} en {esc(symbol)} rechazada</b>\n\n"
                  f"{esc(reason)}\n\n<i>El bot prefiere no operar antes que arriesgar de más. "
                  "Solo te aviso una vez cada 6 h por mercado.</i>",
                  [[button("🩺 ¿Qué puedo operar?", "d")], [button("🔕 No avisar de rechazos", "n:rejections")]],
                  silent=True)

    def notify_stats(self, stats: dict):
        """El bot lo llama cada hora; se envia segun la preferencia (diario por defecto)."""
        mode = self.prefs.get("summary", "daily")
        now = datetime.now()
        if mode == "off":
            return
        if mode == "daily":
            if self._last_summary is None:
                self._last_summary = now  # primer resumen a las 24 h de arrancar
                return
            if now - self._last_summary < timedelta(hours=24):
                return
        self._last_summary = now
        cur = self._currency()
        period = "del día" if mode == "daily" else "de la hora"
        dd_max = self.bot_instance.risk_manager.max_drawdown_pct if self.bot_instance else 10
        self.send(
            f"📰 <b>Resumen {period}</b>\n\n"
            f"Balance: <b>{esc(fmt_money(stats['balance'], cur))}</b>  "
            f"{pl_icon(stats['profit_total'])} {esc(fmt_signed(stats['profit_pct'], pct=True))}\n"
            f"Operaciones: {stats['total_trades']} ({stats['winning_trades']}✅ / {stats['losing_trades']}❌)"
            f" · acierto {stats['win_rate']:.0f}%\n"
            f"Abiertas: {stats['open_trades']}\n"
            f"Drawdown: {bar(stats['drawdown_pct'], dd_max)} {stats['drawdown_pct']:.1f}%",
            [[button("📊 Estado", "s"), button("💰 Ganancias", "g")]], silent=True)

    def notify_risk_alert(self, message: str):
        # Las alertas de riesgo SIEMPRE suenan
        self.send(f"🚨 <b>ALERTA DE RIESGO</b>\n\n{esc(message)}",
                  [[button("🛡 Riesgo", "r"), button("💼 Posiciones", "p")]], silent=False)

    def notify_shutdown(self, stats: dict):
        cur = self._currency()
        self.send(f"⏹ <b>Bot detenido</b>\n\nBalance final: <b>{esc(fmt_money(stats['balance'], cur))}</b> "
                  f"({esc(fmt_signed(stats['profit_pct'], pct=True))})\n"
                  f"Operaciones: {stats['total_trades']} · acierto {stats['win_rate']:.0f}%")
