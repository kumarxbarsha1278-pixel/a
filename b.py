#!/usr/bin/env python3
"""
⚡ LIGHTNING VPS — GLOBAL 4 SLOTS + Hourly Pricing + Strict Key Isolation

✅ 6 apps (RageBite, Lightning, XSilent, VIP Mods, Ninja, XSilent2)
✅ PER-APP slots — har app ka apna slot pool
✅ Dynamic slots — owner/admin bot se set kar sakte hain
✅ Key prefix = app name (XSilent2 bhi XSILENT- use karta hai)
✅ STRICT cross-app isolation (app_id based)
✅ RageBite bypass ONLY
✅ Multi-device keys (1-20 devices)
✅ Time-based keys (5m, 2h, 1d, etc.)
✅ Hourly pricing: coins = rate × hours × devices × count
✅ Default rate: 10 coins/hour/key
✅ Expiry = hard delete
✅ Owner = all apps owner | Admin = own app mini-owner (unlimited)
✅ Admin-add permission (owner controlled)
✅ Balance system sirf reseller ke liye
✅ Maintenance mode — APK me bhi dikhta hai
✅ Full database stats for owner
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
KEY_BOT_TOKEN = "8823908635:AAGZN9cD6feaNAuhF1WwepeZ7Vg1IIQSKGg"
DD_BOT_TOKEN  = "8650600804:AAFw-AuiLMtbUUHIbqwdPzVeOG8s11yfdA8"
OWNER_ID = 6321758394

API_SECRET = "RAGEBITE_SECRET_2026_CHANGE_ME"
API_PORT = 5000

# ✅ Per-app defaults — runtime pe DB se slots aayenge
_APPS = {
    "com.ragebite.app":   {"name": "RageBite",  "prefix": "RAGEBITE", "default_rate": 10, "default_slots": 4},
    "com.ragebite.one":   {"name": "Lightning", "prefix": "LIGHTNING","default_rate": 10, "default_slots": 4},
    "com.ragebite.two":   {"name": "XSilent",   "prefix": "XSILENT",  "default_rate": 10, "default_slots": 4},
    "com.ragebite.three": {"name": "VIP Mods",  "prefix": "VIPMODS",  "default_rate": 10, "default_slots": 4},
    "com.ragebite.four":  {"name": "Ninja",     "prefix": "NINJA",    "default_rate": 10, "default_slots": 4},
    "com.ragebite.five":  {"name": "XSilent2",  "prefix": "XSILENT",  "default_rate": 10, "default_slots": 3},
}

_NAME_TO_PKG = {v["name"].lower().replace(" ", ""): k for k, v in _APPS.items()}
_PKG_TO_NAME = {k: v["name"] for k, v in _APPS.items()}
_PKG_TO_PREFIX = {k: v["prefix"] for k, v in _APPS.items()}
_PKG_TO_SLOTS_DEFAULT = {k: v.get("default_slots", 4) for k, v in _APPS.items()}

APP_IDS = list(_APPS.keys())

# ✅ RageBite bypass ONLY — uski key har app me chalti hai
BYPASS_PACKAGES = {"com.ragebite.app"}

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


def get_app_rate(pkg):
    """Hourly rate (coins per key per hour)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (f"rate:{pkg}",))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return int(row[0])
        except:
            pass
    return _APPS.get(pkg, {}).get("default_rate", 10)


def set_app_rate(pkg, coins):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
              (f"rate:{pkg}", str(coins)))
    conn.commit()
    conn.close()


def get_app_slots(pkg):
    """Us app ke slots (DB se, fallback default)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (f"slots:{pkg}",))
    row = c.fetchone()
    conn.close()
    if row:
        try:
            return max(1, int(row[0]))
        except:
            pass
    return _PKG_TO_SLOTS_DEFAULT.get(pkg, 4)


def set_app_slots(pkg, count):
    """Us app ke slots set karo + slots table update karo."""
    count = max(1, min(50, int(count)))   # 1-50 ke beech
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
              (f"slots:{pkg}", str(count)))

    # Extra slots delete karo (agar count kam hua)
    c.execute("DELETE FROM slots WHERE app_id=? AND slot_id > ?", (pkg, count))
    # Naye slots add karo
    c.execute("SELECT slot_id FROM slots WHERE app_id=? ORDER BY slot_id", (pkg,))
    existing = [r[0] for r in c.fetchall()]
    for i in range(1, count + 1):
        if i not in existing:
            c.execute("INSERT OR IGNORE INTO slots (app_id, slot_id, is_active) VALUES (?, ?, 0)",
                      (pkg, i))
    conn.commit()
    conn.close()
    return count


def app_slots(pkg):
    """Backward compat — get_app_slots ka alias."""
    return get_app_slots(pkg)


def calc_price(duration_sec, hourly_rate, devices=1, count=1):
    """Total coins = rate × hours × devices × count. Minimum 1."""
    hours = duration_sec / 3600.0
    total = hourly_rate * hours * devices * count
    return max(1, int(round(total)))


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

    # ✅ PER-APP slots table
    c.execute('''CREATE TABLE IF NOT EXISTS slots (
        app_id TEXT,
        slot_id INTEGER,
        key TEXT,
        device_id TEXT,
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

    # ✅ Admin permission table
    c.execute('''CREATE TABLE IF NOT EXISTS admin_perms (
        telegram_id TEXT PRIMARY KEY,
        can_add_admin INTEGER DEFAULT 0
    )''')

    # Migrations
    try:
        c.execute("ALTER TABLE keys ADD COLUMN max_devices INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass

    # ✅ Migration: agar purana global slots table hai to migrate
    c.execute("PRAGMA table_info(slots)")
    cols = [r[1] for r in c.fetchall()]
    if 'app_id' not in cols:
        print("🔄 Migrating slots table to per-app schema...")
        c.execute("DROP TABLE IF EXISTS slots")
        c.execute('''CREATE TABLE slots (
            app_id TEXT,
            slot_id INTEGER,
            key TEXT,
            device_id TEXT,
            ip TEXT,
            port TEXT,
            time_sec INTEGER,
            start_time TEXT,
            end_time TEXT,
            is_active INTEGER DEFAULT 0,
            PRIMARY KEY (app_id, slot_id)
        )''')

    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off')")

    # Default rates
    for pkg in APP_IDS:
        default_rate = _APPS[pkg].get("default_rate", 10)
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                  (f"rate:{pkg}", str(default_rate)))

    # Default slots
    for pkg in APP_IDS:
        default_slots = _PKG_TO_SLOTS_DEFAULT.get(pkg, 4)
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                  (f"slots:{pkg}", str(default_slots)))

    # Auto-cleanup: NULL app_id ya prefix mismatch wali keys
    c.execute("SELECT key, app_id FROM keys")
    bad_keys = []
    for k, aid in c.fetchall():
        if not aid:
            bad_keys.append(k)
            continue
        expected_prefix = _PKG_TO_PREFIX.get(aid)
        if expected_prefix and not k.startswith(expected_prefix + "-"):
            bad_keys.append(k)
    if bad_keys:
        for k in bad_keys:
            c.execute("DELETE FROM keys WHERE key=?", (k,))
            c.execute('DELETE FROM key_devices WHERE key=?', (k,))
        print(f"🗑️ Cleaned {len(bad_keys)} invalid keys")

    conn.commit()
    conn.close()

    # Per-app slots ensure karo (DB ready hone ke baad)
    for pkg in APP_IDS:
        total = get_app_slots(pkg)
        set_app_slots(pkg, total)
        print(f"✅ {app_display(pkg)}: {total} slots")

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
    c.execute('DELETE FROM admin_perms WHERE telegram_id=?', (str(telegram_id),))
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


# ==================== ADMIN PERMISSIONS ====================
def can_admin_add_admin(telegram_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT can_add_admin FROM admin_perms WHERE telegram_id=?", (str(telegram_id),))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == 1)


def set_admin_add_permission(telegram_id, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO admin_perms (telegram_id, can_add_admin) VALUES (?, ?)",
              (str(telegram_id), 1 if value else 0))
    conn.commit()
    conn.close()


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
        slot_count = get_app_slots(app_id)
    slot_count = max(1, min(get_app_slots(app_id), int(slot_count)))
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
            c.execute('''UPDATE slots SET key=NULL, device_id=NULL, ip=NULL, port=NULL,
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
    """4-layer strict verification + multi-device support."""
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('SELECT expiry, status, device_id, app_id, max_devices FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            if not row:
                return None, "NOT_FOUND", False
            expiry_str, status, existing_device, key_app_id, max_devices = row

            if not key_app_id:
                return None, "INVALID_KEY_APP", False

            if key_app_id != app_id:
                return None, "WRONG_APP", False

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
                c.execute('''UPDATE slots SET key=NULL, device_id=NULL, ip=NULL, port=NULL,
                             time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                             WHERE key=?''', (key,))
                conn.commit()
                return None, "EXPIRED", False

            max_devices = max_devices or 1

            c.execute('SELECT COUNT(*) FROM key_devices WHERE key=?', (key,))
            dev_count = c.fetchone()[0]

            if dev_count == 0:
                c.execute('INSERT OR IGNORE INTO key_devices (key, device_id, bound_at) VALUES (?, ?, ?)',
                          (key, device_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                c.execute('UPDATE keys SET device_id = ? WHERE key = ?', (device_id, key))
                conn.commit()
                return int(expiry.timestamp() * 1000), "VALID", True

            c.execute('SELECT 1 FROM key_devices WHERE key=? AND device_id=?', (key, device_id))
            if c.fetchone():
                return int(expiry.timestamp() * 1000), "VALID", True

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
            c.execute('''UPDATE slots SET key=NULL, device_id=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                         WHERE key=?''', (key,))
            conn.commit()
            return True
        finally:
            conn.close()


# ==================== PER-APP SLOTS ====================
def allot_slot(device_id, key, ip, port, time_sec, app_id):
    """Per-app slot pool me se slot allot karo."""
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()

            c.execute('SELECT slot_count FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            total_app_slots = get_app_slots(app_id)
            key_slot_count = row[0] if row else total_app_slots
            key_slot_count = min(key_slot_count, total_app_slots)

            c.execute('SELECT COUNT(*) FROM slots WHERE app_id=? AND key=? AND is_active=1', (app_id, key))
            used_by_key = c.fetchone()[0]
            if used_by_key >= key_slot_count:
                return None, "KEY_SLOTS_FULL"

            c.execute('SELECT slot_id FROM slots WHERE app_id=? AND device_id=? AND is_active=1',
                      (app_id, device_id))
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
            c.execute('''UPDATE slots SET key=?, device_id=?, ip=?, port=?, 
                         time_sec=?, start_time=?, end_time=?, is_active=1 
                         WHERE app_id=? AND slot_id=?''',
                      (key, device_id, ip, port, time_sec,
                       start.strftime('%Y-%m-%d %H:%M:%S'),
                       end.strftime('%Y-%m-%d %H:%M:%S'),
                       app_id, slot_id))
            conn.commit()
            return slot_id, "OK"
        finally:
            conn.close()


def release_slot(slot_id, app_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('''UPDATE slots SET key=NULL, device_id=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0 
                         WHERE app_id=? AND slot_id=?''', (app_id, slot_id))
            conn.commit()
        finally:
            conn.close()


def get_all_slots(app_id=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        if app_id:
            c.execute('SELECT slot_id, key, device_id, ip, port, time_sec, start_time, end_time, is_active '
                      'FROM slots WHERE app_id=? ORDER BY slot_id', (app_id,))
        else:
            c.execute('SELECT app_id, slot_id, key, device_id, ip, port, time_sec, start_time, end_time, is_active '
                      'FROM slots ORDER BY app_id, slot_id')
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


# ==================== DATABASE STATS ====================
def get_db_stats():
    conn = get_conn()
    c = conn.cursor()
    stats = {}
    for pkg in APP_IDS:
        name = app_display(pkg)

        c.execute("SELECT COUNT(*) FROM keys WHERE app_id=?", (pkg,))
        keys_total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM keys WHERE app_id=? AND status='ACTIVE'", (pkg,))
        keys_active = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM admins WHERE app_id=?", (pkg,))
        admins = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM resellers WHERE app_id=?", (pkg,))
        resellers = c.fetchone()[0]

        total_slots = get_app_slots(pkg)
        c.execute("SELECT COUNT(*) FROM slots WHERE app_id=? AND is_active=1", (pkg,))
        busy_slots = c.fetchone()[0]

        rate = get_app_rate(pkg)

        stats[pkg] = {
            "name": name,
            "keys_total": keys_total,
            "keys_active": keys_active,
            "admins": admins,
            "resellers": resellers,
            "slots_total": total_slots,
            "slots_busy": busy_slots,
            "rate": rate,
        }
    conn.close()
    return stats


def get_all_keys_owner(app_id=None, generated_by=None):
    conn = get_conn()
    c = conn.cursor()
    query = '''SELECT key, app_id, generated_by, status, expiry, max_devices, created_at
               FROM keys WHERE 1=1'''
    params = []
    if app_id:
        query += ' AND app_id=?'
        params.append(app_id)
    if generated_by:
        query += ' AND generated_by=?'
        params.append(str(generated_by))
    query += ' ORDER BY created_at DESC LIMIT 100'
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return rows


def get_all_admins_grouped():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''SELECT a.telegram_id, a.app_id, 
                        COALESCE(p.can_add_admin, 0)
                 FROM admins a
                 LEFT JOIN admin_perms p ON a.telegram_id = p.telegram_id
                 ORDER BY a.app_id, a.telegram_id''')
    rows = c.fetchall()
    conn.close()
    return rows


def get_all_resellers_grouped():
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT telegram_id, app_id, balance FROM resellers ORDER BY app_id, telegram_id')
    rows = c.fetchall()
    conn.close()
    return rows


# ==================== FLASK API ====================
app = Flask(__name__)
CORS(app)


def check_auth():
    return request.headers.get('X-API-KEY') == API_SECRET


def resolve_request_app(key, client_pkg):
    """STRICT cross-app isolation."""
    if not key:
        return None
    info = get_key_info(key)
    if not info or not info[0]:
        return None

    key_app_id = info[0]

    # RageBite bypass
    if key_app_id in BYPASS_PACKAGES:
        return "com.ragebite.app"

    client_resolved = resolve_app(client_pkg) if client_pkg else None

    if not client_resolved:
        return None
    if client_resolved != key_app_id:
        return None
    return key_app_id


def build_slots_response(app_id=None):
    """Per-app slots response.

    - app_id diya → us app ke slots
    - app_id None → global 4 slots (purane APKs ke liye)
    """
    if app_id is not None:
        rows = get_all_slots(app_id)
        total = app_slots(app_id)
        slots = []
        for r in rows:
            slot_id, key, device_id, ip, port, time_sec, start_time, end_time, is_active = r
            if is_active:
                try:
                    rem = int((datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds())
                except:
                    rem = 0
                slots.append({"slot": slot_id, "status": "BUSY", "remaining": max(0, rem)})
            else:
                slots.append({"slot": slot_id, "status": "FREE", "remaining": 0})
        active = sum(1 for s in slots if s["status"] == "BUSY")
        return {"slots": slots, "active": active, "max": total}

    # ✅ Global 4 (purane APKs ke liye)
    total_global = 4
    all_rows = get_all_slots(None)
    busy_count = sum(1 for r in all_rows if r[9] == 1)

    slots = []
    for i in range(1, total_global + 1):
        if i <= busy_count:
            slots.append({"slot": i, "status": "BUSY", "remaining": 0})
        else:
            slots.append({"slot": i, "status": "FREE", "remaining": 0})

    return {"slots": slots, "active": min(busy_count, total_global), "max": total_global}


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({"status": "OK"})


@app.route('/api/verify', methods=['POST'])
def api_verify():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    # ✅ MAINTENANCE CHECK
    if get_maintenance() == "on":
        return jsonify({"status": "INVALID", "reason": "MAINTENANCE"})

    data = request.json or {}
    key = data.get('key', '').upper().strip()
    device_id = data.get('device_id', '').strip()
    client_pkg = data.get('package', APP_IDS[0])

    if not device_id:
        return jsonify({"status": "INVALID", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "INVALID", "reason": "NoKey"})

    pkg = resolve_request_app(key, client_pkg)
    if pkg is None:
        return jsonify({"status": "INVALID", "reason": "WRONG_APP"})
    if pkg not in APP_IDS:
        return jsonify({"status": "INVALID", "reason": "UnknownApp"})

    expiry, status, _ = verify_key_with_device(key, device_id, pkg)
    if status == "VALID":
        return jsonify({"status": "VALID", "expiry": expiry})
    return jsonify({"status": "INVALID", "reason": status})


@app.route('/api/slots', methods=['GET', 'POST'])
def api_slots():
    # ✅ MAINTENANCE CHECK
    if get_maintenance() == "on":
        return jsonify({"maintenance": True, "status": "MAINTENANCE"})

    if request.method == 'POST':
        data = request.json or {}
        client_pkg = data.get('package', '').strip()
        app_id = resolve_app(client_pkg)
        if not app_id:
            return jsonify(build_slots_response(None))
        return jsonify(build_slots_response(app_id))

    pkg_q = request.args.get('package', '').strip()
    if pkg_q:
        app_id = resolve_app(pkg_q)
        if app_id:
            return jsonify(build_slots_response(app_id))
    return jsonify(build_slots_response(None))


@app.route('/api/slots/status', methods=['GET', 'POST'])
def api_slots_status():
    return api_slots()


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
    if pkg is None:
        return jsonify({"status": "ERROR", "reason": "WRONG_APP"})
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
    if slot_status == "APP_POOL_FULL":
        return jsonify({"status": "ERROR", "reason": "AllSlotsFull"})
    if slot_id is None:
        return jsonify({"status": "ERROR", "reason": "AllSlotsFull"})

    app_name = app_display(pkg)

    details = (
        f"⚡ ATTACK REQUEST\n\n"
        f"🔑 Key: {key}\n"
        f"📱 App: {app_name}\n"
        f"🎮 Client package: {client_pkg}\n"
        f"🎯 Target: {ip}:{port}\n"
        f"⏱ Duration: {time_sec}s\n"
        f"📌 Slot: {slot_id}/{app_slots(pkg)}\n"
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
        "end_time": end.strftime('%H:%M:%S'),
        "total_slots": app_slots(pkg)
    })


def auto_release_loop():
    while True:
        try:
            for app_id, slot_id in get_expired_slots():
                release_slot(slot_id, app_id)
                print(f"✅ Released slot #{slot_id} for {app_display(app_id)}")
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

**👑 ADMIN MGMT:**
`/addadmn <id> <app>`
`/removeadmin <id>`
`/adminlist` — short
`/adminlist2` — full with perms
`/allowadminadd <admin_id>`
`/revokeadminadd <admin_id>`

**🔑 KEY MGMT:**
`/genkey <time> <app> [devices]`
`/bulkkeys <count> <time> <app>`
`/delkey <key>`
`/resetkey <key>`
`/listkeys [app]`
`/allkeys [app]`
`/adminkeys <admin_id>`
`/resellerkeys <reseller_id>`

**📊 SLOTS:**
`/setslots <app> <count>`
`/slotinfo`

**💰 RATE:**
`/setrate <app> <coins_per_hour>`

**🛒 RESELLERS:**
`/addbalance <rid> <amt> <app>`
`/removebalance <rid> <amt> <app>`

**📈 STATS:**
`/dbstats`

**🔧 MISC:**
`/maintenance on|off`

📱 **Apps:** {app_list_str()}

**Time:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
**Devices:** 1-20""", parse_mode='Markdown')
        return

    admin_app = get_admin_app(uid)
    if admin_app:
        name = app_display(admin_app)
        rate = get_app_rate(admin_app)
        slots = get_app_slots(admin_app)
        can_add = can_admin_add_admin(uid)
        add_line = "\n`/addadmn <id> <app>` ✅ permitted" if can_add else ""

        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** ⚡ ADMIN

📱 **Your App:** {name}
💰 **Keys:** unlimited
🏷 **Rate:** {rate} coins/hour/key
📊 **Slots:** {slots}

**⚡ Commands:**
`/genkey <time> [devices]`
`/bulkkeys <count> <time>`
`/setrate <coins_per_hour>`
`/setslots <count>`
`/delkey <key>`
`/resetkey <key>`
`/listkeys`
`/addreseller <id> <coins>`
`/removereseller <id>`
`/addbalance <rid> <amt>`
`/removebalance <rid> <amt>`
`/resellerlist`{add_line}

**Time:** `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
**Devices:** 1-20""", parse_mode='Markdown')
        return

    reseller_app, bal = get_reseller_app(uid)
    if reseller_app:
        name = app_display(reseller_app)
        rate = get_app_rate(reseller_app)
        bot.reply_to(message, f"""⚡ **LIGHTNING BOT** 🛒 RESELLER

📱 **Your App:** {name}
💰 **Balance:** {bal} coins
🏷 **Rate:** {rate} coins/hour/key

**🛒 Commands:**
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


# ==================== OWNER: ADMIN MGMT ====================
@bot.message_handler(commands=['addadmn', 'addadmin'])
def cmd_add_admin(message):
    uid = message.from_user.id
    is_owner_user = is_owner(uid)

    if not is_owner_user:
        if not is_admin(uid):
            bot.reply_to(message, "❌ Not authorized")
            return
        if not can_admin_add_admin(uid):
            bot.reply_to(message, "❌ You don't have permission to add admins.\nContact owner.")
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

    if not is_owner_user:
        my_app = get_admin_app(uid)
        if app_id != my_app:
            bot.reply_to(message, f"❌ You can only add admins for: **{app_display(my_app)}**", parse_mode='Markdown')
            return

    add_admin(telegram_id, app_id)
    name = app_display(app_id)
    bot.reply_to(message, f"✅ **Admin Added**\n\n👤 `{telegram_id}`\n📱 **{name}**", parse_mode='Markdown')
    try:
        bot.send_message(telegram_id, f"⚡ You are now ADMIN!\n📱 App: {name}\n💰 Keys: unlimited", parse_mode='Markdown')
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


@bot.message_handler(commands=['adminlist2'])
def cmd_adminlist2(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    rows = get_all_admins_grouped()
    if not rows:
        bot.reply_to(message, "ℹ️ No admins")
        return
    grouped = {}
    for tid, aid, can_add in rows:
        grouped.setdefault(aid, []).append((tid, can_add))
    r = "⚡ **ALL ADMINS**\n\n"
    for aid, admins in grouped.items():
        r += f"📱 **{app_display(aid)}**\n"
        for tid, can_add in admins:
            perm = " ✅" if can_add else ""
            r += f"  👤 `{tid}`{perm}\n"
        r += "\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['allowadminadd'])
def cmd_allow_admin_add(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/allowadminadd <admin_id>`")
        return
    admin_id = cmd[1]
    admin_app = get_admin_app(admin_id)
    if not admin_app:
        bot.reply_to(message, "❌ Ye admin nahi hai")
        return
    set_admin_add_permission(admin_id, True)
    name = app_display(admin_app)
    bot.reply_to(message, f"✅ `{admin_id}` ({name}) can now add admins.", parse_mode='Markdown')
    try:
        bot.send_message(admin_id,
                         f"✅ You can now add admins for **{name}**.\nUse `/addadmn <id> {name}`",
                         parse_mode='Markdown')
    except:
        pass


@bot.message_handler(commands=['revokeadminadd'])
def cmd_revoke_admin_add(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/revokeadminadd <admin_id>`")
        return
    set_admin_add_permission(cmd[1], False)
    bot.reply_to(message, f"✅ Permission revoked for `{cmd[1]}`", parse_mode='Markdown')


# ==================== SETRATE / SETSLOTS / SLOTINFO ====================
@bot.message_handler(commands=['setrate', 'setprice'])
def cmd_setrate(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    is_owner_user = is_owner(uid)

    if not is_owner_user and not admin_app:
        bot.reply_to(message, "❌ Owner or Admin only")
        return

    cmd = message.text.split()

    if is_owner_user:
        if len(cmd) != 3:
            bot.reply_to(message, f"❌ `/setrate <app_name> <coins_per_hour>`\n\n**Apps:** {app_list_str()}", parse_mode='Markdown')
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
    else:
        if len(cmd) != 2:
            bot.reply_to(message, "❌ `/setrate <coins_per_hour>`")
            return
        app_id = admin_app
        try:
            coins = int(cmd[1])
        except:
            bot.reply_to(message, "❌ Coins must be a number")
            return

    if coins < 1:
        bot.reply_to(message, "❌ Rate >= 1")
        return

    set_app_rate(app_id, coins)
    bot.reply_to(message, f"✅ **{app_display(app_id)}** rate: **{coins}** coins/hour/key", parse_mode='Markdown')


@bot.message_handler(commands=['setslots'])
def cmd_setslots(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    is_owner_user = is_owner(uid)

    if not is_owner_user and not admin_app:
        bot.reply_to(message, "❌ Owner or Admin only")
        return

    cmd = message.text.split()

    if is_owner_user:
        if len(cmd) != 3:
            bot.reply_to(message, f"❌ `/setslots <app_name> <count>`\n\n**Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        app_id = resolve_app(cmd[1])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
        try:
            count = int(cmd[2])
        except:
            bot.reply_to(message, "❌ Count must be a number")
            return
    else:
        if len(cmd) != 2:
            bot.reply_to(message, "❌ `/setslots <count>`")
            return
        app_id = admin_app
        try:
            count = int(cmd[1])
        except:
            bot.reply_to(message, "❌ Count must be a number")
            return

    if count < 1 or count > 50:
        bot.reply_to(message, "❌ Slots 1-50")
        return

    new_count = set_app_slots(app_id, count)
    name = app_display(app_id)
    bot.reply_to(message, f"✅ **{name}** slots set to **{new_count}**", parse_mode='Markdown')

    # Agar owner ne kiya, to us app ke admin ko notify karo
    if is_owner_user:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT telegram_id FROM admins WHERE app_id=?", (app_id,))
        for (tid,) in c.fetchall():
            if str(tid) != str(uid):
                try:
                    bot.send_message(tid, f"⚡ **{name}** slots updated to **{new_count}** by owner.", parse_mode='Markdown')
                except:
                    pass
        conn.close()


@bot.message_handler(commands=['slotinfo'])
def cmd_slotinfo(message):
    uid = message.from_user.id
    is_owner_user = is_owner(uid)
    admin_app = get_admin_app(uid)

    if not is_owner_user and not admin_app:
        bot.reply_to(message, "❌ Owner or Admin only")
        return

    if is_owner_user:
        r = "⚡ **PER-APP SLOTS**\n\n"
        for pkg in APP_IDS:
            rows = get_all_slots(pkg)
            total = app_slots(pkg)
            busy = sum(1 for x in rows if x[8] == 1)
            rate = get_app_rate(pkg)
            r += f"📱 **{app_display(pkg)}**\n"
            r += f"   📊 Slots: {busy}/{total} busy\n"
            r += f"   💰 Rate: {rate} coins/hr\n\n"
        bot.reply_to(message, r, parse_mode='Markdown')
    else:
        rows = get_all_slots(admin_app)
        total = app_slots(admin_app)
        busy = sum(1 for x in rows if x[8] == 1)
        rate = get_app_rate(admin_app)
        name = app_display(admin_app)
        r = f"📱 **{name}**\n\n"
        r += f"📊 Slots: {busy}/{total} busy\n"
        r += f"💰 Rate: {rate} coins/hr\n"
        bot.reply_to(message, r, parse_mode='Markdown')


# ==================== GENKEY / BULKKEYS ====================
def _generate_and_reply(message, app_id, dur_sec, dur_disp, devices, slots=None, count=1, is_bulk=False):
    uid = message.from_user.id
    is_owner_user = is_owner(uid)
    is_admin_user = is_admin(uid)

    unlimited = is_owner_user or is_admin_user

    hourly_rate = get_app_rate(app_id)
    total_needed = calc_price(dur_sec, hourly_rate, devices, count)
    hours = dur_sec / 3600.0

    if not unlimited:
        if hourly_rate <= 0:
            bot.reply_to(message, "❌ Rate not set for this app. Contact admin/owner.")
            return
        balance = get_reseller_balance(uid, app_id)
        if balance < total_needed:
            bot.reply_to(message, f"""❌ **Insufficient Balance**

💰 Need: `{total_needed}` coins
💰 You have: `{balance}` coins

📊 Breakdown: {hourly_rate} coins/hr × {hours:.2f} hr × {devices} devices × {count} keys""", parse_mode='Markdown')
            return

    keys_list = []
    for _ in range(count):
        k, exp, sc = generate_key(dur_sec, app_id, uid, slots, devices)
        keys_list.append((k, exp, sc))

    if not unlimited and total_needed > 0:
        deduct_reseller_balance(uid, app_id, total_needed)

    app_nm = app_display(app_id)

    if is_bulk:
        header = f"⚡ **{count} KEYS GENERATED**\n\n📱 App: **{app_nm}**\n⏱ {dur_disp}\n👥 {devices} devices/key\n"
        if not unlimited:
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
📅 Expires: {exp}"""
        if not unlimited:
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


# ==================== DELKEY / RESETKEY / LISTKEYS ====================
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


# ==================== OWNER: ALL KEYS VIEWS ====================
@bot.message_handler(commands=['allkeys'])
def cmd_allkeys(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    app_filter = None
    if len(cmd) > 1:
        app_filter = resolve_app(cmd[1])
        if not app_filter:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return

    rows = get_all_keys_owner(app_filter)
    if not rows:
        bot.reply_to(message, "ℹ️ No keys")
        return

    grouped = {}
    for k, aid, gen, status, exp, maxdev, created in rows:
        grouped.setdefault(aid, []).append((k, gen, status, exp, maxdev))

    r = "⚡ **ALL KEYS**\n\n"
    for aid, keys in grouped.items():
        r += f"📱 **{app_display(aid)}** ({len(keys)})\n"
        for k, gen, status, exp, maxdev in keys[:3]:
            r += f"  `{k}`\n    by `{gen}` | {status} | {maxdev}dev\n"
        if len(keys) > 3:
            r += f"  ... +{len(keys)-3} more\n"
        r += "\n"

    if len(r) > 4000:
        r = r[:4000] + "\n\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['adminkeys'])
def cmd_adminkeys(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/adminkeys <admin_id>`")
        return
    admin_id = cmd[1]
    rows = get_all_keys_owner(generated_by=admin_id)
    if not rows:
        bot.reply_to(message, f"ℹ️ No keys by `{admin_id}`", parse_mode='Markdown')
        return
    r = f"⚡ **KEYS by {admin_id}**\n\n"
    for k, aid, gen, status, exp, maxdev, created in rows[:20]:
        r += f"`{k}`\n  📱 {app_display(aid)} | {status} | {maxdev}dev\n  📅 {exp}\n\n"
    if len(r) > 4000:
        r = r[:4000] + "\n\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['resellerkeys'])
def cmd_resellerkeys(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, "❌ `/resellerkeys <reseller_id>`")
        return
    rid = cmd[1]
    rows = get_all_keys_owner(generated_by=rid)
    if not rows:
        bot.reply_to(message, f"ℹ️ No keys by `{rid}`", parse_mode='Markdown')
        return
    r = f"⚡ **KEYS by RESELLER {rid}**\n\n"
    for k, aid, gen, status, exp, maxdev, created in rows[:20]:
        r += f"`{k}`\n  📱 {app_display(aid)} | {status} | {maxdev}dev\n  📅 {exp}\n\n"
    if len(r) > 4000:
        r = r[:4000] + "\n\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['dbstats'])
def cmd_dbstats(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return

    stats = get_db_stats()
    r = "⚡ **DATABASE STATS**\n\n"

    total_keys = 0
    total_active = 0
    total_admins = 0
    total_resellers = 0
    total_slots = 0
    total_busy = 0

    for pkg, s in stats.items():
        r += f"📱 **{s['name']}**\n"
        r += f"  🔑 Keys: {s['keys_active']}/{s['keys_total']}\n"
        r += f"  👤 Admins: {s['admins']}\n"
        r += f"  🛒 Resellers: {s['resellers']}\n"
        r += f"  📊 Slots: {s['slots_busy']}/{s['slots_total']}\n"
        r += f"  💰 Rate: {s['rate']}/hr\n\n"

        total_keys += s['keys_total']
        total_active += s['keys_active']
        total_admins += s['admins']
        total_resellers += s['resellers']
        total_slots += s['slots_total']
        total_busy += s['slots_busy']

    r += "━━━━━━━━━━━━━━━\n"
    r += f"**TOTAL**\n"
    r += f"  🔑 Keys: {total_active}/{total_keys}\n"
    r += f"  👤 Admins: {total_admins}\n"
    r += f"  🛒 Resellers: {total_resellers}\n"
    r += f"  📊 Slots: {total_busy}/{total_slots}\n"

    if len(r) > 4000:
        r = r[:4000] + "\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


# ==================== RESELLER MGMT ====================
@bot.message_handler(commands=['addreseller'])
def cmd_add_reseller(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    if not admin_app and not is_owner(uid):
        bot.reply_to(message, "❌ Admin or Owner only")
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
        bot.reply_to(message, "❌ Admin or Owner only")
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
        bot.reply_to(message, "❌ Admin or Owner only")
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


# ==================== BALANCE MGMT ====================
@bot.message_handler(commands=['addbalance'])
def cmd_add_balance(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    is_owner_user = is_owner(uid)

    if not admin_app and not is_owner_user:
        bot.reply_to(message, "❌ Admin or Owner only")
        return

    cmd = message.text.split()

    if is_owner_user:
        if len(cmd) != 4:
            bot.reply_to(message, f"❌ `/addbalance <reseller_id> <amount> <app_name>`\n\n**Apps:** {app_list_str()}",
                         parse_mode='Markdown')
            return
        try:
            rid = cmd[1]
            amount = int(cmd[2])
        except:
            bot.reply_to(message, "❌ Invalid id or amount")
            return
        app_id = resolve_app(cmd[3])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
    else:
        if len(cmd) != 3:
            bot.reply_to(message, "❌ `/addbalance <reseller_id> <amount>`")
            return
        try:
            rid = cmd[1]
            amount = int(cmd[2])
        except:
            bot.reply_to(message, "❌ Invalid id or amount")
            return
        app_id = admin_app

    if amount <= 0:
        bot.reply_to(message, "❌ Amount > 0")
        return

    new_bal = add_reseller_balance(rid, app_id, amount)
    name = app_display(app_id)
    bot.reply_to(message, f"✅ Added `{amount}` to `{rid}`\n📱 {name}\n💰 New: `{new_bal}`", parse_mode='Markdown')


@bot.message_handler(commands=['removebalance'])
def cmd_remove_balance(message):
    uid = message.from_user.id
    admin_app = get_admin_app(uid)
    is_owner_user = is_owner(uid)

    if not admin_app and not is_owner_user:
        bot.reply_to(message, "❌ Admin or Owner only")
        return

    cmd = message.text.split()

    if is_owner_user:
        if len(cmd) != 4:
            bot.reply_to(message, f"❌ `/removebalance <reseller_id> <amount> <app_name>`\n\n**Apps:** {app_list_str()}",
                         parse_mode='Markdown')
            return
        try:
            rid = cmd[1]
            amount = int(cmd[2])
        except:
            bot.reply_to(message, "❌ Invalid id or amount")
            return
        app_id = resolve_app(cmd[3])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
    else:
        if len(cmd) != 3:
            bot.reply_to(message, "❌ `/removebalance <reseller_id> <amount>`")
            return
        try:
            rid = cmd[1]
            amount = int(cmd[2])
        except:
            bot.reply_to(message, "❌ Invalid id or amount")
            return
        app_id = admin_app

    if amount <= 0:
        bot.reply_to(message, "❌ Amount > 0")
        return

    ok, new_bal = deduct_reseller_balance(rid, app_id, amount)
    if not ok:
        bot.reply_to(message, f"❌ Insufficient: `{new_bal}`", parse_mode='Markdown')
        return
    name = app_display(app_id)
    bot.reply_to(message, f"✅ Removed `{amount}` from `{rid}`\n📱 {name}\n💰 New: `{new_bal}`", parse_mode='Markdown')


@bot.message_handler(commands=['balance'])
def cmd_balance(message):
    uid = message.from_user.id
    reseller_app, bal = get_reseller_app(uid)
    if not reseller_app:
        bot.reply_to(message, "❌ Reseller only")
        return
    name = app_display(reseller_app)
    rate = get_app_rate(reseller_app)
    bot.reply_to(message, f"💰 **Balance**\n\n📱 {name}\n💰 `{bal}` coins\n🏷 Rate: `{rate}` coins/hour/key", parse_mode='Markdown')


# ==================== MAINTENANCE ====================
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
    print(f"🌐 Port: {API_PORT}")
    print(f"✅ Bypass: {BYPASS_PACKAGES}")
    for pkg in APP_IDS:
        print(f"   • {app_display(pkg):12s} → rate {get_app_rate(pkg)}/hr | "
              f"slots {get_app_slots(pkg)} | prefix {app_prefix(pkg)}")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    threading.Thread(target=auto_release_loop, daemon=True).start()
    threading.Thread(target=run_key_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
