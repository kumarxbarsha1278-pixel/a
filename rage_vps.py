#!/usr/bin/env python3
"""
⚡ RAGEBITE VPS ALL-IN-ONE
✅ Flask API + Key Bot + Maintenance
✅ 2 Messages: pehle /bgmi command, phir details (with key)
"""

import os
import sqlite3
import threading
import time
import random
import string
from datetime import datetime, timedelta

import telebot
from telebot import apihelper
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# ==================== CONFIG ====================
KEY_BOT_TOKEN = "8823908635:AAHO373_iqEcipIOdhACahEO-3O-ZipA21g"
DD_BOT_TOKEN = "8650600804:AAFw-AuiLMtbUUHIbqwdPzVeOG8s11yfdA8"
OWNER_ID = 6321758394

API_SECRET = "RAGEBITE_SECRET_2026_CHANGE_ME"
API_PORT = 5000

APP_IDS = ["com.ragebite.app"]
SLOTS_PER_APP = 4

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ragebite.db')

STATUS_ACTIVE = "ACTIVE"
STATUS_EXPIRED = "EXPIRED"
STATUS_DELETED = "DELETED"
STATUS_DISABLED = "DISABLED"

rate_limit_store = {}
db_write_lock = threading.RLock()

apihelper.CONNECT_TIMEOUT = 10
apihelper.READ_TIMEOUT = 10


# ==================== DATABASE ====================
def get_conn():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS keys (
        key TEXT PRIMARY KEY,
        device_id TEXT,
        expiry TEXT,
        status TEXT DEFAULT 'ACTIVE',
        slot_count INTEGER DEFAULT 4,
        app_id TEXT,
        created_at TEXT,
        generated_by TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS slots (
        slot_id INTEGER,
        app_id TEXT,
        device_id TEXT,
        key TEXT,
        package_name TEXT,
        ip TEXT,
        port TEXT,
        time_sec INTEGER,
        start_time TEXT,
        end_time TEXT,
        is_active INTEGER DEFAULT 0,
        PRIMARY KEY (app_id, slot_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off')")

    for app_id in APP_IDS:
        for i in range(1, SLOTS_PER_APP + 1):
            c.execute('INSERT OR IGNORE INTO slots (app_id, slot_id, is_active) VALUES (?, ?, 0)', (app_id, i))

    conn.commit()
    conn.close()
    print(f"✅ Database ready: {DB_NAME}")


def get_maintenance():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='maintenance'")
    row = c.fetchone()
    conn.close()
    return row[0] if row else "off"


def set_maintenance(value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('maintenance', ?)", (value,))
    conn.commit()
    conn.close()


def generate_key(days, app_id, slot_count=4):
    key = "LTN-1M-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_conn()
    c = conn.cursor()
    c.execute('''INSERT INTO keys (key, device_id, expiry, status, slot_count, app_id, created_at, generated_by)
                 VALUES (?, NULL, ?, ?, ?, ?, ?, ?)''',
              (key, expiry, STATUS_ACTIVE, slot_count, app_id,
               datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               str(OWNER_ID)))
    conn.commit()
    conn.close()
    return key, expiry


def verify_key_with_device(key, device_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT expiry, status, device_id FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            if not row:
                return None, "NOT_FOUND", False
            expiry_str, status, existing_device = row
            if status == STATUS_DELETED:
                return None, "DELETED", False
            if status == STATUS_DISABLED:
                return None, "DISABLED", False
            expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
            if expiry < datetime.now():
                c.execute('UPDATE keys SET status = ? WHERE key = ?', (STATUS_EXPIRED, key))
                conn.commit()
                return None, "EXPIRED", False
            if existing_device is None:
                c.execute('UPDATE keys SET device_id = ? WHERE key = ?', (device_id, key))
                conn.commit()
                print(f"🔗 Device bound: {key[:15]}...")
                return int(expiry.timestamp() * 1000), "VALID", True
            elif existing_device == device_id:
                return int(expiry.timestamp() * 1000), "VALID", True
            else:
                return None, "DEVICE_MISMATCH", False
        finally:
            conn.close()


def is_device_authorized(device_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''SELECT key, expiry, status FROM keys 
                         WHERE device_id = ? ORDER BY created_at DESC LIMIT 1''', (device_id,))
            row = c.fetchone()
            if not row:
                return False, None, "NO_KEY"
            key, expiry_str, status = row
            if status == STATUS_DELETED:
                return False, key, "DELETED"
            if status == STATUS_DISABLED:
                return False, key, "DISABLED"
            expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
            if expiry < datetime.now():
                c.execute('UPDATE keys SET status = ? WHERE key = ?', (STATUS_EXPIRED, key))
                conn.commit()
                return False, key, "EXPIRED"
            return True, key, "VALID"
        finally:
            conn.close()


def allot_slot(app_id, device_id, key, package_name, ip, port, time_sec):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT slot_count FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            key_slot_count = row[0] if row else 4
            c.execute('SELECT COUNT(*) FROM slots WHERE key = ? AND is_active = 1', (key,))
            used_slots = c.fetchone()[0]
            if used_slots >= key_slot_count:
                return None, "KEY_SLOTS_FULL"
            c.execute('SELECT slot_id FROM slots WHERE app_id=? AND device_id=? AND is_active=1', (app_id, device_id))
            if c.fetchone():
                return None, "ALREADY_ACTIVE"
            c.execute('''SELECT slot_id FROM slots 
                         WHERE app_id=? AND is_active=0 AND slot_id <= ? 
                         ORDER BY slot_id ASC LIMIT 1''', (app_id, SLOTS_PER_APP))
            slot_row = c.fetchone()
            if slot_row is None:
                return None, "ALL_SLOTS_FULL"
            slot_id = slot_row[0]
            start = datetime.now()
            end = start + timedelta(seconds=time_sec)
            c.execute('''UPDATE slots SET device_id=?, key=?, package_name=?, 
                         ip=?, port=?, time_sec=?, start_time=?, end_time=?, is_active=1 
                         WHERE app_id=? AND slot_id=?''',
                      (device_id, key, package_name, ip, port, time_sec,
                       start.strftime('%Y-%m-%d %H:%M:%S'),
                       end.strftime('%Y-%m-%d %H:%M:%S'),
                       app_id, slot_id))
            conn.commit()
            return slot_id, "OK"
        finally:
            conn.close()


def release_slot(app_id, slot_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''UPDATE slots SET device_id=NULL, key=NULL, package_name=NULL,
                         ip=NULL, port=NULL, time_sec=NULL, start_time=NULL,
                         end_time=NULL, is_active=0 WHERE app_id=? AND slot_id=?''',
                      (app_id, slot_id))
            conn.commit()
        finally:
            conn.close()


def get_app_slots(app_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM slots WHERE app_id=? ORDER BY slot_id', (app_id,))
        return c.fetchall()
    finally:
        conn.close()


def get_expired_slots():
    conn = get_conn()
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('SELECT app_id, slot_id FROM slots WHERE is_active=1 AND end_time <= ?', (now_str,))
        return c.fetchall()
    finally:
        conn.close()


def check_rate_limit(identifier, max_requests=15, window=60):
    now_ts = time.time()
    if identifier not in rate_limit_store:
        rate_limit_store[identifier] = []
    rate_limit_store[identifier] = [t for t in rate_limit_store[identifier] if now_ts - t < window]
    if len(rate_limit_store[identifier]) >= max_requests:
        return False
    rate_limit_store[identifier].append(now_ts)
    return True


def notify_owner_dd(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{DD_BOT_TOKEN}/sendMessage",
            json={"chat_id": OWNER_ID, "text": text},
            timeout=5)
        print(f"📤 DM sent: {text[:50]}")
    except Exception as e:
        print(f"❌ DM error: {e}")


# ==================== FLASK API ====================
app = Flask(__name__)
CORS(app)


def check_auth():
    return request.headers.get('X-API-KEY') == API_SECRET


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({"status": "OK", "apps": APP_IDS, "slots_per_app": SLOTS_PER_APP})


@app.route('/api/verify', methods=['POST'])
def api_verify():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    key = data.get('key', '').upper().strip()
    device_id = data.get('device_id', '').strip()

    if not device_id:
        return jsonify({"status": "INVALID", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "INVALID", "reason": "NoKey"})

    expiry, status, _ = verify_key_with_device(key, device_id)
    if status == "VALID":
        return jsonify({"status": "VALID", "expiry": expiry})
    return jsonify({"status": "INVALID", "reason": status})


@app.route('/api/slots/status', methods=['GET'])
def api_all_slots():
    result = {}
    for app_id in APP_IDS:
        rows = get_app_slots(app_id)
        slots = []
        for r in rows:
            if r[10]:
                try:
                    rem = int((datetime.strptime(r[9], '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds())
                except:
                    rem = 0
                slots.append({"slot": r[0], "status": "BUSY", "remaining": max(0, rem)})
            else:
                slots.append({"slot": r[0], "status": "FREE", "remaining": 0})
        active = sum(1 for s in slots if s["status"] == "BUSY")
        result[app_id] = {"slots": slots, "active": active, "max": SLOTS_PER_APP}
    return jsonify(result)


@app.route('/api/slots/status/<app_id>', methods=['GET'])
def api_app_slots(app_id):
    if app_id not in APP_IDS:
        return jsonify({"error": "Unknown app", "valid": APP_IDS}), 404

    rows = get_app_slots(app_id)
    slots = []
    for r in rows:
        if r[10]:
            try:
                rem = int((datetime.strptime(r[9], '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds())
            except:
                rem = 0
            slots.append({"slot": r[0], "status": "BUSY", "remaining": max(0, rem)})
        else:
            slots.append({"slot": r[0], "status": "FREE", "remaining": 0})
    active = sum(1 for s in slots if s["status"] == "BUSY")
    return jsonify({"app_id": app_id, "slots": slots, "active": active, "max": SLOTS_PER_APP})


@app.route('/api/dd', methods=['POST'])
def api_dd():
    if not check_auth():
        return jsonify({"status": "ERROR", "reason": "Unauthorized"}), 401

    if get_maintenance() == "on":
        return jsonify({
            "status": "ERROR",
            "reason": "MAINTENANCE",
            "message": "Bot maintenance pe hai."
        })

    client_ip = request.remote_addr
    if not check_rate_limit(client_ip, max_requests=15, window=60):
        return jsonify({"status": "ERROR", "reason": "RateLimit"})

    data = request.json or {}
    device_id = data.get('device_id', '').strip()
    key = data.get('key', '').upper().strip()
    ip = data.get('ip', '').strip()
    port = str(data.get('port', '')).strip()
    time_sec = int(data.get('time', 0))
    pkg = data.get('package', APP_IDS[0])

    if not device_id:
        return jsonify({"status": "ERROR", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "ERROR", "reason": "NoKey"})
    if not ip or not port:
        return jsonify({"status": "ERROR", "reason": "MissingIPPort"})
    if time_sec < 10 or time_sec > 300:
        return jsonify({"status": "ERROR", "reason": "InvalidTime"})

    if pkg not in APP_IDS:
        return jsonify({"status": "ERROR", "reason": "UnknownApp", "valid": APP_IDS})

    expiry, status, _ = verify_key_with_device(key, device_id)
    if status != "VALID":
        return jsonify({"status": "ERROR", "reason": status})

    authorized, user_key, reason = is_device_authorized(device_id)
    if not authorized:
        return jsonify({"status": "ERROR", "reason": reason})

    slot_id, slot_status = allot_slot(pkg, device_id, key, pkg, ip, port, time_sec)

    if slot_status == "ALREADY_ACTIVE":
        return jsonify({"status": "ERROR", "reason": "AlreadyActive",
                        "message": "Aapka ek attack already chal raha hai"})

    if slot_status == "KEY_SLOTS_FULL":
        return jsonify({"status": "ERROR", "reason": "KeySlotsFull",
                        "message": "Aapki key ke saare slots busy hain"})

    if slot_id is None:
        return jsonify({"status": "ERROR", "reason": "SlotsFull",
                        "message": f"App {pkg} ke saare slots busy hain"})

    # ✅ PEHLA MSG — command (bridge forward karega)
    notify_owner_dd(f"/bgmi {ip} {port} {time_sec} {pkg}")

    # ✅ DOOSRA MSG — details (with key)
    details = (
        f"🔔 ATTACK REQUEST\n\n"
        f"🔑 Key: {key}\n"
        f"📱 Device: {device_id[:20]}...\n"
        f"🎯 Target: {ip}:{port}\n"
        f"⏱ Time: {time_sec}s\n"
        f"📌 Slot: #{slot_id}\n"
        f"📦 App: {pkg}\n"
        f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    notify_owner_dd(details)

    print(f"✅ Attack request: {ip}:{port} | Slot {slot_id}")

    end = datetime.now() + timedelta(seconds=time_sec)
    return jsonify({
        "status": "SLOT_ALLOTTED",
        "app_id": pkg,
        "slot": slot_id,
        "ip": ip,
        "port": port,
        "time": time_sec,
        "end_time": end.strftime('%H:%M:%S')
    })


def auto_release_loop():
    while True:
        try:
            for app_id, slot_id in get_expired_slots():
                release_slot(app_id, slot_id)
                print(f"✅ Released {app_id} slot #{slot_id}")
        except Exception as e:
            print(f"Auto-release: {e}")
        time.sleep(5)


# ==================== TELEGRAM KEY BOT ====================
bot = telebot.TeleBot(KEY_BOT_TOKEN)


def is_owner(uid):
    return str(uid) == str(OWNER_ID)


@bot.message_handler(commands=['start'])
def cmd_start(message):
    if not is_owner(message.from_user.id):
        return

    maint = get_maintenance()
    maint_status = "🔴 ON" if maint == "on" else "🟢 OFF"
    apps_list = "\n".join([f"• `{a}`" for a in APP_IDS])

    bot.reply_to(message, f"""🔑 **RAGEBITE KEY BOT**

🔧 Maintenance: {maint_status}

📱 **Apps:**
{apps_list}

**Commands:**
`/genkey <days> <app_id> [slots]`
`/listkeys`
`/delkey <key>`
`/maintenance on|off`

**Example:**
`/genkey 30 {APP_IDS[0]} 4`
""", parse_mode='Markdown')


@bot.message_handler(commands=['maintenance'])
def cmd_maintenance(message):
    if not is_owner(message.from_user.id):
        return

    command = message.text.split()

    if len(command) < 2:
        maint = get_maintenance()
        status = "🔴 ON" if maint == "on" else "🟢 OFF"
        bot.reply_to(message, f"🔧 **MAINTENANCE: {status}**", parse_mode='Markdown')
        return

    action = command[1].lower()

    if action == "on":
        set_maintenance("on")
        bot.reply_to(message, "🔧 **MAINTENANCE: ON**", parse_mode='Markdown')
    elif action == "off":
        set_maintenance("off")
        bot.reply_to(message, "✅ **MAINTENANCE: OFF**", parse_mode='Markdown')


@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    if not is_owner(message.from_user.id):
        return

    command = message.text.split()
    if len(command) < 3:
        apps_list = "\n".join([f"• `{a}`" for a in APP_IDS])
        bot.reply_to(message, f"❌ `/genkey <days> <app_id> [slots]`\n\nApps:\n{apps_list}", parse_mode='Markdown')
        return

    try:
        days = int(command[1])
    except ValueError:
        bot.reply_to(message, "❌ Invalid days")
        return

    app_id = command[2]
    if app_id not in APP_IDS:
        bot.reply_to(message, f"❌ Unknown app. Available: {', '.join(APP_IDS)}")
        return

    try:
        slots = int(command[3]) if len(command) > 3 else 4
    except ValueError:
        slots = 4

    if days < 1 or days > 3650:
        bot.reply_to(message, "❌ Days 1-3650")
        return

    if slots < 1 or slots > 4:
        slots = 4

    key, expiry = generate_key(days, app_id, slots)

    bot.reply_to(message, f"""✅ **Key Generated**

🔑 `{key}`
📱 `{app_id}`
📅 {expiry}
⏱ {days} days
🎯 {slots} slots""", parse_mode='Markdown')


@bot.message_handler(commands=['listkeys'])
def cmd_listkeys(message):
    if not is_owner(message.from_user.id):
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT key, status, expiry, app_id FROM keys ORDER BY created_at DESC LIMIT 20')
    rows = c.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "ℹ️ No keys")
        return

    response = "🔑 **KEYS**\n\n"
    for k in rows:
        response += f"`{k[0]}`\n  {k[1]} | {k[2]} | `{k[3]}`\n\n"

    bot.reply_to(message, response, parse_mode='Markdown')


@bot.message_handler(commands=['delkey'])
def cmd_delkey(message):
    if not is_owner(message.from_user.id):
        return

    command = message.text.split()
    if len(command) != 2:
        bot.reply_to(message, "❌ `/delkey <key>`", parse_mode='Markdown')
        return

    key = command[1].upper()

    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE keys SET status = ? WHERE key = ?', (STATUS_DELETED, key))
    conn.commit()
    conn.close()

    bot.reply_to(message, f"✅ `{key}` deleted", parse_mode='Markdown')


def run_key_bot():
    while True:
        try:
            bot.polling(non_stop=True, interval=1, timeout=10, long_polling_timeout=10)
        except Exception as e:
            print(f"⚠️ Key Bot error: {e}")
            time.sleep(5)


# ==================== MAIN ====================
def main():
    init_db()

    print("=" * 60)
    print("🛡️ RAGEBITE VPS ALL-IN-ONE")
    print("=" * 60)
    print(f"📱 Apps: {len(APP_IDS)}")
    for app_id in APP_IDS:
        print(f"   • {app_id} — {SLOTS_PER_APP} slots")
    print(f"📊 Total Slots: {len(APP_IDS) * SLOTS_PER_APP}")
    print(f"🌐 API Port: {API_PORT}")
    print(f"🔧 Maintenance: {get_maintenance().upper()}")
    print(f"🤖 Key Bot: @KEY_SWARGBOT")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    threading.Thread(target=auto_release_loop, daemon=True).start()
    threading.Thread(target=run_key_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
