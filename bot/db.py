"""SQLite operations for the Xray bot."""
from typing import Optional, List
import aiosqlite

from config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    is_admin INTEGER DEFAULT 0,
    keys_limit INTEGER DEFAULT 0,
    keys_created INTEGER DEFAULT 0,
    awaiting_intro INTEGER DEFAULT 0,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_allowances (
    username TEXT PRIMARY KEY,
    amount INTEGER DEFAULT 0,
    added_by INTEGER,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pending_requests (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    intro_text TEXT,
    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS keys (
    key_id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    email TEXT UNIQUE NOT NULL,
    uuid TEXT UNIQUE NOT NULL,
    is_legacy INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_keys_telegram_id ON keys(telegram_id);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Migrate: add awaiting_intro column if not present
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]
        if "awaiting_intro" not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN awaiting_intro INTEGER DEFAULT 0")
        await db.commit()


# ========== users ==========

async def upsert_user(telegram_id: int, username: Optional[str]):
    """Register user or update username on existing record."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, username) VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
            """,
            (telegram_id, username),
        )
        await db.commit()


async def get_user(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_user_by_username(username: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def increase_keys_limit(telegram_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET keys_limit = keys_limit + ? WHERE telegram_id = ?",
            (amount, telegram_id),
        )
        await db.commit()


async def list_all_users() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users ORDER BY first_seen")
        return [dict(r) for r in await cursor.fetchall()]


# ========== pending_allowances ==========

async def add_pending_allowance(username: str, amount: int, added_by: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO pending_allowances (username, amount, added_by)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET amount = amount + excluded.amount
            """,
            (username.lower(), amount, added_by),
        )
        await db.commit()


async def consume_pending_allowance(username: str) -> int:
    """Apply pending allowance: return amount and delete record."""
    if not username:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT amount FROM pending_allowances WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        row = await cursor.fetchone()
        if not row:
            return 0
        amount = row[0]
        await db.execute(
            "DELETE FROM pending_allowances WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        await db.commit()
        return amount


async def set_awaiting_intro(telegram_id: int, flag: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET awaiting_intro = ? WHERE telegram_id = ?",
            (1 if flag else 0, telegram_id),
        )
        await db.commit()


# ========== pending_requests (user-initiated access requests) ==========

async def create_pending_request(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    intro_text: str,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO pending_requests (telegram_id, username, first_name, intro_text)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                intro_text = excluded.intro_text,
                requested_at = CURRENT_TIMESTAMP
            """,
            (telegram_id, username, first_name, intro_text),
        )
        await db.commit()


async def get_pending_request(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM pending_requests WHERE telegram_id = ?",
            (telegram_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_pending_request(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM pending_requests WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()


async def list_pending_requests() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM pending_requests ORDER BY requested_at"
        )
        return [dict(r) for r in await cursor.fetchall()]


# ========== pending_allowances ==========

async def list_pending_allowances() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM pending_allowances ORDER BY added_at")
        return [dict(r) for r in await cursor.fetchall()]


# ========== keys ==========

async def create_key_record(
    telegram_id: Optional[int],
    email: str,
    uuid_str: str,
    is_legacy: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO keys (telegram_id, email, uuid, is_legacy) VALUES (?, ?, ?, ?)",
            (telegram_id, email, uuid_str, 1 if is_legacy else 0),
        )
        if telegram_id is not None:
            await db.execute(
                "UPDATE users SET keys_created = keys_created + 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
        await db.commit()


async def get_keys_by_user(telegram_id: int) -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM keys WHERE telegram_id = ?",
            (telegram_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


async def get_all_keys() -> List[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT k.*, u.username AS owner_username
            FROM keys k
            LEFT JOIN users u ON k.telegram_id = u.telegram_id
            ORDER BY k.created_at
            """
        )
        return [dict(r) for r in await cursor.fetchall()]


async def email_exists(email: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM keys WHERE email = ?",
            (email,),
        )
        return (await cursor.fetchone()) is not None


async def delete_user_keys(telegram_id: int) -> List[str]:
    """Delete all user's keys, reset their counters. Returns deleted emails."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT email FROM keys WHERE telegram_id = ?",
            (telegram_id,),
        )
        emails = [r[0] for r in await cursor.fetchall()]
        await db.execute(
            "DELETE FROM keys WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.execute(
            "UPDATE users SET keys_created = 0, keys_limit = 0 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()
        return emails
