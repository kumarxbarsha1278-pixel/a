#!/usr/bin/env python3
"""
⚡ LIGHTNING VPS — Role-Based Key Management

✅ App names only (no numbers shown to users)
✅ Key prefix = app name (NINJA-, XSILENT-, etc.)
✅ Strict per-app key isolation (koi key kisi dusre app me nahi chalegi)
✅ RageBite (com.ragebite.app) — package bypass ONLY
✅ Time-based keys (5m, 2h, 1d, etc.)
✅ Expiry = hard delete from DB
✅ /setslots (owner only)
✅ /resetkey (owner/admin/reseller)
"""

import os
import re
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
DD_BOT_TOKEN  = "8650600804:AAFw-AuiLMtbUUHIbqwdPzVeOG8s11yfdA8"
OWNER_ID = 6321758394

API_SECRET = "RAGEBITE_SECRET_2026_CHANGE_ME"
API_PORT = 5000

# 🔒 INTERNAL — app definitions
_APPS = {
    "com.ragebite.app":   {"name": "RageBite",  "prefix": "RAGEBITE", "default_slots": 4},
    "com.ragebite.one":   {"name": "Lightning", "prefix": "LIGHTNING","default_slots": 6},
    "com.ragebite.two":   {"name": "XSilent",   "prefix": "XSILENT",  "default_slots": 4},
    "com.ragebite.three": {"name": "VIP Mods",  "prefix": "VIPMODS",  "default_slots": 4},
    "com.ragebite.four":  {"name": "Ninja",     "prefix": "NINJA",    "default_slots": 4},
}

_NAME_TO_PKG = {v["name"].lower().replace(" ", ""): k for k, v in _APPS.items()}
_PKG_TO_NAME = {k: v["name"] for k, v in _APPS.items()}
_PKG_TO_PREFIX = {k: v["prefix"] for k, v in _APPS.items()}

APP_IDS = list(_APPS.keys())

# ✅ ONLY RageBite (com.ragebite.app) — package bypass
BYPASS_PACKAGES = {"com.ragebite.app"}

SLOTS = 4
MAX_KEY_SLOTS = 10

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lightning.db')

STATUS_ACTIVE = "ACTIVE"
STATUS_DELETED = "DELETED"
STATUS_DISABLED = "DISABLED"

rate_limit_store = {}
db_write_lock = threading.RLock()

apihelper.CONNECT_TIMEOUT = 10
apihelper.READ_TIMEOUT = 10


# ==================== HELPERS ====================
def resolve_app(name_or_pkg):
    if not name_or_pkg:
        return None
    s = str(name_or_pkg).strip()
    if s in _APPS:
        return s
    key = s.lower().replace(" ", "")
    return _NAME_TO_PKG.get(key)


def app_display(pkg):
    return _PKG_TO_NAME.get(pkg, "?")


def app_prefix(pkg):
    return _PKG_TO_PREFIX.get(pkg, "KEY")


def get_app_default_slots(pkg):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (f"slots:{pkg}",))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return int(row[0])
        except:
            pass
    return _APPS.get(pkg, {}).get("default_slots", 4)


def set_app_default_slots(pkg, slots):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
              (f"slots:{pkg}", str(slots)))
    conn.commit()
    conn.close()


def parse_duration(s):
    """'5m' → 300, '2h' → 7200, '1d' → 86400. Returns (sec, display) ya (None, None)."""
    if not s:
        return None, None
    s = str(s).strip().lower()
    m = re.match(r'^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', s)
    if not m:
        return None, None
    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith('m'):
        sec = n * 60
        disp = f"{n} min"
    elif unit.startswith('h'):
        sec = n * 3600
        disp = f"{n} hr"
    else:
        sec = n * 86400
        disp = f"{n} day" + ("s" if n != 1 else "")
    if sec < 60:
        return None, None
    if sec > 3650 * 86400:
        return None, None
    return sec, disp


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
        app_id TEXT,
        generated_by TEXT,
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

    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        telegram_id TEXT,
        app_id TEXT,
        added_at TEXT,
        PRIMARY KEY (telegram_id, app_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS resellers (
        telegram_id TEXT,
        app_id TEXT,
        balance INTEGER DEFAULT 0,
        added_at TEXT,
        PRIMARY KEY (telegram_id, app_id)
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


# ==================== ADMIN ====================
def add_admin(telegram_id, app_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO admins (telegram_id, app_id, added_at) VALUES (?, ?, ?)',
              (str(telegram_id), app_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def remove_admin(telegram_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('DELETE FROM admins WHERE telegram_id=?', (str(telegram_id),))
    conn.commit()
    conn.close()


def get_admin_app(telegram_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT app_id FROM admins WHERE telegram_id=? LIMIT 1', (str(telegram_id),))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def is_admin(telegram_id):
    return get_admin_app(telegram_id) is not None


def list_admins():
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT telegram_id, app_id FROM admins ORDER BY app_id')
    rows = c.fetchall()
    conn.close()
    return rows


# ==================== RESELLER ====================
def add_reseller(telegram_id, app_id, balance=0):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO resellers (telegram_id, app_id, balance, added_at) VALUES (?, ?, ?, ?)',
              (str(telegram_id), app_id, balance, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()


def remove_reseller(telegram_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('DELETE FROM resellers WHERE telegram_id=?', (str(telegram_id),))
    conn.commit()
    conn.close()


def get_reseller_app(telegram_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT app_id, balance FROM resellers WHERE telegram_id=? LIMIT 1', (str(telegram_id),))
    row = c.fetchone()
    conn.close()
    return row if row else (None, 0)


def is_reseller(telegram_id):
    return get_reseller_app(telegram_id)[0] is not None


def get_reseller_balance(telegram_id, app_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT balance FROM resellers WHERE telegram_id=? AND app_id=?', (str(telegram_id), app_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0


def set_reseller_balance(telegram_id, app_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE resellers SET balance=? WHERE telegram_id=? AND app_id=?',
              (amount, str(telegram_id), app_id))
    conn.commit()
    conn.close()


def add_reseller_balance(telegram_id, app_id, amount):
    current = get_reseller_balance(telegram_id, app_id)
    new = current + amount
    set_reseller_balance(telegram_id, app_id, new)
    return new


def deduct_reseller_balance(telegram_id, app_id, amount):
    current = get_reseller_balance(telegram_id, app_id)
    if current < amount:
        return False, current
    new = current - amount
    set_reseller_balance(telegram_id, app_id, new)
    return True, new


def list_resellers(app_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT telegram_id, balance FROM resellers WHERE app_id=? ORDER BY telegram_id', (app_id,))
    rows = c.fetchall()
    conn.close()
    return rows


# ==================== KEYS ====================
def generate_key(duration_sec, app_id, generated_by, slot_count=None):
    if slot_count is None:
        slot_count = get_app_default_slots(app_id)
    slot_count = max(1, min(MAX_KEY_SLOTS, int(slot_count)))

    prefix = app_prefix(app_id)
    body = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    key = f"{prefix}-{body}"

    expiry = (datetime.now() + timedelta(seconds=duration_sec)).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_conn()
    c = conn.cursor()
    c.execute('''INSERT INTO keys (key, device_id, expiry, status, slot_count, app_id, generated_by, created_at)
                 VALUES (?, NULL, ?, ?, ?, ?, ?, ?)''',
              (key, expiry, STATUS_ACTIVE, slot_count, app_id, str(generated_by),
               datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    return key, expiry, slot_count


def delete_key_hard(key):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('DELETE FROM keys WHERE key=?', (key,))
            c.execute('''UPDATE slots SET device_id=NULL, key=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                         WHERE key=?''', (key,))
            conn.commit()
        finally:
            conn.close()


def delete_key_soft(key):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE keys SET status = ? WHERE key = ?', (STATUS_DELETED, key))
    conn.commit()
    conn.close()


def list_keys(app_id=None, generated_by=None):
    conn = get_conn()
    c = conn.cursor()
    query = 'SELECT key, status, expiry FROM keys WHERE 1=1'
    params = []
    if app_id:
        query += ' AND app_id=?'
        params.append(app_id)
    if generated_by:
        query += ' AND generated_by=?'
        params.append(str(generated_by))
    query += ' ORDER BY created_at DESC LIMIT 20'
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return rows


def get_key_info(key):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT app_id, generated_by FROM keys WHERE key=?', (key,))
    row = c.fetchone()
    conn.close()
    return row


def verify_key_with_device(key, device_id, app_id):
    """Strict per-app check. Expired → hard delete."""
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT expiry, status, device_id, app_id FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            if not row:
                return None, "NOT_FOUND", False
            expiry_str, status, existing_device, key_app_id = row

            if key_app_id and key_app_id != app_id:
                return None, "WRONG_APP", False

            if status == STATUS_DELETED:
                return None, "DELETED", False
            if status == STATUS_DISABLED:
                return None, "DISABLED", False

            expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
            if expiry < datetime.now():
                c.execute('DELETE FROM keys WHERE key=?', (key,))
                c.execute('''UPDATE slots SET device_id=NULL, key=NULL, ip=NULL, port=NULL,
                             time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                             WHERE key=?''', (key,))
                conn.commit()
                return None, "EXPIRED", False

            if existing_device is None:
                c.execute('UPDATE keys SET device_id = ? WHERE key = ?', (device_id, key))
                conn.commit()
                return int(expiry.timestamp() * 1000), "VALID", True
            elif existing_device == device_id:
                return int(expiry.timestamp() * 1000), "VALID", True
            else:
                return None, "DEVICE_MISMATCH", False
        finally:
            conn.close()


def reset_key(key):
    """Device unlock + slots release. Expiry same."""
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT expiry FROM keys WHERE key=?', (key,))
            row = c.fetchone()
            if not row:
                return False
            expiry = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
            if expiry < datetime.now():
                return False
            c.execute('UPDATE keys SET device_id=NULL WHERE key=?', (key,))
            c.execute('''UPDATE slots SET device_id=NULL, key=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                         WHERE key=?''', (key,))
            conn.commit()
            return True
        finally:
            conn.close()


# ==================== SLOTS (attacks) ====================
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


def get_expired_keys():
    conn = get_conn()
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('SELECT key FROM keys WHERE expiry <= ?', (now_str,))
        return [r[0] for r in c.fetchall()]
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


def normalize_package(pkg, key=None):
    """Sirf RageBite ka bypass. Baaki strict."""
    if key:
        info = get_key_info(key)
        if info:
            key_app_id = info[0]
            if key_app_id in BYPASS_PACKAGES:
                return "com.ragebite.app"
            return pkg
    return pkg


def build_slots_response():
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
    return {"slots": slots, "active": active, "max": SLOTS}


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({"status": "OK"})


@app.route('/api/verify', methods=['POST'])
def api_verify():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    key = data.get('key', '').upper().strip()
    device_id = data.get('device_id', '').strip()
    pkg = data.get('package', APP_IDS[0])

    if not device_id:
        return jsonify({"status": "INVALID", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "INVALID", "reason": "NoKey"})

    pkg = normalize_package(pkg, key)

    if pkg not in APP_IDS:
        return jsonify({"status": "INVALID", "reason": "UnknownApp"})

    expiry, status, _ = verify_key_with_device(key, device_id, pkg)
    if status == "VALID":
        return jsonify({"status": "VALID", "expiry": expiry})
    return jsonify({"status": "INVALID", "reason": status})


@app.route('/api/slots', methods=['GET'])
def api_slots():
    return jsonify(build_slots_response())


@app.route('/api/slots/status', methods=['GET'])
def api_slots_status():
    return jsonify(build_slots_response())


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
    pkg = data.get('package', APP_IDS[0])

    if not device_id:
        return jsonify({"status": "ERROR", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "ERROR", "reason": "NoKey"})
    if not ip or not port:
        return jsonify({"status": "ERROR", "reason": "MissingIPPort"})
    if time_sec < 10 or time_sec > 300:
        return jsonify({"status": "ERROR", "reason": "InvalidTime"})

    pkg = normalize_package(pkg, key)

    if pkg not in APP_IDS:
        return jsonify({"status": "ERROR", "reason": "UnknownApp"})

    expiry, status, _ = verify_key_with_device(key, device_id, pkg)
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

    print(f"✅ Attack: {ip}:{port} | Slot {slot_id} | Key {key}")
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

            for key in get_expired_keys():
                delete_key_hard(key)
                print(f"🗑️ Expired key deleted: {key}")
        except Exception as e:
            print(f"Auto-release: {e}")
        time.sleep(5)


# ==================== KEY BOT ====================
bot = telebot.TeleBot(KEY_BOT_TOKEN)


def is_owner(uid):
    return str(uid) == str(OWNER_ID)


def app_list_str():
    return ", ".join(_APPS[p]["name"] for p in APP_IDS)


@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id

    if is_owner(uid):
        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** 👑 OWNER

**👑 OWNER Commands:**
`/addadmn <telegram_id> <app_name>`
`/removeadmin <telegram_id>`
`/adminlist`
`/genkey <time> <app_name> [slots]`
`/delkey <key>`
`/resetkey <key>`
`/listkeys [app_name]`
`/setslots <app_name> <slots>`
`/slotinfo`
`/maintenance on|off`

📱 **Apps:** {app_list_str()}

**Time format:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`

**Example:**
`/addadmn 123456789 Lightning`
`/genkey 2h Ninja`
`/genkey 1d Lightning 3`
`/setslots Lightning 6`""", parse_mode='Markdown')
        return

    admin_app = get_admin_app(uid)
    if admin_app:
        name = app_display(admin_app)
        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** ⚡ ADMIN

📱 **Your App:** {name}

**⚡ ADMIN Commands:**
`/genkey <time>`
`/delkey <key>`
`/resetkey <key>`
`/listkeys`
`/addreseller <telegram_id> <coins>`
`/removereseller <telegram_id>`
`/addbalance <telegram_id> <amount>`
`/removebalance <telegram_id> <amount>`
`/resellerlist`

**Time format:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`""", parse_mode='Markdown')
        return

    reseller_app, bal = get_reseller_app(uid)
    if reseller_app:
        name = app_display(reseller_app)
        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** 🛒 RESELLER

📱 **Your App:** {name}
💰 **Balance:** {bal} coins

**🛒 RESELLER Commands:**
`/genkey <time>`
`/delkey <key>`
`/resetkey <key>`
`/keyslist`
`/balance`

**Time format:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`""", parse_mode='Markdown')
        return

    bot.reply_to(message, "❌ Not authorized.\nContact owner.")


@bot.message_handler(commands=['addadmn', 'addadmin'])
def cmd_add_admin(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split(maxsplit=2)
    if len(cmd) != 3:
        bot.reply_to(message, f"❌ `/addadmn <telegram_id> <app_name>`\n\n**Apps:** {app_list_str()}",
                     parse_mode='Markdown')
        return
    telegram_id = cmd[1]
    app_id = resolve_app(cmd[2])
    if not app_id:
        bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
        return
    add_admin(telegram_id, app_id)
    name = app_display(app_id)
    bot.reply_to(message, f"""✅ **Admin Added**

👤 ID: `{telegram_id}`
📱 App: **{name}**""", parse_mode='Markdown')
    try:
        bot.send_message(telegram_id, f"""⚡ **You are now ADMIN!**

📱 Your App: **{name}**

**Commands:**
`/genkey <time>`
`/delkey <key>`
`/resetkey <key>`
`/listkeys`
`/addreseller <id> <coins>`
`/removereseller <id>`
`/addbalance <id> <amount>`
`/removebalance <id> <amount>`
`/resellerlist`

**Time:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`""", parse_mode='Markdown')
    except:
        pass


@bot.message_handler(commands=['removeadmin'])
def cmd_remove_admin(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/removeadmin <telegram_id>`")
        return
    telegram_id = cmd[1]
    remove_admin(telegram_id)
    bot.reply_to(message, f"✅ Admin removed: `{telegram_id}`", parse_mode='Markdown')


@bot.message_handler(commands=['adminlist'])
def cmd_admin_list(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    admins = list_admins()
    if not admins:
        bot.reply_to(message, "ℹ️ No admins")
        return
    r = "⚡ **ADMINS**\n\n"
    for tid, app_id in admins:
        r += f"👤 `{tid}` → {app_display(app_id)}\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['setslots'])
def cmd_setslots(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split(maxsplit=2)
    if len(cmd) != 3:
        bot.reply_to(message, f"❌ `/setslots <app_name> <slots>`\n\n**Apps:** {app_list_str()}",
                     parse_mode='Markdown')
        return
    app_id = resolve_app(cmd[1])
    if not app_id:
        bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
        return
    try:
        slots = int(cmd[2])
    except:
        bot.reply_to(message, f"❌ Slots must be 1-{MAX_KEY_SLOTS}")
        return
    if slots < 1 or slots > MAX_KEY_SLOTS:
        bot.reply_to(message, f"❌ Slots 1-{MAX_KEY_SLOTS}")
        return
    set_app_default_slots(app_id, slots)
    name = app_display(app_id)
    bot.reply_to(message, f"""✅ **Slots Updated**

📱 App: **{name}**
🎯 New default slots: **{slots}**""", parse_mode='Markdown')


@bot.message_handler(commands=['slotinfo'])
def cmd_slotinfo(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    r = "⚡ **DEFAULT SLOTS**\n\n"
    for pkg in APP_IDS:
        r += f"📱 {app_display(pkg)} → `{get_app_default_slots(pkg)}` slots\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    uid = message.from_user.id
    cmd = message.text.split()

    if is_owner(uid):
        if len(cmd) < 3:
            bot.reply_to(message,
                         f"❌ `/genkey <time> <app_name> [slots]`\n\n**Apps:** {app_list_str()}\n**Time:** `5m` `30m` `1h` `2h` `1d` `30d`",
                         parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time. Use: `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`",
                         parse_mode='Markdown')
            return
        app_id = resolve_app(cmd[2])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
        slots = None
        if len(cmd) > 3:
            try:
                slots = int(cmd[3])
            except:
                bot.reply_to(message, "❌ Slots must be a number")
                return
            if slots < 1 or slots > MAX_KEY_SLOTS:
                bot.reply_to(message, f"❌ Slots 1-{MAX_KEY_SLOTS}")
                return
        key, expiry, used_slots = generate_key(dur_sec, app_id, uid, slots)
        bot.reply_to(message, f"""⚡ **KEY GENERATED**

🔑 `{key}`
📱 App: **{app_display(app_id)}**
⏱ {dur_disp}
🎯 {used_slots} slots
📅 Expires: {expiry}""", parse_mode='Markdown')
        return

    admin_app = get_admin_app(uid)
    reseller_app, _ = get_reseller_app(uid)
    my_app = admin_app or reseller_app

    if my_app:
        if len(cmd) < 2:
            bot.reply_to(message, "❌ `/genkey <time>`\n\n**Time:** `5m` `30m` `1h` `2h` `1d` `30d`",
                         parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time. Use: `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`",
                         parse_mode='Markdown')
            return
        key, expiry, used_slots = generate_key(dur_sec, my_app, uid, None)
        bot.reply_to(message, f"""⚡ **KEY GENERATED**

🔑 `{key}`
📱 App: **{app_display(my_app)}**
⏱ {dur_disp}
🎯 {used_slots} slots
📅 Expires: {expiry}""", parse_mode='Markdown')
        return

    bot.reply_to(message, "❌ Not authorized")


@bot.message_handler(commands=['delkey'])
def cmd_delkey(message):
    uid = message.from_user.id
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/delkey <key>`")
        return
    key = cmd[1].upper()

    info = get_key_info(key)
    if not info:
        bot.reply_to(message, "❌ Key not found")
        return
    key_app, key_gen = info

    if is_owner(uid):
        delete_key_soft(key)
        bot.reply_to(message, f"✅ `{key}` deleted")
        return

    admin_app = get_admin_app(uid)
    if admin_app and key_app == admin_app:
        delete_key_soft(key)
        bot.reply_to(message, f"✅ `{key}` deleted")
        return

    reseller_app, _ = get_reseller_app(uid)
    if reseller_app and key_app == reseller_app and key_gen == str(uid):
        delete_key_soft(key)
        bot.reply_to(message, f"✅ `{key}` deleted")
        return

    bot.reply_to(message, "❌ Permission denied")


@bot.message_handler(commands=['resetkey'])
def cmd_resetkey(message):
    uid = message.from_user.id
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/resetkey <key>`")
        return
    key = cmd[1].upper()

    info = get_key_info(key)
    if not info:
        bot.reply_to(message, "❌ Key not found")
        return
    key_app, key_gen = info

    allowed = False
    if is_owner(uid):
        allowed = True
    else:
        admin_app = get_admin_app(uid)
        if admin_app and key_app == admin_app:
            allowed = True
        else:
            reseller_app, _ = get_reseller_app(uid)
            if reseller_app and key_app == reseller_app and key_gen == str(uid):
                allowed = True

    if not allowed:
        bot.reply_to(message, "❌ Permission denied")
        return

    if reset_key(key):
        bot.reply_to(message, f"""✅ **KEY RESET**

🔑 `{key}`
📱 App: **{app_display(key_app)}**

Device unlock, slots free.
Expiry same rahegi.""", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Reset failed (key expired ya not found)")


@bot.message_handler(commands=['listkeys', 'keyslist'])
def cmd_listkeys(message):
    uid = message.from_user.id
    cmd = message.text.split()

    if is_owner(uid):
        app_id = None
        if len(cmd) > 1:
            app_id = resolve_app(cmd[1])
        rows = list_keys(app_id)
    else:
        admin_app = get_admin_app(uid)
        reseller_app, _ = get_reseller_app(uid)
        app_id = admin_app or reseller_app
        if not app_id:
            bot.reply_to(message, "❌ Not authorized")
            return
        if is_reseller(uid):
            rows = list_keys(app_id, generated_by=uid)
        else:
            rows = list_keys(app_id)

    if not rows:
        bot.reply_to(message, "ℹ️ No keys")
        return

    r = "⚡ **KEYS**\n\n"
    for k in rows[:15]:
        r += f"`{k[0]}`\n  {k[1]} | {k[2]}\n\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['addreseller'])
def cmd_add_reseller(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    if not admin_app and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    cmd = message.text.split()
    if len(cmd) != 3:
        bot.reply_to(message, "❌ `/addreseller <telegram_id> <coins>`")
        return
    try:
        rid = cmd[1]
        coins = int(cmd[2])
    except:
        bot.reply_to(message, "❌ Invalid format")
        return
    if coins < 0:
        bot.reply_to(message, "❌ Coins >= 0")
        return

    app_id = admin_app if admin_app else "com.ragebite.app"
    name = app_display(app_id)
    add_reseller(rid, app_id, coins)
    bot.reply_to(message, f"""✅ **Reseller Added**

👤 ID: `{rid}`
📱 App: **{name}**
💰 Coins: `{coins}`""", parse_mode='Markdown')
    try:
        bot.send_message(rid, f"""⚡ **You are now RESELLER!**

📱 Your App: **{name}**
💰 Balance: `{coins}` coins

**Commands:**
`/genkey <time>`
`/delkey <key>`
`/resetkey <key>`
`/keyslist`
`/balance`""", parse_mode='Markdown')
    except:
        pass


@bot.message_handler(commands=['removereseller'])
def cmd_remove_reseller(message):
    uid = message.from_user.id
    if not is_admin(uid) and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/removereseller <telegram_id>`")
        return
    rid = cmd[1]
    remove_reseller(rid)
    bot.reply_to(message, f"✅ Reseller `{rid}` removed", parse_mode='Markdown')


@bot.message_handler(commands=['resellerlist'])
def cmd_reseller_list(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    if not admin_app and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    app_id = admin_app if admin_app else "com.ragebite.app"
    name = app_display(app_id)
    rows = list_resellers(app_id)
    if not rows:
        bot.reply_to(message, f"ℹ️ No resellers for {name}")
        return
    r = f"🛒 **RESELLERS — {name}**\n\n"
    for tid, bal in rows:
        r += f"👤 `{tid}` | 💰 {bal}\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['addbalance'])
def cmd_add_balance(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    if not admin_app and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    cmd = message.text.split()
    if len(cmd) != 3:
        bot.reply_to(message, "❌ `/addbalance <telegram_id> <amount>`")
        return
    try:
        rid = cmd[1]
        amount = int(cmd[2])
    except:
        bot.reply_to(message, "❌ Invalid format")
        return
    if amount <= 0:
        bot.reply_to(message, "❌ Amount > 0")
        return
    app_id = admin_app if admin_app else "com.ragebite.app"
    new_bal = add_reseller_balance(rid, app_id, amount)
    bot.reply_to(message, f"✅ Added `{amount}` to `{rid}`\n💰 New: `{new_bal}`", parse_mode='Markdown')


@bot.message_handler(commands=['removebalance'])
def cmd_remove_balance(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    if not admin_app and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    cmd = message.text.split()
    if len(cmd) != 3:
        bot.reply_to(message, "❌ `/removebalance <telegram_id> <amount>`")
        return
    try:
        rid = cmd[1]
        amount = int(cmd[2])
    except:
        bot.reply_to(message, "❌ Invalid format")
        return
    if amount <= 0:
        bot.reply_to(message, "❌ Amount > 0")
        return
    app_id = admin_app if admin_app else "com.ragebite.app"
    ok, new_bal = deduct_reseller_balance(rid, app_id, amount)
    if not ok:
        bot.reply_to(message, f"❌ Insufficient: `{new_bal}`", parse_mode='Markdown')
        return
    bot.reply_to(message, f"✅ Removed `{amount}` from `{rid}`\n💰 New: `{new_bal}`", parse_mode='Markdown')


@bot.message_handler(commands=['balance'])
def cmd_balance(message):
    uid = message.from_user.id
    reseller_app, bal = get_reseller_app(uid)
    if not reseller_app:
        bot.reply_to(message, "❌ Reseller only")
        return
    name = app_display(reseller_app)
    bot.reply_to(message, f"💰 **Balance**\n\n📱 App: **{name}**\n💰 `{bal}` coins", parse_mode='Markdown')


@bot.message_handler(commands=['maintenance'])
def cmd_maintenance(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
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
    print(f"👑 Owner: {OWNER_ID}")
    print(f"📱 Apps: {app_list_str()}")
    print(f"📊 Global slots: {SLOTS}")
    print(f"🌐 Port: {API_PORT}")
    print(f"✅ Bypass: {BYPASS_PACKAGES}")
    for pkg in APP_IDS:
        print(f"   • {app_display(pkg):10s} → {get_app_default_slots(pkg)} slots | prefix {app_prefix(pkg)}")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    threading.Thread(target=auto_release_loop, daemon=True).start()
    threading.Thread(target=run_key_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
