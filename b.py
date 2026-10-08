#!/usr/bin/env python3
"""
⚡ LIGHTNING VPS — PREMIUM DYNAMIC EDITION (FINAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Per-app slots — STRICT ISOLATION (23 kabhi nahi)
✅ Cross-app key block — 3 layer security
✅ DD bot: 2 alag messages (info + /bgmi command)
✅ Dynamic apps
✅ Decimal rates
✅ Maintenance freeze (keys + slots)
✅ Multi-device keys (1-20)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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

# ═══════════════════════ CONFIG ═══════════════════════
KEY_BOT_TOKEN = "8823908635:AAGZN9cD6feaNAuhF1WwepeZ7Vg1IIQSKGg"
DD_BOT_TOKEN  = "8650600804:AAFw-AuiLMtbUUHIbqwdPzVeOG8s11yfdA8"
OWNER_ID = 6321758394

API_SECRET = "RAGEBITE_SECRET_2026_CHANGE_ME"
API_PORT = 5000

BYPASS_PACKAGES = set()

_DEFAULT_APPS = {
    "com.ragebite.app":   {"name": "RageBite",  "prefix": "RAGEBITE", "default_rate": 10, "default_slots": 4},
    "com.ragebite.one":   {"name": "Lightning", "prefix": "LIGHTNING","default_rate": 10, "default_slots": 4},
    "com.ragebite.two":   {"name": "XSilent",   "prefix": "XSILENT",  "default_rate": 10, "default_slots": 4},
    "com.ragebite.three": {"name": "VIP Mods",  "prefix": "VIPMODS",  "default_rate": 10, "default_slots": 4},
    "com.ragebite.four":  {"name": "Ninja",     "prefix": "NINJA",    "default_rate": 10, "default_slots": 4},
    "com.ragebite.five":  {"name": "XSilent2",  "prefix": "XSILENT2", "default_rate": 10, "default_slots": 3},
}

MAX_KEY_DEVICES = 20
MAX_BULK = 100
MAX_APPS = 100
MIN_ATTACK_TIME = 10
MAX_ATTACK_TIME = 300

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lightning.db')

STATUS_ACTIVE = "ACTIVE"
STATUS_DELETED = "DELETED"
STATUS_DISABLED = "DISABLED"

rate_limit_store = {}
db_write_lock = threading.RLock()
_cache_lock = threading.RLock()

apihelper.CONNECT_TIMEOUT = 10
apihelper.READ_TIMEOUT = 10

_apps_cache = {}
_name_to_pkg_cache = {}


def get_conn():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def reload_apps_cache():
    global _apps_cache, _name_to_pkg_cache
    with _cache_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT package, name, prefix, default_rate, default_slots FROM apps ORDER BY name")
            rows = c.fetchall()
            new_apps, new_names = {}, {}
            for pkg, name, prefix, rate, slots in rows:
                new_apps[pkg] = {
                    "name": name,
                    "prefix": prefix,
                    "default_rate": float(rate),
                    "default_slots": int(slots),
                }
                new_names[name.lower().replace(" ", "")] = pkg
            _apps_cache = new_apps
            _name_to_pkg_cache = new_names
        finally:
            conn.close()


# ═══════════════════════ HELPERS ═══════════════════════
def APP_IDS():
    with _cache_lock:
        return list(_apps_cache.keys())


def resolve_app(name_or_pkg):
    """Robust resolver with aliases (.apk/.app, case, com. prefix)."""
    if not name_or_pkg:
        return None
    s = str(name_or_pkg).strip().lower()
    if not s:
        return None

    with _cache_lock:
        if s in _apps_cache:
            return s

        key = s.replace(" ", "").replace("_", "").replace("-", "")
        if key in _name_to_pkg_cache:
            return _name_to_pkg_cache[key]

        candidates = [s]
        if s.endswith('.apk'):
            candidates.append(s[:-4] + '.app')
            candidates.append(s[:-4])
        elif s.endswith('.app'):
            candidates.append(s[:-4] + '.apk')
            candidates.append(s[:-4])
        else:
            candidates.append(s + '.app')
            candidates.append(s + '.apk')

        extra = []
        for c in candidates:
            if c.startswith('com.'):
                extra.append(c[4:])
            else:
                extra.append('com.' + c)
        candidates.extend(extra)

        for c in candidates:
            if c in _apps_cache:
                return c

        return None


def app_display(pkg):
    with _cache_lock:
        return _apps_cache.get(pkg, {}).get("name", "?")


def app_prefix(pkg):
    with _cache_lock:
        return _apps_cache.get(pkg, {}).get("prefix", "KEY")


def _app_default_rate(pkg):
    with _cache_lock:
        return _apps_cache.get(pkg, {}).get("default_rate", 10.0)


def _app_default_slots(pkg):
    with _cache_lock:
        return _apps_cache.get(pkg, {}).get("default_slots", 4)


def fmt_rate(rate):
    if rate is None:
        return "0"
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return "0"
    if rate == int(rate):
        return str(int(rate))
    return f"{rate:g}"


def get_app_rate(pkg):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (f"rate:{pkg}",))
        row = c.fetchone()
    finally:
        conn.close()
    if row:
        try:
            return float(row[0])
        except (TypeError, ValueError):
            pass
    return _app_default_rate(pkg)


def set_app_rate(pkg, coins):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                  (f"rate:{pkg}", str(coins)))
        conn.commit()
    finally:
        conn.close()


def get_app_slots(pkg):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (f"slots:{pkg}",))
        row = c.fetchone()
    finally:
        conn.close()
    if row:
        try:
            return max(1, int(row[0]))
        except (TypeError, ValueError):
            pass
    return _app_default_slots(pkg)


def set_app_slots(pkg, count):
    count = max(1, min(50, int(count)))
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                  (f"slots:{pkg}", str(count)))
        c.execute('''UPDATE slots SET key=NULL, device_id=NULL, ip=NULL, port=NULL,
                     time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                     WHERE app_id=? AND slot_id > ? AND is_active=1''', (pkg, count))
        c.execute("DELETE FROM slots WHERE app_id=? AND slot_id > ?", (pkg, count))
        c.execute("SELECT slot_id FROM slots WHERE app_id=? ORDER BY slot_id", (pkg,))
        existing = {r[0] for r in c.fetchall()}
        for i in range(1, count + 1):
            if i not in existing:
                c.execute("INSERT OR IGNORE INTO slots (app_id, slot_id, is_active) VALUES (?, ?, 0)",
                          (pkg, i))
        conn.commit()
    finally:
        conn.close()
    return count


def app_slots(pkg):
    return get_app_slots(pkg)


def calc_price(duration_sec, hourly_rate, devices=1, count=1):
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
    if sec < 60 or sec > 3650 * 86400:
        return None, None
    return sec, disp


def fmt_remaining(seconds):
    if seconds <= 0:
        return "Expired"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s and not d: parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"


def progress_bar(busy, total, width=10):
    if total <= 0:
        return "░" * width
    filled = int(round((busy / total) * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ═══════════════════════ APP MANAGEMENT ═══════════════════════
def validate_package(pkg):
    if not pkg or len(pkg) < 3 or len(pkg) > 100:
        return False
    return bool(re.match(r'^[a-z][a-z0-9._]*$', pkg))


def validate_prefix(prefix):
    if not prefix or len(prefix) < 2 or len(prefix) > 16:
        return False
    return bool(re.match(r'^[A-Z][A-Z0-9]*$', prefix))


def add_app_to_db(pkg, name, prefix, rate=10.0, slots=4):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO apps (package, name, prefix, default_rate, default_slots, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (pkg, name, prefix, float(rate), int(slots), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                  (f"rate:{pkg}", str(rate)))
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                  (f"slots:{pkg}", str(slots)))
        for i in range(1, int(slots) + 1):
            c.execute("INSERT OR IGNORE INTO slots (app_id, slot_id, is_active) VALUES (?, ?, 0)",
                      (pkg, i))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    reload_apps_cache()


def remove_app_from_db(pkg):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM key_devices WHERE key IN (SELECT key FROM keys WHERE app_id=?)", (pkg,))
        c.execute("DELETE FROM keys WHERE app_id=?", (pkg,))
        c.execute("DELETE FROM slots WHERE app_id=?", (pkg,))
        c.execute("DELETE FROM admins WHERE app_id=?", (pkg,))
        c.execute("DELETE FROM resellers WHERE app_id=?", (pkg,))
        c.execute("DELETE FROM settings WHERE key=? OR key=?", (f"rate:{pkg}", f"slots:{pkg}"))
        c.execute("DELETE FROM apps WHERE package=?", (pkg,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    reload_apps_cache()


def list_all_apps():
    with _cache_lock:
        return [(pkg, dict(info)) for pkg, info in _apps_cache.items()]


def app_list_str():
    return " · ".join(info["name"] for _, info in list_all_apps())


# ═══════════════════════ DATABASE ═══════════════════════
def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS apps (
        package TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        prefix TEXT NOT NULL,
        default_rate REAL DEFAULT 10,
        default_slots INTEGER DEFAULT 4,
        created_at TEXT
    )''')

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

    c.execute('''CREATE TABLE IF NOT EXISTS admin_perms (
        telegram_id TEXT PRIMARY KEY,
        can_add_admin INTEGER DEFAULT 0
    )''')

    c.execute("PRAGMA table_info(keys)")
    key_cols = [r[1] for r in c.fetchall()]
    if 'max_devices' not in key_cols:
        try:
            c.execute("ALTER TABLE keys ADD COLUMN max_devices INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass

    c.execute("PRAGMA table_info(slots)")
    cols = [r[1] for r in c.fetchall()]
    if 'app_id' not in cols:
        print("🔄 Migrating slots table...")
        c.execute("DROP TABLE IF EXISTS slots")
        c.execute('''CREATE TABLE slots (
            app_id TEXT, slot_id INTEGER, key TEXT, device_id TEXT,
            ip TEXT, port TEXT, time_sec INTEGER, start_time TEXT,
            end_time TEXT, is_active INTEGER DEFAULT 0,
            PRIMARY KEY (app_id, slot_id)
        )''')

    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance', 'off')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_started_at', '')")

    c.execute("SELECT COUNT(*) FROM apps")
    if c.fetchone()[0] == 0:
        print("🌱 Seeding default apps...")
        for pkg, info in _DEFAULT_APPS.items():
            c.execute("INSERT OR IGNORE INTO apps (package, name, prefix, default_rate, default_slots, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                      (pkg, info["name"], info["prefix"],
                       float(info.get("default_rate", 10)),
                       int(info.get("default_slots", 4)),
                       datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    c.execute("SELECT package, default_rate, default_slots FROM apps")
    for pkg, rate, slots in c.fetchall():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                  (f"rate:{pkg}", str(rate)))
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                  (f"slots:{pkg}", str(slots)))

    c.execute("SELECT key, app_id FROM keys")
    bad_keys = []
    for k, aid in c.fetchall():
        if not aid:
            bad_keys.append(k)
            continue
        c.execute("SELECT prefix FROM apps WHERE package=?", (aid,))
        row = c.fetchone()
        if not row:
            bad_keys.append(k)
            continue
        expected_prefix = row[0]
        if expected_prefix and not k.startswith(expected_prefix + "-"):
            bad_keys.append(k)
    if bad_keys:
        for k in bad_keys:
            c.execute("DELETE FROM key_devices WHERE key=?", (k,))
            c.execute("DELETE FROM keys WHERE key=?", (k,))
        print(f"🗑️ Cleaned {len(bad_keys)} invalid keys")

    conn.commit()
    conn.close()

    reload_apps_cache()

    for pkg in APP_IDS():
        total = get_app_slots(pkg)
        set_app_slots(pkg, total)

    print(f"✅ Database ready: {DB_NAME}")


def get_setting(key, default=""):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = c.fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    finally:
        conn.close()


def get_maintenance():
    return get_setting('maintenance', 'off')


def set_maintenance(value):
    old = get_maintenance()
    if value == "on" and old != "on":
        set_setting('maintenance_started_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        set_setting('maintenance', 'on')
        return None
    elif value == "off" and old == "on":
        started_str = get_setting('maintenance_started_at', '')
        frozen_seconds = 0
        if started_str:
            try:
                started = datetime.strptime(started_str, '%Y-%m-%d %H:%M:%S')
                frozen_seconds = int((datetime.now() - started).total_seconds())
            except (ValueError, TypeError):
                frozen_seconds = 0

        if frozen_seconds > 0:
            extend_all_keys(frozen_seconds)
            extend_active_slots(frozen_seconds)

        set_setting('maintenance_started_at', '')
        set_setting('maintenance', 'off')
        return frozen_seconds
    else:
        set_setting('maintenance', value)
        return None


def extend_active_slots(seconds):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute("SELECT app_id, slot_id, end_time FROM slots WHERE is_active=1")
            rows = c.fetchall()
            for app_id, slot_id, end_str in rows:
                try:
                    end = datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S')
                    new_end = end + timedelta(seconds=seconds)
                    c.execute("UPDATE slots SET end_time=? WHERE app_id=? AND slot_id=?",
                              (new_end.strftime('%Y-%m-%d %H:%M:%S'), app_id, slot_id))
                except (ValueError, TypeError):
                    pass
            conn.commit()
        finally:
            conn.close()


def extend_all_keys(seconds, app_id=None, generated_by=None):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            query = "SELECT key, expiry FROM keys WHERE status=? "
            params = [STATUS_ACTIVE]
            if app_id:
                query += "AND app_id=? "
                params.append(app_id)
            if generated_by:
                query += "AND generated_by=? "
                params.append(str(generated_by))

            c.execute(query, params)
            rows = c.fetchall()
            count = 0
            for key, expiry_str in rows:
                try:
                    expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
                    if expiry < datetime.now():
                        continue
                    new_expiry = expiry + timedelta(seconds=seconds)
                    c.execute("UPDATE keys SET expiry=? WHERE key=?",
                              (new_expiry.strftime('%Y-%m-%d %H:%M:%S'), key))
                    count += 1
                except (ValueError, TypeError):
                    pass
            conn.commit()
            return count
        finally:
            conn.close()


# ═══════════════════════ ADMIN ═══════════════════════
def add_admin(telegram_id, app_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO admins (telegram_id, app_id, added_at) VALUES (?, ?, ?)',
                  (str(telegram_id), app_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    finally:
        conn.close()


def remove_admin(telegram_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM admin_perms WHERE telegram_id=?', (str(telegram_id),))
        c.execute('DELETE FROM admins WHERE telegram_id=?', (str(telegram_id),))
        conn.commit()
    finally:
        conn.close()


def get_admin_app(telegram_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT app_id FROM admins WHERE telegram_id=? LIMIT 1', (str(telegram_id),))
        row = c.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def is_admin(telegram_id):
    return get_admin_app(telegram_id) is not None


def list_admins():
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT telegram_id, app_id FROM admins ORDER BY app_id')
        return c.fetchall()
    finally:
        conn.close()


def can_admin_add_admin(telegram_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT can_add_admin FROM admin_perms WHERE telegram_id=?", (str(telegram_id),))
        row = c.fetchone()
        return bool(row and row[0] == 1)
    finally:
        conn.close()


def set_admin_add_permission(telegram_id, value):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO admin_perms (telegram_id, can_add_admin) VALUES (?, ?)",
                  (str(telegram_id), 1 if value else 0))
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════ RESELLER ═══════════════════════
def add_reseller(telegram_id, app_id, balance=0):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO resellers (telegram_id, app_id, balance, added_at) VALUES (?, ?, ?, ?)',
                  (str(telegram_id), app_id, balance, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    finally:
        conn.close()


def remove_reseller(telegram_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM resellers WHERE telegram_id=?', (str(telegram_id),))
        conn.commit()
    finally:
        conn.close()


def get_reseller_app(telegram_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT app_id, balance FROM resellers WHERE telegram_id=? LIMIT 1', (str(telegram_id),))
        row = c.fetchone()
        return row if row else (None, 0)
    finally:
        conn.close()


def is_reseller(telegram_id):
    return get_reseller_app(telegram_id)[0] is not None


def get_reseller_balance(telegram_id, app_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT balance FROM resellers WHERE telegram_id=? AND app_id=?', (str(telegram_id), app_id))
        row = c.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def set_reseller_balance(telegram_id, app_id, amount):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('UPDATE resellers SET balance=? WHERE telegram_id=? AND app_id=?',
                  (amount, str(telegram_id), app_id))
        conn.commit()
    finally:
        conn.close()


def add_reseller_balance(telegram_id, app_id, amount):
    current = get_reseller_balance(telegram_id, app_id)
    new = current + amount
    set_reseller_balance(telegram_id, app_id, new)
    return new


def deduct_reseller_balance(telegram_id, app_id, amount):
    with db_write_lock:
        current = get_reseller_balance(telegram_id, app_id)
        if current < amount:
            return False, current
        new = current - amount
        set_reseller_balance(telegram_id, app_id, new)
        return True, new


def list_resellers(app_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT telegram_id, balance FROM resellers WHERE app_id=? ORDER BY telegram_id', (app_id,))
        return c.fetchall()
    finally:
        conn.close()


# ═══════════════════════ KEYS ═══════════════════════
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
    expiry = (datetime.now() + timedelta(seconds=duration_sec)).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('''INSERT INTO keys (key, device_id, expiry, status, slot_count, max_devices, app_id, generated_by, created_at)
                     VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)''',
                  (key, expiry, STATUS_ACTIVE, slot_count, max_devices, app_id, str(generated_by),
                   datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    finally:
        conn.close()
    return key, expiry, slot_count


def delete_key_hard(key):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()
            c.execute('DELETE FROM key_devices WHERE key=?', (key,))
            c.execute('DELETE FROM keys WHERE key=?', (key,))
            c.execute('''UPDATE slots SET key=NULL, device_id=NULL, ip=NULL, port=NULL,
                         time_sec=NULL, start_time=NULL, end_time=NULL, is_active=0
                         WHERE key=?''', (key,))
            conn.commit()
        finally:
            conn.close()


def delete_key_soft(key):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('UPDATE keys SET status = ? WHERE key = ?', (STATUS_DELETED, key))
        conn.commit()
    finally:
        conn.close()


def list_keys(app_id=None, generated_by=None, limit=20, include_deleted=False):
    conn = get_conn()
    try:
        c = conn.cursor()
        query = 'SELECT key, status, expiry, max_devices FROM keys WHERE 1=1'
        params = []
        if not include_deleted:
            query += " AND status != ?"
            params.append(STATUS_DELETED)
        if app_id:
            query += ' AND app_id=?'
            params.append(app_id)
        if generated_by:
            query += ' AND generated_by=?'
            params.append(str(generated_by))
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        c.execute(query, params)
        return c.fetchall()
    finally:
        conn.close()


def get_key_info(key):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT app_id, generated_by, expiry, status FROM keys WHERE key=?', (key,))
        return c.fetchone()
    finally:
        conn.close()


def verify_key_with_device(key, device_id, app_id):
    """STRICT: Cross-app block via app_id + prefix check."""
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

            expected_prefix = app_prefix(app_id)
            if not expected_prefix or expected_prefix == "KEY":
                return None, "UNKNOWN_APP", False
            if not key.startswith(expected_prefix + "-"):
                return None, "PREFIX_MISMATCH", False

            if status == STATUS_DELETED:
                return None, "DELETED", False
            if status == STATUS_DISABLED:
                return None, "DISABLED", False

            now = datetime.now()
            try:
                expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                return None, "INVALID_EXPIRY", False

            if expiry < now:
                c.execute('DELETE FROM key_devices WHERE key=?', (key,))
                c.execute('DELETE FROM keys WHERE key=?', (key,))
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
                          (key, device_id, now.strftime('%Y-%m-%d %H:%M:%S')))
                c.execute('UPDATE keys SET device_id = ? WHERE key = ?', (device_id, key))
                conn.commit()
                return int(expiry.timestamp() * 1000), "VALID", True

            c.execute('SELECT 1 FROM key_devices WHERE key=? AND device_id=?', (key, device_id))
            if c.fetchone():
                return int(expiry.timestamp() * 1000), "VALID", True

            if dev_count >= max_devices:
                return None, "DEVICE_LIMIT_REACHED", False

            c.execute('INSERT OR IGNORE INTO key_devices (key, device_id, bound_at) VALUES (?, ?, ?)',
                      (key, device_id, now.strftime('%Y-%m-%d %H:%M:%S')))
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
            try:
                expiry = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                return False
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


# ═══════════════════════ SLOTS ═══════════════════════
def allot_slot(device_id, key, ip, port, time_sec, app_id):
    with db_write_lock:
        conn = get_conn()
        try:
            c = conn.cursor()

            c.execute('SELECT slot_count FROM keys WHERE key = ?', (key,))
            row = c.fetchone()
            total_app_slots = get_app_slots(app_id)
            key_slot_count = row[0] if (row and row[0]) else total_app_slots
            key_slot_count = max(1, min(key_slot_count, total_app_slots))

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
            now = datetime.now()
            end = now + timedelta(seconds=time_sec)
            c.execute('''UPDATE slots SET key=?, device_id=?, ip=?, port=?, 
                         time_sec=?, start_time=?, end_time=?, is_active=1 
                         WHERE app_id=? AND slot_id=?''',
                      (key, device_id, ip, port, time_sec,
                       now.strftime('%Y-%m-%d %H:%M:%S'),
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
    if get_maintenance() == "on":
        return []
    conn = get_conn()
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute('SELECT app_id, slot_id FROM slots WHERE is_active=1 AND end_time <= ?', (now_str,))
        return c.fetchall()
    finally:
        conn.close()


def get_expired_keys():
    if get_maintenance() == "on":
        return []
    conn = get_conn()
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        c.execute("SELECT key FROM keys WHERE expiry <= ? AND status=?", (now_str, STATUS_ACTIVE))
        return [r[0] for r in c.fetchall()]
    finally:
        conn.close()


def check_rate_limit(identifier, max_requests=15, window=60):
    if not identifier:
        return True
    now_ts = time.time()
    if identifier not in rate_limit_store:
        rate_limit_store[identifier] = []
    rate_limit_store[identifier] = [t for t in rate_limit_store[identifier] if now_ts - t < window]
    if len(rate_limit_store[identifier]) >= max_requests:
        return False
    rate_limit_store[identifier].append(now_ts)
    return True


def notify_owner_dd(text):
    """Send message to DD bot with debug logging."""
    url = f"https://api.telegram.org/bot{DD_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": OWNER_ID, "text": text}, timeout=10)
        if r.ok:
            data = r.json()
            if data.get("ok"):
                print(f"📤 DD sent | msg_id={data['result']['message_id']}")
            else:
                print(f"❌ DD rejected: {data}")
        else:
            print(f"❌ DD HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"❌ DD error: {type(e).__name__}: {e}")


# ═══════════════════════ STATS ═══════════════════════
def get_db_stats():
    conn = get_conn()
    try:
        c = conn.cursor()
        stats = {}
        for pkg in APP_IDS():
            name = app_display(pkg)

            c.execute("SELECT COUNT(*), SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) FROM keys WHERE app_id=?", (pkg,))
            keys_total, keys_active = c.fetchone()
            keys_active = keys_active or 0
            keys_total = keys_total or 0

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
        return stats
    finally:
        conn.close()


def get_all_keys_owner(app_id=None, generated_by=None):
    conn = get_conn()
    try:
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
        return c.fetchall()
    finally:
        conn.close()


def get_all_admins_grouped():
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('''SELECT a.telegram_id, a.app_id, 
                            COALESCE(p.can_add_admin, 0)
                     FROM admins a
                     LEFT JOIN admin_perms p ON a.telegram_id = p.telegram_id
                     ORDER BY a.app_id, a.telegram_id''')
        return c.fetchall()
    finally:
        conn.close()


# ═══════════════════════ FLASK API ═══════════════════════
app = Flask(__name__)
CORS(app)


def check_auth():
    return request.headers.get('X-API-KEY') == API_SECRET


def resolve_request_app(key, client_pkg):
    """STRICT: key's DB app + client package + prefix — all must match."""
    if not key:
        return None, "NO_KEY"
    if not client_pkg:
        return None, "NO_PACKAGE"

    info = get_key_info(key)
    if not info or not info[0]:
        return None, "KEY_NOT_FOUND"

    key_app_id = info[0]
    client_resolved = resolve_app(client_pkg)

    if not client_resolved:
        return None, "UNKNOWN_PACKAGE"

    if client_resolved != key_app_id:
        return None, "WRONG_APP"

    expected_prefix = app_prefix(key_app_id)
    if not key.startswith(expected_prefix + "-"):
        return None, "PREFIX_MISMATCH"

    return key_app_id, "OK"


def build_slots_response(app_id):
    """STRICT: app_id MANDATORY. Only that app's slots."""
    if not app_id or app_id not in APP_IDS():
        return {
            "slots": [], "active": 0, "free": 0, "max": 0,
            "error": "UnknownPackage"
        }

    rows = get_all_slots(app_id)
    total = app_slots(app_id)

    slots = []
    for r in rows:
        slot_id, key, device_id, ip, port, time_sec, start_time, end_time, is_active = r
        if is_active and end_time:
            try:
                rem = int((datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
                           - datetime.now()).total_seconds())
            except (ValueError, TypeError):
                rem = 0
            slots.append({"slot": slot_id, "status": "BUSY", "remaining": max(0, rem)})
        else:
            slots.append({"slot": slot_id, "status": "FREE", "remaining": 0})

    active = sum(1 for s in slots if s["status"] == "BUSY")
    free = total - active

    return {
        "app": app_display(app_id),
        "package": app_id,
        "slots": slots,
        "active": active,
        "free": free,
        "max": total
    }


@app.route('/api/health', methods=['GET'])
def api_health():
    return jsonify({"status": "OK"})


@app.route('/api/verify', methods=['POST'])
def api_verify():
    if not check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    if get_maintenance() == "on":
        return jsonify({"status": "INVALID", "reason": "MAINTENANCE"})

    data = request.json or {}
    key = (data.get('key') or '').upper().strip()
    device_id = (data.get('device_id') or '').strip()
    client_pkg = (data.get('package') or '').strip()

    if not device_id:
        return jsonify({"status": "INVALID", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "INVALID", "reason": "NoKey"})
    if not client_pkg:
        return jsonify({"status": "INVALID", "reason": "NoPackage"})

    pkg, reason = resolve_request_app(key, client_pkg)
    if pkg is None:
        return jsonify({"status": "INVALID", "reason": reason})

    expiry, status, _ = verify_key_with_device(key, device_id, pkg)
    if status == "VALID":
        return jsonify({"status": "VALID", "expiry": expiry})
    return jsonify({"status": "INVALID", "reason": status})


@app.route('/api/slots', methods=['GET', 'POST'])
def api_slots():
    """STRICT: package MANDATORY. Only that app's slots."""
    if get_maintenance() == "on":
        return jsonify({"maintenance": True, "status": "MAINTENANCE"})

    if request.method == 'POST':
        data = request.json or {}
        client_pkg = (data.get('package') or '').strip()

        print(f"🔍 /api/slots POST | package='{client_pkg}'")

        if not client_pkg:
            return jsonify({
                "slots": [], "active": 0, "free": 0, "max": 0,
                "error": "PackageRequired"
            }), 400

        app_id = resolve_app(client_pkg)
        if not app_id:
            print(f"   ❌ Unknown package. Available: {APP_IDS()}")
            return jsonify({
                "slots": [], "active": 0, "free": 0, "max": 0,
                "error": "UnknownPackage"
            }), 404

        print(f"   ✅ Resolved → {app_id}")
        return jsonify(build_slots_response(app_id))

    pkg_q = (request.args.get('package') or '').strip()
    if not pkg_q:
        return jsonify({
            "slots": [], "active": 0, "free": 0, "max": 0,
            "error": "PackageRequired"
        }), 400

    app_id = resolve_app(pkg_q)
    if not app_id:
        return jsonify({
            "slots": [], "active": 0, "free": 0, "max": 0,
            "error": "UnknownPackage"
        }), 404

    return jsonify(build_slots_response(app_id))


@app.route('/api/slots/status', methods=['GET', 'POST'])
def api_slots_status():
    return api_slots()


@app.route('/api/dd', methods=['POST'])
def api_dd():
    if not check_auth():
        return jsonify({"status": "ERROR", "reason": "Unauthorized"}), 401
    if get_maintenance() == "on":
        return jsonify({"status": "ERROR", "reason": "MAINTENANCE"})

    client_ip = request.remote_addr or "unknown"
    if not check_rate_limit(client_ip):
        return jsonify({"status": "ERROR", "reason": "RateLimit"})

    data = request.json or {}
    device_id = (data.get('device_id') or '').strip()
    key = (data.get('key') or '').upper().strip()
    ip = (data.get('ip') or '').strip()
    port = str(data.get('port') or '').strip()
    try:
        time_sec = int(data.get('time', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "ERROR", "reason": "InvalidTime"})
    client_pkg = (data.get('package') or '').strip()

    if not device_id:
        return jsonify({"status": "ERROR", "reason": "NoDeviceID"})
    if not key:
        return jsonify({"status": "ERROR", "reason": "NoKey"})
    if not ip or not port:
        return jsonify({"status": "ERROR", "reason": "MissingIPPort"})
    if time_sec < MIN_ATTACK_TIME or time_sec > MAX_ATTACK_TIME:
        return jsonify({"status": "ERROR", "reason": "InvalidTime"})
    if not client_pkg:
        return jsonify({"status": "ERROR", "reason": "NoPackage"})

    pkg, reason = resolve_request_app(key, client_pkg)
    if pkg is None:
        return jsonify({"status": "ERROR", "reason": reason})

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
    total = app_slots(pkg)
    rows_now = get_all_slots(pkg)
    busy_now = sum(1 for r in rows_now if r[8] == 1)

    # Message 1: Attack Info
    details = (
        f"⚡ ATTACK REQUEST\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔑 Key: {key}\n"
        f"📱 App: {app_name}\n"
        f"🎯 Target: {ip}:{port}\n"
        f"⏱ Duration: {time_sec}s\n"
        f"📌 Slot: {slot_id}/{total}\n"
        f"👤 Device: {device_id[:16]}...\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    notify_owner_dd(details)

    # Message 2: DD Command (ALAG)
    notify_owner_dd(f"/bgmi {ip} {port} {time_sec} {app_name}")

    print(f"✅ Attack: {ip}:{port} | Slot {slot_id} | {app_name} | {key}")
    end = datetime.now() + timedelta(seconds=time_sec)
    return jsonify({
        "status": "SLOT_ALLOTTED",
        "slot": slot_id,
        "app": app_name,
        "package": pkg,
        "ip": ip,
        "port": port,
        "time": time_sec,
        "end_time": end.strftime('%H:%M:%S'),
        "total_slots": total,
        "active_slots": busy_now,
        "free_slots": total - busy_now
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


# ═══════════════════════ KEY BOT ═══════════════════════
bot = telebot.TeleBot(KEY_BOT_TOKEN)


def is_owner(uid):
    return str(uid) == str(OWNER_ID)


def get_role(uid):
    if is_owner(uid):
        return "owner"
    if is_admin(uid):
        return "admin"
    if is_reseller(uid):
        return "reseller"
    return "none"


@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    role = get_role(uid)

    if role == "owner":
        mnt = "🔴 ON" if get_maintenance() == 'on' else "🟢 OFF"
        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ⚡ LIGHTNING VPS · OWNER      ║
╚══════════════════════════════════╝

👑 **Welcome back, Boss!**
🌐 Maintenance: **{mnt}**

┌─ 🆕 APP MANAGEMENT ──────────────┐
│ `/addapp <package> <name> <prefix> [rate] [slots]`
│ `/delapp <package>`
│ `/apps`
└──────────────────────────────────┘

┌─ 👑 ADMIN MANAGEMENT ────────────┐
│ `/addadmn <id> <app>`
│ `/removeadmin <id>`
│ `/adminlist` · `/adminlist2`
│ `/allowadminadd <id>`
│ `/revokeadminadd <id>`
└──────────────────────────────────┘

┌─ 🔑 KEY MANAGEMENT ──────────────┐
│ `/genkey <time> <app> [dev]`
│ `/bulkkeys <count> <time> <app>`
│ `/delkey <key>` · `/resetkey <key>`
│ `/extendkeys <time> [app] [admin]`
│ `/listkeys [app]` · `/allkeys [app]`
└──────────────────────────────────┘

┌─ 📊 SLOTS & PRICING ─────────────┐
│ `/setslots <app> <count>`
│ `/setrate <app> <coins>`
│ `/slotinfo`
└──────────────────────────────────┘

┌─ 🛒 RESELLERS & SYSTEM ──────────┐
│ `/addreseller` · `/addbalance`
│ `/resellerlist` · `/dbstats`
│ `/maintenance on|off`
└──────────────────────────────────┘

📱 **Apps ({len(APP_IDS())}):** {app_list_str()}

⏱ `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
👥 Devices: 1–{MAX_KEY_DEVICES}""", parse_mode='Markdown')

    elif role == "admin":
        admin_app = get_admin_app(uid)
        name = app_display(admin_app)
        rate = fmt_rate(get_app_rate(admin_app))
        slots = get_app_slots(admin_app)
        can_add = can_admin_add_admin(uid)
        add_line = f"\n│ `/addadmn <id>` *(auto app)*\n" if can_add else ""

        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ⚡ LIGHTNING VPS · ADMIN      ║
╚══════════════════════════════════╝

📱 **App:** **{name}**
💰 **Rate:** `{rate}` coins/hour/key
📊 **Slots:** `{slots}`

┌─ ⚡ KEY GENERATION ──────────────┐
│ `/genkey <time> [devices]`
│ `/bulkkeys <count> <time>`
└──────────────────────────────────┘

┌─ 🛠 KEY MANAGEMENT ──────────────┐
│ `/delkey <key>` · `/resetkey <key>`
│ `/extendkeys <time> [admin_id]`
│ `/listkeys`
└──────────────────────────────────┘

┌─ 🛒 RESELLERS ───────────────────┐
│ `/addreseller <id> <coins>`
│ `/removereseller <id>`
│ `/addbalance <rid> <amt>`
│ `/removebalance <rid> <amt>`
│ `/resellerlist`{add_line}└──────────────────────────────────┘

┌─ 📊 INFO ────────────────────────┐
│ `/slotinfo`
└──────────────────────────────────┘

⏱ `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
👥 Devices: 1–{MAX_KEY_DEVICES}""", parse_mode='Markdown')

    elif role == "reseller":
        reseller_app, bal = get_reseller_app(uid)
        name = app_display(reseller_app)
        rate = fmt_rate(get_app_rate(reseller_app))
        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ⚡ LIGHTNING VPS · RESELLER   ║
╚══════════════════════════════════╝

📱 **App:** **{name}**
💰 **Balance:** `{bal}` coins
🏷 **Rate:** `{rate}` coins/hour/key

┌─ ⚡ KEY GENERATION ──────────────┐
│ `/genkey <time> [devices]`
│ `/bulkkeys <count> <time>`
└──────────────────────────────────┘

┌─ 🛠 KEY MANAGEMENT ──────────────┐
│ `/delkey <key>` · `/resetkey <key>`
│ `/keyslist`
└──────────────────────────────────┘

┌─ 💰 ACCOUNT ─────────────────────┐
│ `/balance`
└──────────────────────────────────┘

⏱ `5m` `30m` `1h` `2h` `12h` `1d` `7d` `30d`
👥 Devices: 1–{MAX_KEY_DEVICES}""", parse_mode='Markdown')

    else:
        bot.reply_to(message, """╔══════════════════════════════════╗
║   ❌  ACCESS DENIED              ║
╚══════════════════════════════════╝

Aap authorized nahi hain.

📩 Owner se contact karein access ke liye.""")


# ═══════════════════════ APP MANAGEMENT ═══════════════════════
@bot.message_handler(commands=['addapp'])
def cmd_addapp(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return

    cmd = message.text.split()
    if len(cmd) < 4:
        bot.reply_to(message, """╔══════════════════════════════════╗
║   🆕  ADD NEW APP                ║
╚══════════════════════════════════╝

**Usage:** `/addapp <package> <name> <prefix> [rate] [slots]`

**Example:**
`/addapp com.ragebite.six RageBiteSix RBSIX 10 4`

**Rules:**
• package → lowercase, dots ok
• name → Display name
• prefix → UPPERCASE, A-Z 0-9
• rate → coins/hour (default 10)
• slots → 1-50 (default 4)""", parse_mode='Markdown')
        return

    pkg = cmd[1].strip().lower()
    name = cmd[2].strip()
    prefix = cmd[3].strip().upper()

    if not validate_package(pkg):
        bot.reply_to(message, "❌ Invalid package. Example: `com.ragebite.six`", parse_mode='Markdown')
        return
    if len(name) < 2 or len(name) > 30:
        bot.reply_to(message, "❌ Name must be 2-30 chars")
        return
    if not validate_prefix(prefix):
        bot.reply_to(message, "❌ Invalid prefix. UPPERCASE A-Z 0-9 only.")
        return
    if pkg in APP_IDS():
        bot.reply_to(message, f"❌ Package `{pkg}` already exists", parse_mode='Markdown')
        return
    if resolve_app(name):
        bot.reply_to(message, f"❌ App name `{name}` already exists", parse_mode='Markdown')
        return

    with _cache_lock:
        for p, info in _apps_cache.items():
            if info["prefix"] == prefix:
                bot.reply_to(message, f"❌ Prefix `{prefix}` already used by **{info['name']}**", parse_mode='Markdown')
                return

    if len(APP_IDS()) >= MAX_APPS:
        bot.reply_to(message, f"❌ Max {MAX_APPS} apps allowed")
        return

    rate = 10.0
    slots = 4
    if len(cmd) > 4:
        try:
            rate = float(cmd[4])
            if rate < 1 or rate > 10000:
                bot.reply_to(message, "❌ Rate must be 1-10000")
                return
        except (TypeError, ValueError):
            bot.reply_to(message, "❌ Invalid rate")
            return
    if len(cmd) > 5:
        try:
            slots = int(cmd[5])
            if slots < 1 or slots > 50:
                bot.reply_to(message, "❌ Slots must be 1-50")
                return
        except (TypeError, ValueError):
            bot.reply_to(message, "❌ Invalid slots")
            return

    try:
        add_app_to_db(pkg, name, prefix, rate, slots)
    except Exception as e:
        bot.reply_to(message, f"❌ Failed: {e}")
        return

    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  APP ADDED SUCCESSFULLY     ║
╚══════════════════════════════════╝

📦 **Package:** `{pkg}`
📱 **Name:** **{name}**
🔤 **Prefix:** `{prefix}`
💰 **Rate:** `{fmt_rate(rate)}` coins/hr
📊 **Slots:** `{slots}`

✅ Key generation ready!
✅ Admins can be added
✅ Slots auto-created""", parse_mode='Markdown')


@bot.message_handler(commands=['delapp'])
def cmd_delapp(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return

    cmd = message.text.split()
    if len(cmd) != 2:
        bot.reply_to(message, f"❌ `/delapp <package_or_name>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
        return

    pkg = resolve_app(cmd[1])
    if not pkg:
        bot.reply_to(message, f"❌ App not found: `{cmd[1]}`", parse_mode='Markdown')
        return

    name = app_display(pkg)
    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("🗑️ YES, DELETE", callback_data=f"delapp_yes:{pkg}"),
        telebot.types.InlineKeyboardButton("❌ Cancel", callback_data="delapp_no")
    )
    bot.reply_to(message, f"""⚠️ **DELETE APP CONFIRMATION**
━━━━━━━━━━━━━━━━━━━━━━━━━━

📱 **{name}**
📦 `{pkg}`

**This will delete:**
• All keys
• All slots
• All admins
• All resellers
• All settings

**Yeh action undo nahi hoga!**

Confirm karo:""", reply_markup=markup, parse_mode='Markdown')


@bot.callback_query_handler(func=lambda call: call.data.startswith("delapp_"))
def cb_delapp(call):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Owner only")
        return

    if call.data == "delapp_no":
        bot.edit_message_text("❌ Cancelled", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "Cancelled")
        return

    pkg = call.data.split(":", 1)[1]
    name = app_display(pkg)
    try:
        remove_app_from_db(pkg)
        bot.edit_message_text(f"""╔══════════════════════════════════╗
║   🗑️  APP DELETED                ║
╚══════════════════════════════════╝

📱 **{name}**
📦 `{pkg}`

Sab data clean ho gaya.""", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        bot.answer_callback_query(call.id, "✅ Deleted")
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ {e}")


@bot.message_handler(commands=['apps'])
def cmd_apps(message):
    uid = message.from_user.id
    if not is_owner(uid) and not is_admin(uid):
        bot.reply_to(message, "❌ Owner or Admin only")
        return

    apps = list_all_apps()
    if not apps:
        bot.reply_to(message, "ℹ️ No apps configured")
        return

    r = "╔══════════════════════════════════╗\n║      📱  ALL APPS                ║\n╚══════════════════════════════════╝\n\n"
    for pkg, info in apps:
        rate = get_app_rate(pkg)
        slots = get_app_slots(pkg)
        r += f"📱 **{info['name']}**\n"
        r += f"   📦 `{pkg}`\n"
        r += f"   🔤 `{info['prefix']}`\n"
        r += f"   💰 `{fmt_rate(rate)}`/hr · 📊 `{slots}` slots\n\n"
    r += f"**Total:** {len(apps)} apps"
    if len(r) > 4000:
        r = r[:4000] + "\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


# ═══════════════════════ ADMIN MANAGEMENT ═══════════════════════
@bot.message_handler(commands=['addadmn', 'addadmin'])
def cmd_add_admin(message):
    uid = message.from_user.id
    is_owner_user = is_owner(uid)

    if not is_owner_user:
        if not is_admin(uid):
            bot.reply_to(message, "❌ Not authorized")
            return
        if not can_admin_add_admin(uid):
            bot.reply_to(message, "❌ Permission nahi hai. Owner se contact karein.")
            return

    cmd = message.text.split()

    if is_owner_user:
        if len(cmd) != 3:
            bot.reply_to(message, f"❌ `/addadmn <id> <app_name>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        telegram_id = cmd[1]
        app_id = resolve_app(cmd[2])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
    else:
        if len(cmd) != 2:
            my_app = get_admin_app(uid)
            bot.reply_to(message, f"❌ `/addadmn <telegram_id>`\n\nAuto add hoga: **{app_display(my_app)}**", parse_mode='Markdown')
            return
        telegram_id = cmd[1]
        app_id = get_admin_app(uid)

    add_admin(telegram_id, app_id)
    name = app_display(app_id)

    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  ADMIN ADDED                ║
╚══════════════════════════════════╝

👤 **ID:** `{telegram_id}`
📱 **App:** **{name}**
💰 **Rate:** `{fmt_rate(get_app_rate(app_id))}` coins/hr""", parse_mode='Markdown')

    try:
        bot.send_message(telegram_id, f"""╔══════════════════════════════════╗
║   ⚡  WELCOME ADMIN!            ║
╚══════════════════════════════════╝

🎉 Aapko **{name}** ka admin banaya gaya hai.

💰 **Rate:** `{fmt_rate(get_app_rate(app_id))}` coins/hour
📊 **Slots:** `{get_app_slots(app_id)}`

Use `/start` to see commands.""", parse_mode='Markdown')
    except Exception:
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
        bot.reply_to(message, "ℹ️ Koi admin nahi hai")
        return
    r = "╔══════════════════════════════════╗\n║       👑  ADMINS LIST           ║\n╚══════════════════════════════════╝\n\n"
    for tid, app_id in admins:
        r += f"👤 `{tid}`\n   └─ 📱 {app_display(app_id)}\n\n"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['adminlist2'])
def cmd_adminlist2(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    rows = get_all_admins_grouped()
    if not rows:
        bot.reply_to(message, "ℹ️ Koi admin nahi hai")
        return
    grouped = {}
    for tid, aid, can_add in rows:
        grouped.setdefault(aid, []).append((tid, can_add))
    r = "╔══════════════════════════════════╗\n║     📋  DETAILED ADMINS         ║\n╚══════════════════════════════════╝\n\n"
    for aid, admins in grouped.items():
        r += f"📱 **{app_display(aid)}** ({len(admins)})\n"
        for tid, can_add in admins:
            perm = " ✅" if can_add else ""
            r += f"   └─ 👤 `{tid}`{perm}\n"
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
    bot.reply_to(message, f"✅ `{admin_id}` can now add admins for **{name}**.", parse_mode='Markdown')
    try:
        bot.send_message(admin_id,
                         f"✅ **Permission Granted**\n\nAb aap apne app **{name}** me admin add kar sakte hain.\nUse: `/addadmn <id>`",
                         parse_mode='Markdown')
    except Exception:
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


# ═══════════════════════ SETRATE / SETSLOTS ═══════════════════════
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
            bot.reply_to(message, f"❌ `/setrate <app_name> <coins_per_hour>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        app_id = resolve_app(cmd[1])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
        try:
            coins = float(cmd[2])
        except (TypeError, ValueError):
            bot.reply_to(message, "❌ Invalid rate")
            return
    else:
        if len(cmd) != 2:
            bot.reply_to(message, "❌ `/setrate <coins_per_hour>`", parse_mode='Markdown')
            return
        app_id = admin_app
        try:
            coins = float(cmd[1])
        except (TypeError, ValueError):
            bot.reply_to(message, "❌ Invalid rate")
            return

    if coins < 1:
        bot.reply_to(message, "❌ Rate must be >= 1")
        return

    set_app_rate(app_id, coins)
    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  RATE UPDATED               ║
╚══════════════════════════════════╝

📱 **{app_display(app_id)}**
💰 **{fmt_rate(coins)}** coins/hour/key""", parse_mode='Markdown')


@bot.message_handler(commands=['setslots'])
def cmd_setslots(message):
    uid = message.from_user.id

    if not is_owner(uid):
        bot.reply_to(message, "❌ Owner only")
        return

    cmd = message.text.split()
    if len(cmd) != 3:
        bot.reply_to(message, f"❌ `/setslots <app_name> <count>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
        return
    app_id = resolve_app(cmd[1])
    if not app_id:
        bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
        return
    try:
        count = int(cmd[2])
    except (TypeError, ValueError):
        bot.reply_to(message, "❌ Count must be a number")
        return

    if count < 1 or count > 50:
        bot.reply_to(message, "❌ Slots must be 1-50")
        return

    new_count = set_app_slots(app_id, count)
    name = app_display(app_id)
    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  SLOTS UPDATED              ║
╚══════════════════════════════════╝

📱 **{name}**
📊 **{new_count}** slots""", parse_mode='Markdown')

    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute("SELECT telegram_id FROM admins WHERE app_id=?", (app_id,))
        admins = c.fetchall()
    finally:
        conn.close()
    for (tid,) in admins:
        if str(tid) != str(uid):
            try:
                bot.send_message(tid, f"⚡ **{name}** slots updated to **{new_count}** by owner.", parse_mode='Markdown')
            except Exception:
                pass


@bot.message_handler(commands=['slotinfo'])
def cmd_slotinfo(message):
    uid = message.from_user.id
    is_owner_user = is_owner(uid)
    admin_app = get_admin_app(uid)

    if not is_owner_user and not admin_app:
        bot.reply_to(message, "❌ Owner or Admin only")
        return

    if is_owner_user:
        r = "╔══════════════════════════════════╗\n║     📊  PER-APP SLOTS           ║\n╚══════════════════════════════════╝\n\n"
        for pkg in APP_IDS():
            rows = get_all_slots(pkg)
            total = app_slots(pkg)
            busy = sum(1 for x in rows if x[8] == 1)
            rate = get_app_rate(pkg)
            bar = progress_bar(busy, total)
            r += f"📱 **{app_display(pkg)}**\n"
            r += f"   `{bar}` {busy}/{total}\n"
            r += f"   💰 {fmt_rate(rate)} coins/hr\n\n"
        bot.reply_to(message, r, parse_mode='Markdown')
    else:
        rows = get_all_slots(admin_app)
        total = app_slots(admin_app)
        busy = sum(1 for x in rows if x[8] == 1)
        rate = get_app_rate(admin_app)
        name = app_display(admin_app)
        bar = progress_bar(busy, total)
        r = f"""╔══════════════════════════════════╗
║     📊  SLOT INFO                ║
╚══════════════════════════════════╝

📱 **{name}**
📊 `{bar}` {busy}/{total}
💰 **{fmt_rate(rate)}** coins/hr"""
        bot.reply_to(message, r, parse_mode='Markdown')


# ═══════════════════════ EXTEND KEYS ═══════════════════════
@bot.message_handler(commands=['extendkeys'])
def cmd_extendkeys(message):
    uid = message.from_user.id
    is_owner_user = is_owner(uid)
    admin_app = get_admin_app(uid)

    if not is_owner_user and not admin_app:
        bot.reply_to(message, "❌ Owner or Admin only")
        return

    cmd = message.text.split()

    if is_owner_user:
        if len(cmd) < 2:
            bot.reply_to(message, f"❌ `/extendkeys <time> [app_name] [admin_id]`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time format", parse_mode='Markdown')
            return

        app_id = None
        if len(cmd) > 2:
            app_id = resolve_app(cmd[2])
            if not app_id:
                bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
                return

        admin_filter = cmd[3] if len(cmd) > 3 else None
        count = extend_all_keys(dur_sec, app_id, admin_filter)

        scope = app_display(app_id) if app_id else "all apps"
        if admin_filter:
            scope += f" (admin `{admin_filter}`)"

        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  KEYS EXTENDED              ║
╚══════════════════════════════════╝

⏱ Added: **{dur_disp}**
📱 Scope: **{scope}**
🔑 Keys: **{count}**""", parse_mode='Markdown')

    else:
        if len(cmd) < 2:
            bot.reply_to(message, "❌ `/extendkeys <time> [admin_id]`", parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time format", parse_mode='Markdown')
            return

        admin_filter = cmd[2] if len(cmd) > 2 else None
        count = extend_all_keys(dur_sec, admin_app, admin_filter)
        name = app_display(admin_app)

        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  KEYS EXTENDED              ║
╚══════════════════════════════════╝

⏱ Added: **{dur_disp}**
📱 App: **{name}**
🔑 Keys: **{count}**""", parse_mode='Markdown')


# ═══════════════════════ GENKEY / BULKKEYS ═══════════════════════
def _generate_and_reply(message, app_id, dur_sec, dur_disp, devices, slots=None, count=1, is_bulk=False):
    uid = message.from_user.id
    unlimited = is_owner(uid) or is_admin(uid)

    hourly_rate = get_app_rate(app_id)
    total_needed = calc_price(dur_sec, hourly_rate, devices, count)
    hours = dur_sec / 3600.0

    if not unlimited:
        if hourly_rate <= 0:
            bot.reply_to(message, "❌ Rate set nahi hai. Admin se contact karein.")
            return
        balance = get_reseller_balance(uid, app_id)
        if balance < total_needed:
            bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ❌  INSUFFICIENT BALANCE      ║
╚══════════════════════════════════╝

💰 **Need:** `{total_needed}` coins
💰 **You have:** `{balance}` coins

📊 `{fmt_rate(hourly_rate)}` × `{hours:.2f}h` × `{devices}dev` × `{count}key`""", parse_mode='Markdown')
            return

    new_bal = None
    if not unlimited and total_needed > 0:
        ok, new_bal = deduct_reseller_balance(uid, app_id, total_needed)
        if not ok:
            bot.reply_to(message, f"❌ Deduction failed. Have: `{new_bal}`", parse_mode='Markdown')
            return

    keys_list = []
    try:
        for _ in range(count):
            k, exp, sc = generate_key(dur_sec, app_id, uid, slots, devices)
            keys_list.append((k, exp, sc))
    except Exception as e:
        if not unlimited and total_needed > 0 and new_bal is not None:
            add_reseller_balance(uid, app_id, total_needed)
        bot.reply_to(message, f"❌ Key generation failed: {e}")
        return

    app_nm = app_display(app_id)

    if is_bulk:
        header = f"╔══════════════════════════════════╗\n║   ⚡  {count} KEYS GENERATED        ║\n╚══════════════════════════════════╝\n\n"
        header += f"📱 **{app_nm}**\n"
        header += f"⏱ **{dur_disp}** · 👥 **{devices}** dev\n"
        if not unlimited:
            header += f"💰 Deducted: `{total_needed}` coins\n"
        header += "\n"
        body = "\n".join(f"`{k}`" for k, _, _ in keys_list)
        bot.reply_to(message, header + body, parse_mode='Markdown')
    else:
        k, exp, sc = keys_list[0]
        text = f"""╔══════════════════════════════════╗
║   ⚡  KEY GENERATED              ║
╚══════════════════════════════════╝

`{k}`

📱 **{app_nm}**
⏱ **{dur_disp}**
👥 **{devices}** device(s)
📅 Expires: `{exp}`"""
        if not unlimited:
            text += f"\n💰 Deducted: `{total_needed}` coins"
        bot.reply_to(message, text, parse_mode='Markdown')


@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    uid = message.from_user.id
    cmd = message.text.split()

    if is_owner(uid):
        if len(cmd) < 3:
            bot.reply_to(message, f"❌ `/genkey <time> <app_name> [devices]`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        dur_sec, dur_disp = parse_duration(cmd[1])
        if not dur_sec:
            bot.reply_to(message, "❌ Invalid time format", parse_mode='Markdown')
            return
        app_id = resolve_app(cmd[2])
        if not app_id:
            bot.reply_to(message, f"❌ Invalid app. Use: {app_list_str()}")
            return
        devices = 1
        if len(cmd) > 3:
            try:
                devices = int(cmd[3])
            except (TypeError, ValueError):
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
            bot.reply_to(message, "❌ Invalid time format", parse_mode='Markdown')
            return
        devices = 1
        if len(cmd) > 2:
            try:
                devices = int(cmd[2])
            except (TypeError, ValueError):
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
            bot.reply_to(message, f"❌ `/bulkkeys <count> <time> <app_name>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        try:
            count = int(cmd[1])
        except (TypeError, ValueError):
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
        except (TypeError, ValueError):
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


# ═══════════════════════ DELKEY / RESETKEY / LISTKEYS ═══════════════════════
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
    key_app, key_gen = info[0], info[1]

    if is_owner(uid):
        delete_key_soft(key)
        bot.reply_to(message, f"✅ Deleted: `{key}`", parse_mode='Markdown')
        return
    admin_app = get_admin_app(uid)
    if admin_app and key_app == admin_app:
        delete_key_soft(key)
        bot.reply_to(message, f"✅ Deleted: `{key}`", parse_mode='Markdown')
        return
    reseller_app, _ = get_reseller_app(uid)
    if reseller_app and key_app == reseller_app and key_gen == str(uid):
        delete_key_soft(key)
        bot.reply_to(message, f"✅ Deleted: `{key}`", parse_mode='Markdown')
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
    key_app, key_gen = info[0], info[1]

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
        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  KEY RESET                  ║
╚══════════════════════════════════╝

🔑 `{key}`
📱 **{app_display(key_app)}**

Device unlocked · Slots freed""", parse_mode='Markdown')
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
        bot.reply_to(message, "ℹ️ Koi key nahi hai")
        return

    r = "╔══════════════════════════════════╗\n║       🔑  YOUR KEYS              ║\n╚══════════════════════════════════╝\n\n"
    for k in rows[:15]:
        r += f"🔑 `{k[0]}`\n   {k[1]} · {k[2]} · {k[3]}dev\n\n"
    bot.reply_to(message, r, parse_mode='Markdown')


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

    r = "╔══════════════════════════════════╗\n║       🔑  ALL KEYS               ║\n╚══════════════════════════════════╝\n\n"
    for aid, keys in grouped.items():
        r += f"📱 **{app_display(aid)}** ({len(keys)})\n"
        for k, gen, status, exp, maxdev in keys[:3]:
            r += f"   └─ `{k}`\n      by `{gen}` · {status} · {maxdev}dev\n"
        if len(keys) > 3:
            r += f"   ... +{len(keys)-3} more\n"
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
    r = f"╔══════════════════════════════════╗\n║  🔑  KEYS by {admin_id}          ║\n╚══════════════════════════════════╝\n\n"
    for k, aid, gen, status, exp, maxdev, created in rows[:20]:
        r += f"🔑 `{k}`\n  📱 {app_display(aid)} · {status} · {maxdev}dev\n  📅 {exp}\n\n"
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
    r = f"╔══════════════════════════════════╗\n║  🔑  KEYS by RESELLER {rid}      ║\n╚══════════════════════════════════╝\n\n"
    for k, aid, gen, status, exp, maxdev, created in rows[:20]:
        r += f"🔑 `{k}`\n  📱 {app_display(aid)} · {status} · {maxdev}dev\n  📅 {exp}\n\n"
    if len(r) > 4000:
        r = r[:4000] + "\n\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


@bot.message_handler(commands=['dbstats'])
def cmd_dbstats(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return

    stats = get_db_stats()
    r = "╔══════════════════════════════════╗\n║     📈  DATABASE STATS          ║\n╚══════════════════════════════════╝\n\n"

    tot_keys = tot_active = tot_admins = tot_resellers = tot_slots = tot_busy = 0

    for pkg, s in stats.items():
        bar = progress_bar(s['slots_busy'], s['slots_total'])
        r += f"📱 **{s['name']}**\n"
        r += f"   🔑 {s['keys_active']}/{s['keys_total']} keys\n"
        r += f"   👤 {s['admins']} · 🛒 {s['resellers']}\n"
        r += f"   📊 `{bar}` {s['slots_busy']}/{s['slots_total']}\n"
        r += f"   💰 {fmt_rate(s['rate'])}/hr\n\n"

        tot_keys += s['keys_total']
        tot_active += s['keys_active']
        tot_admins += s['admins']
        tot_resellers += s['resellers']
        tot_slots += s['slots_total']
        tot_busy += s['slots_busy']

    r += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    r += f"**📊 GRAND TOTALS**\n"
    r += f"   🔑 {tot_active}/{tot_keys} keys\n"
    r += f"   👤 {tot_admins} admins\n"
    r += f"   🛒 {tot_resellers} resellers\n"
    r += f"   📊 {tot_busy}/{tot_slots} slots\n"

    if len(r) > 4000:
        r = r[:4000] + "\n... (truncated)"
    bot.reply_to(message, r, parse_mode='Markdown')


# ═══════════════════════ RESELLER MGMT ═══════════════════════
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
    except (TypeError, ValueError):
        bot.reply_to(message, "❌ Invalid format")
        return
    if coins < 0:
        bot.reply_to(message, "❌ Coins >= 0")
        return
    app_id = admin_app if admin_app else APP_IDS()[0]
    name = app_display(app_id)
    add_reseller(rid, app_id, coins)
    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  RESELLER ADDED             ║
╚══════════════════════════════════╝

👤 `{rid}`
📱 **{name}**
💰 `{coins}` coins""", parse_mode='Markdown')


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
    app_id = admin_app if admin_app else APP_IDS()[0]
    name = app_display(app_id)
    rows = list_resellers(app_id)
    if not rows:
        bot.reply_to(message, f"ℹ️ No resellers for {name}")
        return
    r = f"╔══════════════════════════════════╗\n║  🛒  RESELLERS · {name[:15]:<15}║\n╚══════════════════════════════════╝\n\n"
    for tid, bal in rows:
        r += f"👤 `{tid}` · 💰 `{bal}`\n"
    bot.reply_to(message, r, parse_mode='Markdown')


# ═══════════════════════ BALANCE ═══════════════════════
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
            bot.reply_to(message, f"❌ `/addbalance <reseller_id> <amount> <app_name>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        try:
            rid = cmd[1]
            amount = int(cmd[2])
        except (TypeError, ValueError):
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
        except (TypeError, ValueError):
            bot.reply_to(message, "❌ Invalid id or amount")
            return
        app_id = admin_app

    if amount <= 0:
        bot.reply_to(message, "❌ Amount > 0")
        return

    new_bal = add_reseller_balance(rid, app_id, amount)
    name = app_display(app_id)
    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  BALANCE ADDED              ║
╚══════════════════════════════════╝

👤 `{rid}`
📱 **{name}**
💰 `+{amount}` → `{new_bal}`""", parse_mode='Markdown')


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
            bot.reply_to(message, f"❌ `/removebalance <reseller_id> <amount> <app_name>`\n\n📱 **Apps:** {app_list_str()}", parse_mode='Markdown')
            return
        try:
            rid = cmd[1]
            amount = int(cmd[2])
        except (TypeError, ValueError):
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
        except (TypeError, ValueError):
            bot.reply_to(message, "❌ Invalid id or amount")
            return
        app_id = admin_app

    if amount <= 0:
        bot.reply_to(message, "❌ Amount > 0")
        return

    ok, new_bal = deduct_reseller_balance(rid, app_id, amount)
    if not ok:
        bot.reply_to(message, f"❌ Insufficient balance: `{new_bal}`", parse_mode='Markdown')
        return
    name = app_display(app_id)
    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  BALANCE REMOVED            ║
╚══════════════════════════════════╝

👤 `{rid}`
📱 **{name}**
💰 `-{amount}` → `{new_bal}`""", parse_mode='Markdown')


@bot.message_handler(commands=['balance'])
def cmd_balance(message):
    uid = message.from_user.id
    reseller_app, bal = get_reseller_app(uid)
    if not reseller_app:
        bot.reply_to(message, "❌ Reseller only")
        return
    name = app_display(reseller_app)
    rate = get_app_rate(reseller_app)
    bot.reply_to(message, f"""╔══════════════════════════════════╗
║   💰  BALANCE                    ║
╚══════════════════════════════════╝

📱 **{name}**
💰 `{bal}` coins
🏷 `{fmt_rate(rate)}` coins/hour/key""", parse_mode='Markdown')


# ═══════════════════════ MAINTENANCE ═══════════════════════
@bot.message_handler(commands=['maintenance'])
def cmd_maintenance(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ Owner only")
        return
    cmd = message.text.split()
    current = get_maintenance()
    if len(cmd) < 2:
        bot.reply_to(message, f"🔧 Maintenance: {'🔴 ON' if current == 'on' else '🟢 OFF'}")
        return
    arg = cmd[1].lower()
    if arg == "on":
        set_maintenance("on")
        bot.reply_to(message, """╔══════════════════════════════════╗
║   🔧  MAINTENANCE: ON            ║
╚══════════════════════════════════╝

⏸ Keys & slots time frozen.
All operations paused.""")
    elif arg == "off":
        frozen = set_maintenance("off")
        frozen_str = ""
        if frozen:
            frozen_str = f"\n\n⏱ Restored: **{fmt_remaining(frozen)}**\n🔑 Keys & slots auto-extended."
        bot.reply_to(message, f"""╔══════════════════════════════════╗
║   ✅  MAINTENANCE: OFF           ║
╚══════════════════════════════════╝{frozen_str}""", parse_mode='Markdown')
    else:
        bot.reply_to(message, "❌ Use `on` or `off`", parse_mode='Markdown')


def run_key_bot():
    while True:
        try:
            bot.polling(non_stop=True, interval=1, timeout=10, long_polling_timeout=10)
        except Exception as e:
            print(f"⚠️ Key Bot: {e}")
            time.sleep(5)


# ═══════════════════════ MAIN ═══════════════════════
def main():
    init_db()

    print("=" * 60)
    print("⚡ LIGHTNING VPS — FINAL EDITION")
    print("=" * 60)
    print(f"👑 Owner: {OWNER_ID}")
    print(f"📱 Apps loaded: {len(APP_IDS())}")
    print(f"🌐 API Port: {API_PORT}")
    print(f"🔒 Bypass: {BYPASS_PACKAGES if BYPASS_PACKAGES else 'NONE (all isolated)'}")
    print("-" * 60)
    for pkg in APP_IDS():
        print(f"   • {app_display(pkg):12s} → {fmt_rate(get_app_rate(pkg)):>5}/hr | "
              f"slots {get_app_slots(pkg)} | prefix {app_prefix(pkg)}")
    print("=" * 60)
    print("✅ Running...")
    print("=" * 60)

    threading.Thread(target=auto_release_loop, daemon=True).start()
    threading.Thread(target=run_key_bot, daemon=True).start()

    app.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    main()
