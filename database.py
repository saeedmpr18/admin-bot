# -*- coding: utf-8 -*-
"""
ماژول دیتابیس ربات مدیریت گروه
از SQLite برای ذخیره‌ی تنظیمات هر گروه، اخطارها و کلمات ممنوعه استفاده می‌کنه.
"""

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot_data.db"

# چون python-telegram-bot به صورت async کار می‌کنه ولی sqlite3 sync هست،
# از یه لاک ساده برای جلوگیری از تداخل هم‌زمان استفاده می‌کنیم.
_lock = threading.Lock()


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """ساخت جدول‌ها در صورت عدم وجود"""
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                link_filter INTEGER DEFAULT 1,
                word_filter INTEGER DEFAULT 1,
                welcome_message TEXT DEFAULT 'سلام {name} عزیز، به گروه خوش اومدی! 🌹',
                welcome_enabled INTEGER DEFAULT 1,
                max_warnings INTEGER DEFAULT 3,
                punishment TEXT DEFAULT 'mute'  -- mute یا ban
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                chat_id INTEGER,
                user_id INTEGER,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS banned_words (
                chat_id INTEGER,
                word TEXT,
                PRIMARY KEY (chat_id, word)
            )
        """)
        conn.commit()
        conn.close()


def get_settings(chat_id: int) -> sqlite3.Row:
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO chat_settings (chat_id) VALUES (?)", (chat_id,))
            conn.commit()
            cur.execute("SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,))
            row = cur.fetchone()
        conn.close()
        return row


def update_setting(chat_id: int, field: str, value):
    allowed_fields = {
        "link_filter", "word_filter", "welcome_message",
        "welcome_enabled", "max_warnings", "punishment"
    }
    if field not in allowed_fields:
        raise ValueError(f"فیلد نامعتبر: {field}")
    get_settings(chat_id)  # اطمینان از وجود ردیف
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(f"UPDATE chat_settings SET {field} = ? WHERE chat_id = ?", (value, chat_id))
        conn.commit()
        conn.close()


def add_warning(chat_id: int, user_id: int) -> int:
    """یک اخطار اضافه می‌کنه و تعداد فعلی رو برمی‌گردونه"""
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO warnings (chat_id, user_id, count) VALUES (?, ?, 1)", (chat_id, user_id))
            new_count = 1
        else:
            new_count = row["count"] + 1
            cur.execute("UPDATE warnings SET count = ? WHERE chat_id = ? AND user_id = ?",
                        (new_count, chat_id, user_id))
        conn.commit()
        conn.close()
        return new_count


def remove_warning(chat_id: int, user_id: int) -> int:
    """یک اخطار کم می‌کنه (حداقل صفر) و تعداد جدید رو برمی‌گردونه"""
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = cur.fetchone()
        if row is None:
            conn.close()
            return 0
        new_count = max(0, row["count"] - 1)
        cur.execute("UPDATE warnings SET count = ? WHERE chat_id = ? AND user_id = ?",
                    (new_count, chat_id, user_id))
        conn.commit()
        conn.close()
        return new_count


def get_warnings(chat_id: int, user_id: int) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = cur.fetchone()
        conn.close()
        return row["count"] if row else 0


def reset_warnings(chat_id: int, user_id: int):
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        conn.commit()
        conn.close()


def add_banned_word(chat_id: int, word: str):
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO banned_words (chat_id, word) VALUES (?, ?)",
                    (chat_id, word.lower().strip()))
        conn.commit()
        conn.close()


def remove_banned_word(chat_id: int, word: str) -> bool:
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM banned_words WHERE chat_id = ? AND word = ?",
                    (chat_id, word.lower().strip()))
        changed = cur.rowcount > 0
        conn.commit()
        conn.close()
        return changed


def get_banned_words(chat_id: int) -> list:
    with _lock:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT word FROM banned_words WHERE chat_id = ?", (chat_id,))
        rows = cur.fetchall()
        conn.close()
        return [r["word"] for r in rows]
