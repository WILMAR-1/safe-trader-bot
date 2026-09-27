"""Pruebas de la experiencia en Telegram, sin red: simula la API de Telegram y los brokers.
Recorre todas las pantallas y botones con el bot real (Binance y Exness)."""
import logging
import os
import sys
import tempfile
import types
from html.parser import HTMLParser
from types import SimpleNamespace as NS

tmp = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(tmp)  # el bot guarda data/runtime_config.json: que no toque el del proyecto
os.makedirs("data", exist_ok=True)
os.environ.update(TELEGRAM_ENABLED="false", TIMEFRAME="1h", PAIR_WHITELIST="BTC/USDT,ETH/USDT",
                  DB_PATH=os.path.join(tmp, "t.db"), STRATEGY="trend")
sys.modules.setdefault("ccxt", types.ModuleType("ccxt"))
logging.basicConfig(level=logging.CRITICAL)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import src.telegram_bot as tg  # noqa: E402

tg.PREFS_PATH = os.path.join(tmp, "prefs.json")

# ---------------------------------------------------------------------
#  API de Telegram simulada
# ---------------------------------------------------------------------
SENT = []          # (method, payload)
FAIL_PARSE = {"on": False}


class Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def fake_post(url, json=None, timeout=None):
    method = url.rsplit("/", 1)[-1]
    SENT.append((method, json))
    if FAIL_PARSE["on"] and json.get("parse_mode"):
        return Resp({"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"})
    return Resp({"ok": True, "result": {"message_id": len(SENT)}})


tg.requests.post = fake_post

ALLOWED_TAGS = {"b", "i", "code"}


class TagCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.errors.append(f"etiqueta no permitida <{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(f"cierre desbalanceado </{tag}>")


def check_payloads(start=0):
    """Todo lo enviado es HTML valido para Telegram, cabe en 4096 y los botones son validos."""
    for method, p in SENT[start:]:
        if method not in ("sendMessage", "editMessageText"):
            continue
        text = p["text"]
        assert len(text) <= 4096, f"mensaje de {len(text)} caracteres"
        if p.get("parse_mode") == "HTML":
            c = TagCheck()
            c.feed(text)
            assert not c.errors and not c.stack, (c.errors, c.stack, text[:200])
        for row in (p.get("reply_markup") or {}).get("inline_keyboard", []):
            assert 1 <= len(row) <= 4, row
            for b in row:
                assert len(b["callback_data"].encode()) <= 64


def buttons_of(p):
    return [b["callback_data"] for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]


def last_msg():
    return next(p for m, p in reversed(SENT) if m in ("sendMessage", "editMessageText"))


def last_text():
    return next(p["text"] for m, p in reversed(SENT) if m in ("sendMessage", "editMessageText"))


def msg(chat, text):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}


def press(chat, data, mid=1):
    return {"update_id": 1, "callback_query": {"id": "c", "data": data,
                                               "message": {"message_id": mid, "chat": {"id": chat}}}}


def crawl(cmd, start_data="m"):
    """Pulsar todos los botones alcanzables (salvo los destructivos) y validar cada pantalla."""
    seen, queue = set(), [start_data]
    skip_prefixes = ("stop!", "cl!:", "rm:", "da:", "pause", "resume", "st:", "mx:", "n:", "login")
    while queue:
        data = queue.pop()
        if data in seen:
            continue
        seen.add(data)
        before = len(SENT)
        cmd._handle_update(press(CHAT, data))
        cmd._thread_join()
        check_payloads(before)
        for _, p in SENT[before:]:
            for b in buttons_of(p) if p else []:
                if not b.startswith(skip_prefixes):
                    queue.append(b)
    return seen


# Ejecutar los hilos de trabajo en el acto para que las pruebas sean deterministas
class InlineThread:
    def __init__(self, target, daemon=None):
        self.t = target

    def start(self):
        self.t()


tg.threading.Thread = InlineThread
tg.TelegramCommander._thread_join = lambda self: None
CHAT = "42"

# ---------------------------------------------------------------------
#  Datos de mercado sinteticos
# ---------------------------------------------------------------------
rng = np.random.default_rng(1)
N = 600
closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, N)))
TS = (1767225600 + np.arange(N) * 3600) * 1000
ROWS = np.c_[TS, closes, closes * 1.005, closes * 0.995, closes, np.ones(N)]

print("=" * 70); print("  PRUEBAS DE TELEGRAM"); print("=" * 70)

# =====================================================================
#  1. BINANCE (spot)
# =====================================================================
import src.exchange as exmod  # noqa: E402


class FakeBinance:
    def __init__(self):
        pass

    def fetch_ohlcv(self, sym, tf, limit=200):
        return ROWS[-limit:].tolist()

    def fetch_ticker(self, sym):
        return {"last": float(closes[-1]), "percentage": 1.5}

    def get_balance(self):
        return {"free": {"USDT": 1000}}

    def get_min_amount(self, sym):
        return 0.0

    def create_buy_order(self, sym, amt, price=None):
        return {"price": float(closes[-1]), "amount": amt, "id": "b"}

    def create_sell_order(self, sym, amt, price=None):
        return {"price": float(closes[-1]) * 1.02, "amount": amt, "id": "s"}


exmod.Exchange = FakeBinance
import src.bot as botmod  # noqa: E402

botmod.Exchange = FakeBinance
bot = botmod.TradingBot()
cmd = tg.TelegramCommander("TOKEN", CHAT)
cmd.set_bot(bot)
bot.notifier = cmd

# Una operacion abierta y otra cerrada con motivo que lleva "_" (rompia Markdown)
bot.db.open_trade("BTC/USDT", float(closes[-1]) * 0.97, 0.5, 50, "trend_strategy")
tid = bot.db.open_trade("ETH/USDT", 50.0, 1.0, 50, "trend_strategy")
bot.db.close_trade(tid, 55.0, "TRAILING_ATR (+10.0%)")

# --- 1. /start: bienvenida con teclado fijo + menu ---
SENT.clear()
cmd._handle_update(msg(CHAT, "/start"))
assert SENT[0][1]["reply_markup"]["keyboard"], "falta el teclado persistente"
assert "pause" in buttons_of(SENT[1][1]) and "stop?" in buttons_of(SENT[1][1])
check_payloads()
print("1) [OK] /start muestra bienvenida, teclado fijo y menu con botones")

# --- 2. Todas las pantallas alcanzables son validas ---
screens = crawl(cmd)
for must in ("s", "p", "h:0", "g", "r", "cfg", "mk", "n", "hlp", "stop?"):
    assert must in screens, f"pantalla {must} no alcanzable"
print(f"2) [OK] {len(screens)} pantallas recorridas: HTML valido, <=4096, botones validos")

# --- 3. Chat no autorizado: silencio total ---
SENT.clear()
cmd._handle_update(msg("999", "/estado"))
cmd._handle_update(press("999", "stop!"))
assert not SENT and bot.running
print("3) [OK] ignora mensajes y botones de otros chats")

# --- 4. Pausar/reanudar desde botones, sin mensaje duplicado de control ---
SENT.clear()
cmd._handle_update(press(CHAT, "pause"))
assert bot.risk_manager.is_paused
assert not any(m == "sendMessage" for m, _ in SENT), "aviso duplicado al pausar desde Telegram"
cmd._handle_update(press(CHAT, "resume"))
assert not bot.risk_manager.is_paused
print("4) [OK] pausar/reanudar con botones, editando el mismo mensaje")

# --- 5. Ajustes con botones y con texto ---
stake0 = botmod.Config.STAKE_AMOUNT
cmd._handle_update(press(CHAT, "st:10"))
assert botmod.Config.STAKE_AMOUNT == stake0 + 10
cmd._handle_update(press(CHAT, "st?"))
cmd._handle_update(msg(CHAT, "25,5"))
assert botmod.Config.STAKE_AMOUNT == 25.5
cmd._handle_update(msg(CHAT, "/setstake 3"))       # por debajo del minimo
assert botmod.Config.STAKE_AMOUNT == 25.5 and "no válido" in last_text()
cmd._handle_update(press(CHAT, "mx:1"))
assert botmod.Config.MAX_OPEN_TRADES == 4
print("5) [OK] inversión y máximo de posiciones con +/- y escribiendo el valor")

# --- 6. Mercados: anadir por texto, quitar por boton, no dejar la lista vacia ---
cmd._handle_update(press(CHAT, "add"))
cmd._handle_update(msg(CHAT, "sol"))
assert "SOL/USDT" in botmod.Config.PAIR_WHITELIST
cmd._handle_update(press(CHAT, "rm:SOL/USDT"))
assert "SOL/USDT" not in botmod.Config.PAIR_WHITELIST
print("6) [OK] añadir/quitar mercados")

# --- 7. Cerrar posicion: pide confirmacion y luego cierra de verdad ---
open_id = bot.list_open_positions()[0]["id"]
SENT.clear()
cmd._handle_update(press(CHAT, f"cl?:{open_id}"))
assert f"cl!:{open_id}" in buttons_of(last_msg()), "sin confirmación antes de cerrar"
assert bot.db.get_open_trades(), "cerró sin confirmar"
cmd._handle_update(press(CHAT, f"cl!:{open_id}"))
assert not bot.db.get_open_trades(), "no cerró la posición"
assert any("GANANCIA" in p.get("text", "") for m, p in SENT if m == "sendMessage")
check_payloads()
print("7) [OK] cerrar posición con confirmación y aviso del resultado")

# --- 8. Avisos: preferencias persistentes y respetadas ---
cmd._handle_update(press(CHAT, "n:trades"))
SENT.clear()
cmd.notify_trade_open("BTC/USDT", 100, 1, 100)
assert not SENT, "envió aviso de operación con el aviso desactivado"
cmd._handle_update(press(CHAT, "n:trades"))
assert tg.TelegramCommander("T", CHAT).prefs["trades"] is True
cmd.prefs["summary"] = "daily"
SENT.clear()
st = bot.risk_manager.get_stats()
cmd.notify_stats(st); cmd.notify_stats(st)
assert not SENT, "el resumen diario no debe salir cada hora"
cmd.notify_risk_alert("margen bajo <50%>")
assert last_msg()["disable_notification"] is False and "&lt;50%&gt;" in last_msg()["text"]
print("8) [OK] preferencias de avisos; resumen diario; alertas siempre con sonido y escapadas")

# --- 9. Si Telegram rechaza el formato, se reenvia en texto plano ---
FAIL_PARSE["on"] = True
SENT.clear()
cmd.send("<b>hola</b>")
FAIL_PARSE["on"] = False
assert last_msg().get("parse_mode") is None and last_msg()["text"] == "hola"
print("9) [OK] un fallo de formato ya no pierde el mensaje")

# --- 10. Detener exige confirmacion ---
SENT.clear()
cmd._handle_update(msg(CHAT, "/stop"))
assert bot.running and "stop!" in buttons_of(last_msg())
cmd._handle_update(press(CHAT, "stop!"))
assert not bot.running
print("10) [OK] detener pide confirmación")

# =====================================================================
#  2. EXNESS (cuenta cent pequena)
# =====================================================================
from src.leverage_risk import LeverageRiskManager  # noqa: E402
import src.exness as exn  # noqa: E402

SPEC = {"BTCUSD": (60000, 1, 0.006), "EURUSDc": (1.08, 1000, 0.0012), "XAUUSDc": (4000, 1, 0.002)}


class FakeMT5:
    def account_info(self):
        return NS(balance=1575, equity=1575, margin=0, leverage=2000, currency="USC",
                  margin_free=1575, profit=0, server="Exness-MT5Trial", login=123)

    def symbol_info(self, sym):
        if sym not in SPEC:
            return None
        return NS(trade_contract_size=SPEC[sym][1], volume_min=0.01, volume_max=100, volume_step=0.01)

    def symbols_get(self):
        return [NS(name=n) for n in list(SPEC) + ["GBPUSDc"]]

    def symbol_info_tick(self, sym):
        p = SPEC[sym][0]
        return NS(ask=p, bid=p, last=p, time=0)

    def positions_get(self, ticket=None):
        return [NS(ticket=777, symbol="EURUSDc", type=0, volume=0.03, price_open=1.07,
                   price_current=1.08, sl=1.06, tp=0.0, profit=30.0, time=1767225600,
                   magic=exn.BOT_MAGIC)]


class FakeExness(exn.ExnessExchange):
    def __init__(self, dry_run=True):
        self.dry_run = True
        self.connected = True
        self.mt5 = FakeMT5()
        self.cent_factor = 100.0
        self.is_cent = True
        self.risk = LeverageRiskManager(3, 1.0, 50, 3)

    def connect(self):
        return True, "ok"

    def fetch_ohlcv(self, sym, tf, limit=250):
        p, _, v = SPEC.get(sym, (1.0, 1, 0.001))
        c = p * np.exp(np.cumsum(np.random.default_rng(0).normal(0, v, limit)))
        c = c / c[-1] * p
        o = np.r_[c[0], c[:-1]]
        return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=limit, freq="1h"),
                             "open": o, "high": np.maximum(o, c) * (1 + v / 2),
                             "low": np.minimum(o, c) * (1 - v / 2), "close": c, "volume": 1.0})


exn.ExnessExchange = FakeExness
botmod.Config.BROKER = "exness"
os.remove("data/runtime_config.json")  # que no herede la lista de la parte Binance
botmod.Config.EXNESS_SYMBOLS = ["BTCUSD", "XRPUSD"]  # XRPUSD no existe en esta cuenta cent
bot2 = botmod.TradingBot()
bot2.exness_ready = True
cmd2 = tg.TelegramCommander("TOKEN", CHAT)
cmd2.set_bot(bot2)
bot2.notifier = cmd2

# --- 11. Pantallas Exness ---
SENT.clear()
screens = crawl(cmd2)
assert "d" in screens
assert any("EURUSDc" in p.get("text", "") for m, p in SENT if m in ("sendMessage", "editMessageText"))
print(f"11) [OK] Exness: {len(screens)} pantallas válidas, posiciones de MT5 visibles")

# --- 12. Diagnostico ofrece anadir lo que SI se puede operar ---
SENT.clear()
cmd2._handle_update(msg(CHAT, "/diagnostico BTCUSD EURUSDc"))
diag = last_text()
assert "❌ <b>BTCUSD</b>" in diag and "✅ <b>EURUSDc</b>" in diag, diag
assert "da:EURUSDc" in buttons_of(last_msg())
cmd2._handle_update(press(CHAT, "da:EURUSDc"))
assert "EURUSDc" in botmod.Config.EXNESS_SYMBOLS, "no añadió a la lista de EXNESS"
print("12) [OK] diagnóstico explica y añade con un botón el mercado operable")

# --- 13. /addpair en Exness usa el nombre real del broker (antes tocaba la lista de Binance) ---
cmd2._handle_update(msg(CHAT, "/addpair gbpusdc"))
assert "GBPUSDc" in botmod.Config.EXNESS_SYMBOLS, botmod.Config.EXNESS_SYMBOLS
cmd2._handle_update(msg(CHAT, "/addpair EURUSDX"))
assert "¿Quisiste decir" in last_text(), last_text()
print("13) [OK] /addpair en Exness: corrige mayúsculas y sugiere nombres parecidos")

check_payloads()
print("\n" + "=" * 70)
print("  TODAS LAS PRUEBAS DE TELEGRAM PASARON")
print("=" * 70)
