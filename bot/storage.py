from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class User:
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Profile:
    id: int
    user_id: int
    name: str
    age: int
    gender: str
    city: str
    created_at: str
    updated_at: str


class UserStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    gender TEXT NOT NULL,
                    city TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )
            conn.commit()

    def register_or_update_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> tuple[User, bool]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO users (
                        telegram_id, username, first_name, last_name, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (telegram_id, username, first_name, last_name, now, now),
                )
                conn.commit()
                user_id = cursor.lastrowid
                created = True
            else:
                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, first_name = ?, last_name = ?, updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (username, first_name, last_name, now, telegram_id),
                )
                conn.commit()
                user_id = row["id"]
                created = False

            current = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

        return (
            User(
                id=current["id"],
                telegram_id=current["telegram_id"],
                username=current["username"],
                first_name=current["first_name"],
                last_name=current["last_name"],
                created_at=current["created_at"],
                updated_at=current["updated_at"],
            ),
            created,
        )

    def save_profile(
        self,
        user_id: int,
        name: str,
        age: int,
        gender: str,
        city: str,
    ) -> Profile:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO profiles (user_id, name, age, gender, city, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, name, age, gender, city, now, now),
                )
                profile_id = cursor.lastrowid
            else:
                profile_id = existing["id"]
                conn.execute(
                    """
                    UPDATE profiles
                    SET name = ?, age = ?, gender = ?, city = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (name, age, gender, city, now, profile_id),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()

        return Profile(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            age=row["age"],
            gender=row["gender"],
            city=row["city"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_profile_by_telegram_id(self, telegram_id: int) -> Profile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*
                FROM profiles p
                JOIN users u ON u.id = p.user_id
                WHERE u.telegram_id = ?
                """,
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        return Profile(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            age=row["age"],
            gender=row["gender"],
            city=row["city"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
