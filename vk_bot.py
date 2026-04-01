"""
═══════════════════════════════════════════════════════════
  Citizen Monitor — VK Bot Integration
═══════════════════════════════════════════════════════════

  Бот для ВКонтакте, интегрированный с Citizen Monitor.
  Отправляет уведомления о происшествиях в беседу/группу VK,
  принимает команды от пользователей.

  ── Настройка ──
  1. Создайте сообщество VK (или используйте существующее)
  2. Настройки → Работа с API → Создать ключ
     Права: сообщения сообщества, управление
  3. Настройки → Работа с API → Long Poll API → Включить
     Версия API: 5.199
     Типы событий: Входящие сообщения
  4. Настройки → Сообщения → Включить сообщения сообщества
  5. Заполните .env файл (см. ниже)

  ── .env ──
  VK_GROUP_TOKEN=vk1.a.xxxxxxx...
  VK_GROUP_ID=123456789
  VK_NOTIFY_PEER_ID=2000000001
  VK_ADMIN_IDS=123456,789012
  CITIZEN_API_URL=http://localhost:3000/api

  ── Запуск ──
  pip install vk-api requests python-dotenv
  python vk_bot.py

  ── Команды ──
  /help           — список команд
  /live           — активные происшествия
  /stats          — статистика за 7 дней
  /top            — лидерборд
  /map            — ссылка на карту
  /subscribe      — подписка на уведомления
  /unsubscribe    — отписка
  /safety         — безопасность района
  (цифра)         — подробности о событии
═══════════════════════════════════════════════════════════
"""

import os
import sys
import json
import time
import threading
import logging
from datetime import datetime

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import requests
from dotenv import load_dotenv

load_dotenv()

VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN", "")
VK_GROUP_ID = int(os.getenv("VK_GROUP_ID", "0"))
VK_NOTIFY_PEER_ID = os.getenv("VK_NOTIFY_PEER_ID", "")
VK_ADMIN_IDS = [int(x) for x in os.getenv("VK_ADMIN_IDS", "").split(",") if x.strip()]
CITIZEN_API_URL = os.getenv("CITIZEN_API_URL", "http://localhost:3000/api")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("citizen-vk")

TYPES = {
    "fire":       {"emoji": "🔥", "label": "ПОЖАР"},
    "accident":   {"emoji": "🚗", "label": "ДТП"},
    "suspicious": {"emoji": "👁",  "label": "ПОДОЗРИТЕЛЬНОЕ"},
    "crime":      {"emoji": "🚨", "label": "КРИМИНАЛ"},
    "medical":    {"emoji": "🏥", "label": "МЕД. ПОМОЩЬ"},
    "sos":        {"emoji": "🆘", "label": "SOS"},
    "other":      {"emoji": "📌", "label": "ДРУГОЕ"},
}

# ── Подписчики ──
SUBS_FILE = "vk_subscribers.json"

def load_subs():
    if os.path.exists(SUBS_FILE):
        with open(SUBS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_subs(s):
    with open(SUBS_FILE, "w") as f:
        json.dump(list(s), f)

subscribers = load_subs()


# ═══════════════════════════════════════
#  VK INIT
# ═══════════════════════════════════════

def init_vk():
    if not VK_GROUP_TOKEN:
        log.error("❌ VK_GROUP_TOKEN не задан")
        sys.exit(1)
    session = vk_api.VkApi(token=VK_GROUP_TOKEN)
    api = session.get_api()
    try:
        info = api.groups.getById(group_id=VK_GROUP_ID)
        log.info(f"✅ Группа: {info[0]['name']}")
    except Exception as e:
        log.error(f"❌ VK: {e}")
        sys.exit(1)
    return session, api


# ═══════════════════════════════════════
#  CITIZEN API
# ═══════════════════════════════════════

def api_get(endpoint, params=None):
    try:
        r = requests.get(f"{CITIZEN_API_URL}/{endpoint}", params=params, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"API [{endpoint}]: {e}")
        return None

def get_incidents(t=None):
    p = {"hours": 24}
    if t: p["type"] = t
    d = api_get("incidents", p)
    return d.get("incidents", []) if d else []

def get_analytics():
    return api_get("analytics/overview")

def get_leaderboard():
    d = api_get("analytics/leaderboard")
    return d.get("leaderboard", []) if d else []

def get_safety(lat, lng):
    return api_get("analytics/safety", {"lat": lat, "lng": lng})


# ═══════════════════════════════════════
#  FORMATTING
# ═══════════════════════════════════════

def time_ago(ts):
    if not ts: return "?"
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(ts)
        diff = (datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds()
        if diff < 60: return "сейчас"
        if diff < 3600: return f"{int(diff//60)} мин"
        if diff < 86400: return f"{int(diff//3600)} ч"
        return f"{int(diff//86400)} дн"
    except: return "?"

def fmt_incident(inc):
    t = TYPES.get(inc.get("type", "other"), TYPES["other"])
    maps = f"https://maps.google.com/?q={inc['lat']},{inc['lng']}"
    lines = [
        f"⚡ {t['emoji']} {t['label']}",
        "",
        f"📝 {inc.get('description', '—')}",
        f"📍 {inc.get('address') or f'{inc[\"lat\"]:.5f}, {inc[\"lng\"]:.5f}'}",
        "",
        f"👤 {inc.get('username', 'Аноним')} · ⭐{inc.get('reputation', 0)}",
        f"✅ {inc.get('confirms', 0)} · ❌ {inc.get('fakes', 0)} · 💬 {inc.get('comment_count', 0)}",
        f"🕐 {time_ago(inc.get('created_at'))}",
        "",
        f"🗺 {maps}",
    ]
    if inc.get("active_streamers", 0) > 0:
        lines.append("📡 Стример на месте")
    st = inc.get("status")
    if st == "confirmed": lines.append("✅ Подтверждено")
    elif st == "resolved": lines.append("☑️ Закрыто")
    return "\n".join(lines)

def fmt_short(inc, i):
    t = TYPES.get(inc.get("type", "other"), TYPES["other"])
    desc = inc.get("description", "")[:55]
    if len(inc.get("description", "")) > 55: desc += "…"
    st = {"confirmed": "✅", "resolved": "☑️", "responding": "📡"}.get(inc.get("status"), "")
    return f"{i}. {t['emoji']} {desc} {st}\n   📍 {inc.get('address') or '—'} · {time_ago(inc.get('created_at'))}"

def fmt_sos(inc):
    maps = f"https://maps.google.com/?q={inc['lat']},{inc['lng']}"
    return "\n".join([
        "🚨🚨🚨 SOS ТРЕВОГА 🚨🚨🚨", "",
        f"📝 {inc.get('description', 'Нужна помощь!')}",
        f"📍 {inc['lat']:.5f}, {inc['lng']:.5f}",
        f"👤 {inc.get('username', 'Аноним')}",
        f"🕐 {datetime.now().strftime('%H:%M:%S')}", "",
        f"🗺 {maps}",
    ])

def fmt_stats(d):
    if not d: return "❌ Ошибка загрузки"
    tot = d.get("totals", {})
    lines = [
        "📊 СТАТИСТИКА (7 ДНЕЙ)", "",
        f"📋 Всего: {tot.get('total', 0)}",
        f"✅ Подтверждено: {tot.get('confirmed', 0)}",
        f"☑️ Решено: {tot.get('resolved', 0)}",
        f"📡 Стримеров: {tot.get('active_streamers', 0)}",
    ]
    if tot.get("avg_response_min"):
        lines.append(f"⏱ Отклик: {tot['avg_response_min']} мин")
    for bt in d.get("byType", []):
        t = TYPES.get(bt["type"], TYPES["other"])
        lines.append(f"  {t['emoji']} {t['label']}: {bt['count']}")
    return "\n".join(lines)

def fmt_top(users):
    if not users: return "❌ Ошибка"
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 ЛИДЕРБОРД", ""]
    for i, u in enumerate(users[:10]):
        r = medals[i] if i < 3 else f"{i+1}."
        s = " 📡" if u.get("is_streamer") else ""
        lines.append(f"{r} {u['username']}{s} — ⭐{u['reputation']} ({u.get('reports',0)} реп.)")
    return "\n".join(lines)

def fmt_safety(d):
    if not d: return "❌ Ошибка"
    lvl = {"safe": "🟢 Безопасно", "moderate": "🟡 Средне", "dangerous": "🔴 Опасно"}.get(d.get("level"), "⚪ ?")
    lines = ["🛡 БЕЗОПАСНОСТЬ РАЙОНА", "", f"Оценка: {d.get('score', 0)}/100 {lvl}", f"За 30 дней: {d.get('total_incidents', 0)} событий"]
    for b in d.get("breakdown", []):
        t = TYPES.get(b["type"], TYPES["other"])
        lines.append(f"  {t['emoji']} {t['label']}: {b['count']}")
    return "\n".join(lines)


# ═══════════════════════════════════════
#  KEYBOARD
# ═══════════════════════════════════════

def main_kb():
    kb = VkKeyboard(one_time=False)
    kb.add_button("🔴 Происшествия", color=VkKeyboardColor.NEGATIVE)
    kb.add_button("📊 Статистика", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🏆 Топ", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🛡 Безопасность", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🗺 Карта", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🔔 Подписка", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()

def filter_kb():
    kb = VkKeyboard(inline=True)
    kb.add_button("🔥 Пожары", color=VkKeyboardColor.NEGATIVE, payload=json.dumps({"f": "fire"}))
    kb.add_button("🚗 ДТП", color=VkKeyboardColor.PRIMARY, payload=json.dumps({"f": "accident"}))
    kb.add_line()
    kb.add_button("🚨 Крим.", color=VkKeyboardColor.NEGATIVE, payload=json.dumps({"f": "crime"}))
    kb.add_button("🏥 Мед.", color=VkKeyboardColor.PRIMARY, payload=json.dumps({"f": "medical"}))
    kb.add_line()
    kb.add_button("📋 Все", color=VkKeyboardColor.SECONDARY, payload=json.dumps({"f": "all"}))
    return kb.get_keyboard()


# ═══════════════════════════════════════
#  SEND
# ═══════════════════════════════════════

def send(api, peer, text, kb=None):
    p = {"peer_id": peer, "message": text, "random_id": int(time.time()*1000) % (2**31), "dont_parse_links": 1}
    if kb: p["keyboard"] = kb
    try: api.messages.send(**p)
    except Exception as e: log.error(f"Send error: {e}")


# ═══════════════════════════════════════
#  HANDLER
# ═══════════════════════════════════════

def handle(api, event):
    txt = event.text.strip().lower()
    pid = event.peer_id
    uid = event.user_id

    # Payload (inline buttons)
    try:
        pl = json.loads(event.payload) if hasattr(event, "payload") and event.payload else None
    except: pl = None

    if pl and "f" in pl:
        ft = pl["f"]
        incs = get_incidents(ft if ft != "all" else None)
        if not incs: send(api, pid, "Нет событий"); return
        lines = [f"📋 {TYPES.get(ft, {}).get('label', 'ВСЕ')} ({len(incs)})\n"]
        for i, inc in enumerate(incs[:10], 1):
            lines.append(fmt_short(inc, i)); lines.append("")
        send(api, pid, "\n".join(lines))
        return

    # Admin
    if uid in VK_ADMIN_IDS:
        if txt == "/admin_stats":
            send(api, pid, f"🔧 Подписчиков: {len(subscribers)}\nAPI: {CITIZEN_API_URL}"); return
        if txt == "/admin_subs":
            send(api, pid, f"📋 ({len(subscribers)}): {', '.join(str(s) for s in subscribers) or '—'}"); return
        if txt.startswith("/admin_broadcast "):
            msg = txt[17:].strip()
            if msg:
                c = sum(1 for s in subscribers if not send(api, s, f"📢 {msg}"))
                send(api, pid, f"✅ Отправлено {len(subscribers)}")
            return

    # Commands
    if txt in ("/start", "/help", "начать", "помощь", "❓ помощь"):
        send(api, pid, "\n".join([
            "⚡ CITIZEN MONITOR", "",
            "🔴 Происшествия — события за 24ч",
            "📊 Статистика — за 7 дней",
            "🏆 Топ — лидерборд",
            "🛡 Безопасность — рейтинг района",
            "🗺 Карта — веб-приложение",
            "🔔 Подписка — уведомления",
            "", "Введите номер события для подробностей"
        ]), main_kb())

    elif txt in ("/live", "происшествия", "🔴 происшествия"):
        incs = get_incidents()
        if not incs: send(api, pid, "✅ Нет активных событий"); return
        lines = [f"🔴 АКТИВНЫЕ ({len(incs)})\n"]
        for i, inc in enumerate(incs[:15], 1):
            lines.append(fmt_short(inc, i)); lines.append("")
        if len(incs) > 15: lines.append(f"…ещё {len(incs)-15}")
        lines.append("\n💡 Введите номер для подробностей")
        send(api, pid, "\n".join(lines), filter_kb())

    elif txt in ("/stats", "статистика", "📊 статистика"):
        send(api, pid, fmt_stats(get_analytics()))

    elif txt in ("/top", "топ", "лидерборд", "🏆 топ"):
        send(api, pid, fmt_top(get_leaderboard()))

    elif txt in ("/safety", "безопасность", "🛡 безопасность"):
        send(api, pid, fmt_safety(get_safety(55.7558, 37.6173)) + f"\n\n🗺 Подробнее: {CITIZEN_API_URL.replace('/api','')}")

    elif txt in ("/map", "карта", "🗺 карта"):
        send(api, pid, f"🗺 Карта: {CITIZEN_API_URL.replace('/api','')}")

    elif txt in ("/subscribe", "подписка", "🔔 подписка"):
        subscribers.add(pid); save_subs(subscribers)
        send(api, pid, "🔔 Подписка оформлена!\nНовые события будут приходить сюда.\n/unsubscribe для отписки")

    elif txt in ("/unsubscribe", "отписка", "отписаться"):
        subscribers.discard(pid); save_subs(subscribers)
        send(api, pid, "🔕 Отписка выполнена")

    elif txt in ("/sos", "sos"):
        send(api, pid, f"🆘 SOS доступен в веб-приложении:\n{CITIZEN_API_URL.replace('/api','')}")

    elif txt.isdigit():
        incs = get_incidents()
        idx = int(txt) - 1
        if 0 <= idx < len(incs):
            send(api, pid, fmt_incident(incs[idx]))
        else:
            send(api, pid, f"❌ #{txt} не найдено (1-{len(incs)})")

    else:
        send(api, pid, "Нажмите кнопку или /help 👇", main_kb())


# ═══════════════════════════════════════
#  WATCHER — авторассылка новых инцидентов
# ═══════════════════════════════════════

class Watcher:
    def __init__(self, api):
        self.api = api
        self.seen = set()
        for inc in get_incidents():
            self.seen.add(inc.get("uid", ""))
        log.info(f"[Watcher] Загружено {len(self.seen)} инцидентов")

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        log.info(f"[Watcher] Запущен (каждые {POLL_INTERVAL}с)")

    def _loop(self):
        while True:
            try:
                for inc in get_incidents():
                    uid = inc.get("uid", "")
                    if uid and uid not in self.seen:
                        self.seen.add(uid)
                        self._notify(inc)
            except Exception as e:
                log.error(f"[Watcher] {e}")
            time.sleep(POLL_INTERVAL)

    def _notify(self, inc):
        msg = fmt_sos(inc) if inc.get("type") == "sos" else fmt_incident(inc)
        targets = set(subscribers)
        if VK_NOTIFY_PEER_ID:
            targets.add(int(VK_NOTIFY_PEER_ID))
        for pid in targets:
            try:
                send(self.api, pid, msg)
            except: pass
        t = TYPES.get(inc.get("type", ""), {}).get("label", "?")
        log.info(f"[Notify] {t} → {len(targets)} получателей")


# ═══════════════════════════════════════
#  WEBHOOK SERVER — receives pushes from Node.js app
# ═══════════════════════════════════════

_global_api = None

def start_webhook_server(api, port=5050):
    """HTTP server for instant notifications from Citizen app"""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    _global_api_ref = api

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            path = self.path

            targets = set(subscribers)
            if VK_NOTIFY_PEER_ID:
                targets.add(int(VK_NOTIFY_PEER_ID))

            msg = None

            if path == "/notify/incident":
                msg = fmt_incident(body)
            elif path == "/notify/sos":
                msg = fmt_sos(body)
            elif path == "/notify/emergency":
                title = body.get("title", "ALERT")
                message = body.get("message", "")
                lat = body.get("lat", 0)
                lng = body.get("lng", 0)
                msg = (
                    f"🔴🔴🔴 ЭКСТРЕННОЕ ОПОВЕЩЕНИЕ 🔴🔴🔴\n\n"
                    f"📢 {title}\n{message}\n\n"
                    f"📍 Радиус: {body.get('radius_km', 5)} км\n"
                    f"🗺 https://maps.google.com/?q={lat},{lng}"
                )
            elif path == "/notify/status":
                inc = body.get("incident", {})
                status = body.get("status", "")
                labels = {"confirmed": "✅ Подтверждено", "resolved": "☑️ Закрыто", "fake": "❌ Фейк", "responding": "📡 Стример выехал"}
                t = TYPES.get(inc.get("type", ""), {})
                desc = inc.get("description", "")[:60]
                msg = f"{t.get('emoji', '📌')} Обновление: {desc}\n📊 {labels.get(status, status)}"
            elif path == "/notify/comment":
                username = body.get("username", "?")
                text = body.get("text", "")[:80]
                msg = f"💬 {username} прокомментировал: {text}"
            elif path == "/notify/streamer":
                username = body.get("username", "?")
                action = body.get("action", "claimed")
                if action == "claimed":
                    msg = f"📡 Стример {username} выезжает на место"
                elif action == "arrived":
                    msg = f"📡 Стример {username} на месте"

            if msg and targets:
                for pid in targets:
                    try:
                        send(_global_api_ref, pid, msg)
                    except Exception as e:
                        log.error(f"[Webhook] Send error to {pid}: {e}")
                log.info(f"[Webhook] {path} → {len(targets)} recipients")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "subscribers": len(subscribers)}).encode())

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"🔗 Webhook-сервер: порт {port}")
    return server


# ═══════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════

def main():
    log.info("═" * 40)
    log.info("  ⚡ Citizen Monitor — VK Bot")
    log.info("═" * 40)

    session, api = init_vk()
    longpoll = VkLongPoll(session)

    Watcher(api).start()
    
    # Start webhook server for real-time pushes from Node.js
    webhook_port = int(os.getenv("WEBHOOK_PORT", "5050"))
    start_webhook_server(api, port=webhook_port)

    log.info(f"🤖 Бот запущен | Подписчиков: {len(subscribers)}")

    try:
        for event in longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                handle(api, event)
    except KeyboardInterrupt:
        log.info("\n🛑 Остановлен")
    except Exception as e:
        log.error(f"❌ {e}")
        raise

if __name__ == "__main__":
    main()
