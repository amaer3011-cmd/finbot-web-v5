"""قاعدة بيانات SQLite بسيطة: الأكواد المحفوظة + عدّاد الصلاة على النبي + سجل المهام (للوحة الأدمن في المرحلة 2)."""
import sqlite3
import threading
import time

_lock = threading.Lock()
_conn = None


def init(path="bot.db"):
    global _conn
    _conn = sqlite3.connect(path, check_same_thread=False)
    with _lock:
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                tg_name TEXT,
                username TEXT,
                first_seen INTEGER,
                last_seen INTEGER
            );
            CREATE TABLE IF NOT EXISTS counters (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                started INTEGER,
                finished INTEGER,
                status TEXT,
                questions INTEGER DEFAULT 0,
                activities INTEGER DEFAULT 0,
                note TEXT
            );
            INSERT OR IGNORE INTO counters(name, value) VALUES ('salawat', 0);
            """
        )
        _conn.commit()


def touch_user(tg_id, tg_name=""):
    now = int(time.time())
    with _lock:
        _conn.execute(
            "INSERT INTO users(tg_id, tg_name, first_seen, last_seen) VALUES (?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET tg_name=excluded.tg_name, last_seen=excluded.last_seen",
            (tg_id, tg_name, now, now),
        )
        _conn.commit()


def save_username(tg_id, username):
    with _lock:
        _conn.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
        _conn.commit()


def get_username(tg_id):
    with _lock:
        row = _conn.execute("SELECT username FROM users WHERE tg_id=?", (tg_id,)).fetchone()
    return row[0] if row and row[0] else None


def add_salawat():
    with _lock:
        _conn.execute("UPDATE counters SET value=value+1 WHERE name='salawat'")
        value = _conn.execute("SELECT value FROM counters WHERE name='salawat'").fetchone()[0]
        _conn.commit()
    return value


def get_salawat():
    with _lock:
        return _conn.execute("SELECT value FROM counters WHERE name='salawat'").fetchone()[0]


def log_job(tg_id, started, status, questions, activities, note=""):
    with _lock:
        _conn.execute(
            "INSERT INTO jobs(tg_id, started, finished, status, questions, activities, note) VALUES (?,?,?,?,?,?,?)",
            (tg_id, int(started), int(time.time()), status, questions, activities, note[:300]),
        )
        _conn.commit()
