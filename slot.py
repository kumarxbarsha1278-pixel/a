#!/usr/bin/env python3
"""
⚡ LIGHTNING VPS — Per-App Slot Pool + Strict Key Isolation + Multi-Device

✅ 5 apps (RageBite, Lightning, XSilent, VIP Mods, Ninja)
✅ Per-app slot pool
✅ Key prefix = app name (NINJA-, XSILENT-, etc.)
✅ 4-layer strict key isolation
✅ RageBite bypass ONLY (Option A)
✅ Multi-device keys (1-20 devices)
✅ Time-based keys (5m, 2h, 1d, etc.)
✅ Expiry = hard delete
✅ /genkey, /bulkkeys, /setslots, /setprice, /resetkey
✅ Balance system (coins × keys × devices)
✅ Owner ko client package bhi dikhega
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

BYPASS_PACKAGES = {"com.ragebite.app"}

MAX_KEY_SLOTS = 20
MAX_KEY_DEVICES = 20
MAX_BULK = 100

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


def get_app_pool_size(pkg):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (f"pool:{pkg}",))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return int(row[0])
        except:
            pass
    return _APPS.get(pkg, {}).get("default_slots", 4)


def set_app_pool_size(pkg, slots):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
              (f"pool:{pkg}", str(slots)))
    conn.commit()
    conn.close()


def get_app_price(pkg):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (f"price:{pkg}",))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return int(row[0])
        except:
            pass
    return 0


def set_app_price(pkg, coins):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
              (f"price:{pkg}", str(coins)))
    conn.commit()
    conn.close()


def parse_duration(s):
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
        max_devices INTEGER DEFAULT 1,
        app_id TEXT,
        generated_by TEXT,
        created_at TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS key_devices (
        key TEXT,
        device_id TEXT,
        bound_at TEXT,
        PRIMARY KEY (key, device_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS slots (
        slot_id INTEGER PRIMARY KEY,
        app_id TEXT,
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

    # Migrations
    try:
        c.execute("ALTER TABLE slots ADD COLUMN app_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE keys ADD COLUMN max_devices INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off')")

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
def generate_key(duration_sec, app_id, generated_by, slot_count=None, max_devices=1):
    if slot_count is None:
        slot_count = get_app_pool_size(app_id)
    slot_count = max(1, min(MAX_KEY_SLOTS, int(slot_count)))
    max_devices = max(1, min(MAX_KEY_DEVICES, int(max_devices)))

    prefix = app_prefix(app_id)
    if not prefix or prefix == "KEY":
        raise ValueError(f"Invalid app_id: {app_id}")

    body = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
    key = f"{prefix}-{body}"

    if not key.startswith(prefix + "-"):
        raise ValueError("Prefix mismatch")

    expiry = (datetime.now() + timedelta(seconds=duration_sec)).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_conn()
    c = conn.cursor()
    c.execute('''INSERT INTO keys (key, device_id, expiry, status, slot_count, max_devices, app_id, generated_by, created_at)
                 VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)''',
              (key, expiry, STATUS_ACTIVE, slot_count, max_devices, app_id, str(generated_by),
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
            c.execute('DELETE FROM key_devices WHERE key=?', (key,))
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
    query = 'SELECT key, status, expiry, max_devices FROM keys WHERE 1=1'
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
    """
    4-layer strict verification + multi-device support.
    """
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT expiry, status, device_id, app_id, max_devices FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            if not row:
                return None, "NOT_FOUND", False
            expiry_str, status, existing_device, key_app_id, max_devices = row

            # Layer 1: app_id NULL/empty
            if not key_app_id:
                return None, "INVALID_KEY_APP", False

            # Layer 2: request app must match key app
            if key_app_id != app_id:
                return None, "WRONG_APP", False

            # Layer 3: prefix check
            expected_prefix = app_prefix(key_app_id)
            if not key.startswith(expected_prefix + "-"):
                return None, "PREFIX_MISMATCH", False

            if status == STATUS_DELETED:
                return None, "DELETED", False
            if status == STATUS_DISABLED:
                return None, "DISABLED", False

            expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
            if expiry < datetime.now():
                c.execute('DELETE FROM keys WHERE key=?', (key,))
                c.execute('DELETE FROM key_devices WHERE key=?', (key,))
                c.execute('''UPDATE slots SET device_id=NULL, key=NULL, ip=NULL, port=NULL,
                             time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                             WHERE key=?''', (key,))
                conn.commit()
                return None, "EXPIRED", False

            max_devices = max_devices or 1

            # Multi-device check
            c.execute('SELECT COUNT(*) FROM key_devices WHERE key=?', (key,))
            dev_count = c.fetchone()[0]

            if dev_count == 0:
                # Pehla device
                c.execute('INSERT OR IGNORE INTO key_devices (key, device_id, bound_at) VALUES (?, ?, ?)',
                          (key, device_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                c.execute('UPDATE keys SET device_id = ? WHERE key = ?', (device_id, key))
                conn.commit()
                return int(expiry.timestamp() * 1000), "VALID", True

            # Device already bound?
            c.execute('SELECT 1 FROM key_devices WHERE key=? AND device_id=?', (key, device_id))
            if c.fetchone():
                return int(expiry.timestamp() * 1000), "VALID", True

            # Naya device — limit check
            if dev_count >= max_devices:
                return None, "DEVICE_LIMIT_REACHED", False

            c.execute('INSERT OR IGNORE INTO key_devices (key, device_id, bound_at) VALUES (?, ?, ?)',
                      (key, device_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            return int(expiry.timestamp() * 1000), "VALID", True
        finally:
            conn.close()


def reset_key(key):
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
            c.execute('DELETE FROM key_devices WHERE key=?', (key,))
            c.execute('''UPDATE slots SET device_id=NULL, key=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                         WHERE key=?''', (key,))
            conn.commit()
            return True
        finally:
            conn.close()


# ==================== SLOTS ====================
def init_app_slots(app_id):
    pool_size = get_app_pool_size(app_id)
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM slots WHERE app_id=?", (app_id,))
    existing = c.fetchone()[0]
    if existing < pool_size:
        for i in range(existing + 1, pool_size + 1):
            c.execute('INSERT INTO slots (app_id, is_active) VALUES (?, 0)', (app_id,))
        conn.commit()
    conn.close()


def allot_slot(device_id, key, ip, port, time_sec, app_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT slot_count FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            key_slot_count = row[0] if row else 4
            c.execute('SELECT COUNT(*) FROM slots WHERE key = ? AND is_active = 1', (key,))
            used_by_key = c.fetchone()[0]
            if used_by_key >= key_slot_count:
                return None, "KEY_SLOTS_FULL"
            c.execute('SELECT slot_id FROM slots WHERE device_id=? AND is_active=1', (device_id,))
            if c.fetchone():
                return None, "ALREADY_ACTIVE"
            c.execute('SELECT slot_id FROM slots WHERE app_id=? AND is_active=0 ORDER BY slot_id ASC LIMIT 1',
                      (app_id,))
            sr = c.fetchone()
            if not sr:
                return None, "APP_POOL_FULL"
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
        c.execute('SELECT slot_id, app_id, device_id, key, ip, port, time_sec, start_time, end_time, is_active FROM slots ORDER BY app_id, slot_id')
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
        print(f"📤 DM sent: {text[:80]}")
    except Exception as e:
        print(f"❌ DM error: {e}")


# ==================== FLASK API ====================
app = Flask(__name__)
CORS(app)


def check_auth():
    return request.headers.get('X-API-KEY') == API_SECRET


def resolve_request_app(key, client_pkg):
    if key:
        info = get_key_info(key)
        if info and info[0]:
            key_app_id = info[0]
            if key_app_id in BYPASS_PACKAGES:
                return "com.ragebite.app"
            return key_app_id
    return client_pkg


def build_slots_response():
    rows = get_all_slots()
    apps_data = {}
    slots_list = []
    for r in rows:
        slot_id, app_id, device_id, key, ip, port, time_sec, start_time, end_time, is_active = r
        if app_id not in apps_data:
            apps_data[app_id] = {"name": app_display(app_id), "used": 0, "total": get_app_pool_size(app_id)}
        if is_active:
            try:
                rem = int((datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds())
            except:
                rem = 0
            apps_data[app_id]["used"] += 1
            slots_list.append({"slot": slot_id, "app": app_display(app_id), "status": "BUSY", "remaining": max(0, rem)})
        else:
            slots_list.append({"slot": slot_id, "app": app_display(app_id), "status": "FREE", "remaining": 0})
    return {"apps": apps_data, "slots": slots_list}


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
    client_pkg = data.get('package', APP_IDS[0])

    if not device_id:
        return jsonify({"status": "INVALID", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "INVALID", "reason": "NoKey"})

    pkg = resolve_request_app(key, client_pkg)
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
    client_pkg = data.get('package', APP_IDS[0])

    if not device_id:
        return jsonify({"status": "ERROR", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "ERROR", "reason": "NoKey"})
    if not ip or not port:
        return jsonify({"status": "ERROR", "reason": "MissingIPPort"})
    if time_sec < 10 or time_sec > 300:
        return jsonify({"status": "ERROR", "reason": "InvalidTime"})

    pkg = resolve_request_app(key, client_pkg)
    if pkg not in APP_IDS:
        return jsonify({"status": "ERROR", "reason": "UnknownApp"})

    expected_prefix = app_prefix(pkg)
    if not key.startswith(expected_prefix + "-"):
        return jsonify({"status": "ERROR", "reason": "KeyPrefixMismatch"})

    expiry, status, _ = verify_key_with_device(key, device_id, pkg)
    if status != "VALID":
        return jsonify({"status": "ERROR", "reason": status})

    slot_id, slot_status = allot_slot(device_id, key, ip, port, time_sec, pkg)
    if slot_status == "ALREADY_ACTIVE":
        return jsonify({"status": "ERROR", "reason": "AlreadyActive"})
    if slot_status == "KEY_SLOTS_FULL":
        return jsonify({"status": "ERROR", "reason": "KeySlotsFull"})
    if slot_id is None:
        return jsonify({"status": "ERROR", "reason": "AppPoolFull"})

    app_name = app_display(pkg)

    # ✅ Owner ko details — client package bhi
    details = (
        f"⚡ ATTACK REQUEST\n\n"
        f"🔑 Key: {key}\n"
        f"📱 App: {app_name}\n"
        f"🎮 Client package: {client_pkg}\n"
        f"🎯 Target: {ip}:{port}\n"
        f"⏱ Duration: {time_sec}s\n"
        f"📌 Slot: {slot_id}\n"
        f"👤 Device: {device_id[:16]}...\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    notify_owner_dd(details)
    notify_owner_dd(f"/bgmi {ip} {port} {time_sec} {app_name}")

    print(f"✅ Attack: {ip}:{port} | Slot {slot_id} | App {app_name} | Key {key}")
    end = datetime.now() + timedelta(seconds=time_sec)
    return jsonify({
        "status": "SLOT_ALLOTTED",
        "slot": slot_id,
        "app": app_name,
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
`/addadmn <id> <app_name>`
`/removeadmin <id>`
`/adminlist`
`/genkey <time> <app_name> [devices]`
`/bulkkeys <count> <time> <app_name>`
`/delkey <key>`
`/resetkey <key>`
`/listkeys [app_name]`
`/setslots <app_name> <slots>`
`/setprice <app_name> <coins>`
`/slotinfo`
`/maintenance on|off`

📱 **Apps:** {app_list_str()}

**Time:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
**Devices:** 1-20

**Example:**
`/genkey 2h Ninja`
`/genkey 2d Ninja 3`
`/bulkkeys 40 2h Ninja`
`/setprice Ninja 10`
`/setslots Lightning 6`""", parse_mode='Markdown')
        return

    admin_app = get_admin_app(uid)
    if admin_app:
        name = app_display(admin_app)
        bal = get_reseller_balance(uid, admin_app)
        price = get_app_price(admin_app)
        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** ⚡ ADMIN

📱 **Your App:** {name}
💰 **Balance:** {bal} coins
🏷 **Price/key:** {price} coins

**⚡ ADMIN Commands:**
`/genkey <time> [devices]`
`/bulkkeys <count> <time>`
`/delkey <key>`
`/resetkey <key>`
`/listkeys`
`/addreseller <id> <coins>`
`/removereseller <id>`
`/addbalance <id> <amount>`
`/removebalance <id> <amount>`
`/resellerlist`

**Time:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
**Devices:** 1-20""", parse_mode='Markdown')
        return

    reseller_app, bal = get_reseller_app(uid)
    if reseller_app:
        name = app_display(reseller_app)
        price = get_app_price(reseller_app)
        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** 🛒 RESELLER

📱 **Your App:** {name}
💰 **Balance:** {bal} coins
🏷 **Price/key:** {price} coins

**🛒 RESELLER Commands:**
`/genkey <time> [devices]`
`/bulkkeys <count> <time>`
`/delkey <key>`
`/resetkey <key>`
`/keyslist`
`/balance`

**Time:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
**Devices:** 1-20""", parse_mode='Markdown')
        return

    bot.reply_to(message, "❌ Not authorized.\nContact owner.")


@bot.message_handler(commands=['addadmn', 'addadmin'])
def cmd_add_admin(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split(maxsplit=2)
    if len(cmd) != 3:
        bot.reply_to(message, f"❌ `/addadmn <id> <app_name>`\n\n**Apps:** {app_list_str()}", parse_mode='Markdown')
        return
    telegram_id = cmd[1]
    app_id = resolve_app(cmd[2])
    if not app_id:
        bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
        return
    add_admin(telegram_id, app_id)
    name = app_display(app_id)
    bot.reply_to(message, f"✅ **Admin Added**\n\n👤 `{telegram_id}`\n📱 **{name}**", parse_mode='Markdown')
    try:
        bot.send_message(telegram_id, f"⚡ You are now ADMIN!\n📱 App: {name}", parse_mode='Markdown')
    except:
        pass


@bot.message_handler(commands=['removeadmin'])
def cmd_remove_admin(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/removeadmin <id>`")
        return
    remove_admin(cmd[1])
    bot.reply_to(message, f"✅ Admin removed: `{cmd[1]}`", parse_mode='Markdown')


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
        bot.reply_to(message, f"❌ `/setslots <app_name> <slots>`\n\n**Apps:** {app_list_str()}", parse_mode='Markdown')
        return
    app_id = resolve_app(cmd[1])
    if not app_id:
        bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
        return
    try:
        slots = int(cmd[2])
    except:
        bot.reply_to(message, f"❌ Slots 1-{MAX_KEY_SLOTS}")
        return
    if slots < 1 or slots > MAX_KEY_SLOTS:
        bot.reply_to(message, f"❌ Slots 1-{MAX_KEY_SLOTS}")
        return
    set_app_pool_size(app_id, slots)
    init_app_slots(app_id)
    bot.reply_to(message, f"✅ **{app_display(app_id)}** pool: **{slots}** slots", parse_mode='Markdown')


@bot.message_handler(commands=['setprice'])
def cmd_setprice(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split(maxsplit=2)
    if len(cmd) != 3:
        bot.reply_to(message, f"❌ `/setprice <app_name> <coins>`\n\n**Apps:** {app_list_str()}", parse_mode='Markdown')
        return
    app_id = resolve_app(cmd[1])
    if not app_id:
        bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
        return
    try:
        coins = int(cmd[2])
    except:
        bot.reply_to(message, "❌ Coins must be a number")
        return
    if coins < 0:
        bot.reply_to(message, "❌ Coins >= 0")
        return
    set_app_price(app_id, coins)
    bot.reply_to(message, f"✅ **{app_display(app_id)}** price: **{coins}** coins/key", parse_mode='Markdown')


@bot.message_handler(commands=['slotinfo'])
def cmd_slotinfo(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    r = "⚡ **APP POOLS & PRICES**\n\n"
    for pkg in APP_IDS:
        pool = get_app_pool_size(pkg)
        price = get_app_price(pkg)
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM slots WHERE app_id=? AND is_active=1", (pkg,))
        used = c.fetchone()[0]
        conn.close()
        r += f"📱 **{app_display(pkg)}** — pool {used}/{pool} | {price} coins/key\n"
    bot.reply_to(message, r, parse_mode='Markdown')


# ==================== GENKEY / BULKKEYS ====================
def _generate_and_reply(message, app_id, dur_sec, dur_disp, devices, slots=None, count=1, is_bulk=False):
    """Common logic for genkey/bulkkeys."""
    uid = message.from_user.id
    is_owner_user = is_owner(uid)

    # Price calc (only for non-owner)
    price_per_key = get_app_price(app_id)
    total_needed = price_per_key * count * devices

    if not is_owner_user:
        if price_per_key <= 0:
            bot.reply_to(message, "❌ Price not set for this app. Contact owner.")
            return
        balance = get_reseller_balance(uid, app_id)
        if balance < total_needed:
            bot.reply_to(message, f"""❌ **Insufficient Balance**

💰 Need: `{total_needed}` coins
💰 You have: `{balance}` coins

📊 Breakdown: {count} keys × {devices} devices × {price_per_key} coins""", parse_mode='Markdown')
            return

    # Generate keys
    keys_list = []
    for _ in range(count):
        k, exp, sc = generate_key(dur_sec, app_id, uid, slots, devices)
        keys_list.append((k, exp, sc))

    # Deduct balance
    if not is_owner_user and total_needed > 0:
        deduct_reseller_balance(uid, app_id, total_needed)

    app_nm = app_display(app_id)

    if is_bulk:
        header = f"⚡ **{count} KEYS GENERATED**\n\n📱 App: **{app_nm}**\n⏱ {dur_disp}\n👥 {devices} devices/key\n"
        if not is_owner_user:
            header += f"💰 Deducted: `{total_needed}` coins\n"
        header += "\n"
        body = "\n".join(f"`{k}`" for k, _, _ in keys_list)
        bot.reply_to(message, header + body, parse_mode='Markdown')
    else:
        k, exp, sc = keys_list[0]
        text = f"""⚡ **KEY GENERATED**

🔑 `{k}`
📱 App: **{app_nm}**
⏱ {dur_disp}
👥 {devices} devices
🎯 {sc} slots
📅 Expires: {exp}"""
        if not is_owner_user:
            text += f"\n💰 Deducted: `{total_needed}` coins"
        bot.reply_to(message, text, parse_mode='Markdown')


@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    uid = message.from_user.id
    cmd = message.text.split()

    if is_owner(uid):
        if len(cmd) < 3:
            bot.reply_to(message,
                         f"❌ `/genkey <time> <app_name> [devices]`\n\n**Apps:** {app_list_str()}\n**Time:** `5m` `30m` `1h` `2h` `1d` `30d`",
                         parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time. Use: `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`", parse_mode='Markdown')
            return
        app_id = resolve_app(cmd[2])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
        devices = 1
        if len(cmd) > 3:
            try:
                devices = int(cmd[3])
            except:
                bot.reply_to(message, "❌ Devices must be a number")
                return
            if devices < 1 or devices > MAX_KEY_DEVICES:
                bot.reply_to(message, f"❌ Devices 1-{MAX_KEY_DEVICES}")
                return
        _generate_and_reply(message, app_id, dur_sec, dur_disp, devices)
        return

    admin_app = get_admin_app(uid)
    reseller_app, _ = get_reseller_app(uid)
    my_app = admin_app or reseller_app

    if my_app:
        if len(cmd) < 2:
            bot.reply_to(message, "❌ `/genkey <time> [devices]`", parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time. Use: `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`", parse_mode='Markdown')
            return
        devices = 1
        if len(cmd) > 2:
            try:
                devices = int(cmd[2])
            except:
                bot.reply_to(message, "❌ Devices must be a number")
                return
            if devices < 1 or devices > MAX_KEY_DEVICES:
                bot.reply_to(message, f"❌ Devices 1-{MAX_KEY_DEVICES}")
                return
        _generate_and_reply(message, my_app, dur_sec, dur_disp, devices)
        return

    bot.reply_to(message, "❌ Not authorized")


@bot.message_handler(commands=['bulkkeys'])
def cmd_bulkkeys(message):
    uid = message.from_user.id
    cmd = message.text.split()

    if is_owner(uid):
        if len(cmd) < 4:
            bot.reply_to(message,
                         f"❌ `/bulkkeys <count> <time> <app_name>`\n\n**Apps:** {app_list_str()}",
                         parse_mode='Markdown')
            return
        try:
            count = int(cmd[1])
        except:
            bot.reply_to(message, "❌ Count must be a number")
            return
        if count < 1 or count > MAX_BULK:
            bot.reply_to(message, f"❌ Count 1-{MAX_BULK}")
            return
        dur_sec, dur_disp = parse_duration(cmd[2])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time", parse_mode='Markdown')
            return
        app_id = resolve_app(cmd[3])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
        _generate_and_reply(message, app_id, dur_sec, dur_disp, 1, count=count, is_bulk=True)
        return

    admin_app = get_admin_app(uid)
    reseller_app, _ = get_reseller_app(uid)
    my_app = admin_app or reseller_app

    if my_app:
        if len(cmd) < 3:
            bot.reply_to(message, "❌ `/bulkkeys <count> <time>`", parse_mode='Markdown')
            return
        try:
            count = int(cmd[1])
        except:
            bot.reply_to(message, "❌ Count must be a number")
            return
        if count < 1 or count > MAX_BULK:
            bot.reply_to(message, f"❌ Count 1-{MAX_BULK}")
            return
        dur_sec, dur_disp = parse_duration(cmd[2])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time", parse_mode='Markdown')
            return
        _generate_and_reply(message, my_app, dur_sec, dur_disp, 1, count=count, is_bulk=True)
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
        bot.reply_to(message, f"✅ **KEY RESET**\n\n🔑 `{key}`\n📱 {app_display(key_app)}\n\nDevice unlock, slots free.", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Reset failed")


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
        r += f"`{k[0]}`\n  {k[1]} | {k[2]} | {k[3]}dev\n\n"
    bot.reply_to(message, r, parse_mode='Markdown')


# ==================== RESELLER MGMT ====================
@bot.message_handler(commands=['addreseller'])
def cmd_add_reseller(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    if not admin_app and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    cmd = message.text.split()
    if len(cmd) != 3:
        bot.reply_to(message, "❌ `/addreseller <id> <coins>`")
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
    bot.reply_to(message, f"✅ **Reseller Added**\n\n👤 `{rid}`\n📱 {name}\n💰 `{coins}`", parse_mode='Markdown')


@bot.message_handler(commands=['removereseller'])
def cmd_remove_reseller(message):
    uid = message.from_user.id
    if not is_admin(uid) and not is_owner(uid):
        bot.reply_to(message, "❌ Admin only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/removereseller <id>`")
        return
    remove_reseller(cmd[1])
    bot.reply_to(message, f"✅ Reseller `{cmd[1]}` removed", parse_mode='Markdown')


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
        bot.reply_to(message, "❌ `/addbalance <id> <amount>`")
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
        bot.reply_to(message, "❌ `/removebalance <id> <amount>`")
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
    price = get_app_price(reseller_app)
    bot.reply_to(message, f"💰 **Balance**\n\n📱 {name}\n💰 `{bal}` coins\n🏷 Price/key: `{price}`", parse_mode='Markdown')


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
    for pkg in APP_IDS:
        init_app_slots(pkg)

    print("=" * 60)
    print("⚡ LIGHTNING VPS")
    print("=" * 60)
    print(f"👑 Owner: {OWNER_ID}")
    print(f"📱 Apps: {app_list_str()}")
    print(f"🌐 Port: {API_PORT}")
    print(f"✅ Bypass: {BYPASS_PACKAGES}")
    for pkg in APP_IDS:
        print(f"   • {app_display(pkg):10s} → pool {get_app_pool_size(pkg)} | price {get_app_price(pkg)} | prefix {app_prefix(pkg)}")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    threading.Thread(target=auto_release_loop, daemon=True).start()
    threading.Thread(target=run_key_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
