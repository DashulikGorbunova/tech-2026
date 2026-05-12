from __future__ import annotations

import json
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
    bio: str
    interests: str
    preferred_gender: str
    age_min: int
    age_max: int
    photo_count: int
    deleted_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProfileRating:
    profile_id: int
    primary_rating: float
    behavior_rating: float
    combined_rating: float
    likes_in: int
    skips_in: int
    matches_in: int
    updated_at: str


class UserStorage:
    def __init__(self, db_path: Path | str = "data/bot.sqlite3") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        Path("data/photos").mkdir(parents=True, exist_ok=True)   # добавили папку для фото
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_ratings (
                    profile_id INTEGER PRIMARY KEY,
                    primary_rating REAL NOT NULL DEFAULT 0.5,
                    behavior_rating REAL NOT NULL DEFAULT 0.5,
                    combined_rating REAL NOT NULL DEFAULT 0.5,
                    likes_in INTEGER NOT NULL DEFAULT 0,
                    skips_in INTEGER NOT NULL DEFAULT 0,
                    matches_in INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(profile_id) REFERENCES profiles(id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_profile_id INTEGER NOT NULL,
                    to_profile_id INTEGER NOT NULL,
                    is_like INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(from_profile_id, to_profile_id),
                    FOREIGN KEY(from_profile_id) REFERENCES profiles(id),
                    FOREIGN KEY(to_profile_id) REFERENCES profiles(id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_low INTEGER NOT NULL,
                    profile_high INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(profile_low, profile_high),
                    FOREIGN KEY(profile_low) REFERENCES profiles(id),
                    FOREIGN KEY(profile_high) REFERENCES profiles(id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_inter_to ON interactions(to_profile_id);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_inter_from ON interactions(from_profile_id);"
            )
            self._apply_profile_migrations(conn)
            conn.commit()

    def _apply_profile_migrations(self, conn: sqlite3.Connection) -> None:
        cur = conn.execute("PRAGMA table_info(profiles);")
        cols = {str(r[1]) for r in cur.fetchall()}
        alters: list[str] = []
        if "bio" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN bio TEXT NOT NULL DEFAULT '';")
        if "interests" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN interests TEXT NOT NULL DEFAULT '';")
        if "preferred_gender" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN preferred_gender TEXT NOT NULL DEFAULT 'any';")
        if "age_min" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN age_min INTEGER NOT NULL DEFAULT 18;")
        if "age_max" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN age_max INTEGER NOT NULL DEFAULT 99;")
        if "photo_count" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN photo_count INTEGER NOT NULL DEFAULT 0;")
        if "deleted_at" not in cols:
            alters.append("ALTER TABLE profiles ADD COLUMN deleted_at TEXT;")
        for sql in alters:
            conn.execute(sql)

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

    def _row_to_profile(self, row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            age=row["age"],
            gender=row["gender"],
            city=row["city"],
            bio=row["bio"] if "bio" in row.keys() else "",
            interests=row["interests"] if "interests" in row.keys() else "",
            preferred_gender=row["preferred_gender"] if "preferred_gender" in row.keys() else "any",
            age_min=row["age_min"] if "age_min" in row.keys() else 18,
            age_max=row["age_max"] if "age_max" in row.keys() else 99,
            photo_count=row["photo_count"] if "photo_count" in row.keys() else 0,
            deleted_at=row["deleted_at"] if "deleted_at" in row.keys() else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_profile(
        self,
        user_id: int,
        name: str,
        age: int,
        gender: str,
        city: str,
        bio: str = "",
        interests: str = "",
        preferred_gender: str = "any",
        age_min: int = 18,
        age_max: int = 99,
        photo_count: int = 0,
    ) -> Profile:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO profiles (
                        user_id, name, age, gender, city, bio, interests,
                        preferred_gender, age_min, age_max, photo_count,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        name,
                        age,
                        gender,
                        city,
                        bio,
                        interests,
                        preferred_gender,
                        age_min,
                        age_max,
                        photo_count,
                        now,
                        now,
                    ),
                )
                profile_id = cursor.lastrowid
            else:
                profile_id = existing["id"]
                conn.execute(
                    """
                    UPDATE profiles
                    SET name = ?, age = ?, gender = ?, city = ?,
                        bio = ?, interests = ?,
                        preferred_gender = ?, age_min = ?, age_max = ?, photo_count = ?,
                        updated_at = ?, deleted_at = NULL
                    WHERE id = ?
                    """,
                    (
                        name,
                        age,
                        gender,
                        city,
                        bio,
                        interests,
                        preferred_gender,
                        age_min,
                        age_max,
                        photo_count,
                        now,
                        profile_id,
                    ),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            assert row is not None
            pid = row["id"]
            has_rating = conn.execute(
                "SELECT 1 FROM profile_ratings WHERE profile_id = ?",
                (pid,),
            ).fetchone()
            if has_rating is None:
                conn.execute(
                    """
                    INSERT INTO profile_ratings (
                        profile_id, primary_rating, behavior_rating, combined_rating,
                        likes_in, skips_in, matches_in, updated_at
                    )
                    VALUES (?, 0.5, 0.5, 0.5, 0, 0, 0, ?)
                    """,
                    (pid, now),
                )
                conn.commit()

        p = self.get_profile_by_id(profile_id)
        assert p is not None
        return p

    def get_profile_by_id(self, profile_id: int) -> Profile | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profiles WHERE id = ? AND deleted_at IS NULL",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_profile(row)

    def get_profile_by_telegram_id(self, telegram_id: int) -> Profile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*
                FROM profiles p
                JOIN users u ON u.id = p.user_id
                WHERE u.telegram_id = ? AND p.deleted_at IS NULL
                """,
                (telegram_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_profile(row)

    def get_user_id_by_telegram_id(self, telegram_id: int) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
        return int(row["id"]) if row else None

    def update_profile_fields(
        self,
        user_id: int,
        name: str | None = None,
        age: int | None = None,
        city: str | None = None,
        bio: str | None = None,
        interests: str | None = None,
        preferred_gender: str | None = None,
        age_min: int | None = None,
        age_max: int | None = None,
        photo_count: int | None = None,
    ) -> Profile | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        p = self._row_to_profile(row)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE profiles SET
                    name = COALESCE(?, name),
                    age = COALESCE(?, age),
                    city = COALESCE(?, city),
                    bio = COALESCE(?, bio),
                    interests = COALESCE(?, interests),
                    preferred_gender = COALESCE(?, preferred_gender),
                    age_min = COALESCE(?, age_min),
                    age_max = COALESCE(?, age_max),
                    photo_count = COALESCE(?, photo_count),
                    updated_at = ?
                WHERE user_id = ?
                """,
                (
                    name,
                    age,
                    city,
                    bio,
                    interests,
                    preferred_gender,
                    age_min,
                    age_max,
                    photo_count,
                    now,
                    user_id,
                ),
            )
            conn.commit()
        return self.get_profile_by_telegram_id(
            (self._get_telegram_id_for_user(user_id))
        )

    def _get_telegram_id_for_user(self, user_id: int) -> int:
        with self._connect() as conn:
            r = conn.execute("SELECT telegram_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if r is None:
            raise ValueError("user not found")
        return int(r["telegram_id"])

    def delete_profile(self, user_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            r = conn.execute("SELECT id FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
            if r is None:
                return False
            conn.execute("UPDATE profiles SET deleted_at = ?, updated_at = ? WHERE user_id = ?", (now, now, user_id))
            conn.commit()
        return True

    def get_survey_counts(self, profile_id: int) -> tuple[int, int, int]:
        with self._connect() as conn:
            li = conn.execute(
                "SELECT COUNT(*) AS c FROM interactions WHERE to_profile_id = ? AND is_like = 1",
                (profile_id,),
            ).fetchone()
            sk = conn.execute(
                "SELECT COUNT(*) AS c FROM interactions WHERE to_profile_id = ? AND is_like = 0",
                (profile_id,),
            ).fetchone()
            mt = conn.execute(
                """
                SELECT COUNT(*) AS c FROM matches
                WHERE profile_low = ? OR profile_high = ?
                """,
                (profile_id, profile_id),
            ).fetchone()
        return (int(li["c"]), int(sk["c"]), int(mt["c"]))

    def get_already_shown_to_ids(self, from_profile_id: int) -> set[int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT to_profile_id FROM interactions WHERE from_profile_id = ?",
                (from_profile_id,),
            ).fetchall()
        return {int(r[0]) for r in rows}

    def get_rating_row(self, profile_id: int) -> ProfileRating | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM profile_ratings WHERE profile_id = ?", (profile_id,)).fetchone()
        if row is None:
            return None
        return ProfileRating(
            profile_id=row["profile_id"],
            primary_rating=row["primary_rating"],
            behavior_rating=row["behavior_rating"],
            combined_rating=row["combined_rating"],
            likes_in=row["likes_in"],
            skips_in=row["skips_in"],
            matches_in=row["matches_in"],
            updated_at=row["updated_at"],
        )

    def upsert_rating(
        self,
        profile_id: int,
        primary: float,
        behavior: float,
        combined: float,
        likes_in: int,
        skips_in: int,
        matches_in: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profile_ratings (
                    profile_id, primary_rating, behavior_rating, combined_rating,
                    likes_in, skips_in, matches_in, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    primary_rating = excluded.primary_rating,
                    behavior_rating = excluded.behavior_rating,
                    combined_rating = excluded.combined_rating,
                    likes_in = excluded.likes_in,
                    skips_in = excluded.skips_in,
                    matches_in = excluded.matches_in,
                    updated_at = excluded.updated_at
                """,
                (profile_id, primary, behavior, combined, likes_in, skips_in, matches_in, now),
            )
            conn.commit()

    def list_candidate_profiles(
        self,
        viewer: Profile,
        exclude_ids: set[int],
        limit: int = 200,
    ) -> list[Profile]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.* FROM profiles p
                WHERE p.deleted_at IS NULL
                AND p.id != ?
                ORDER BY p.id ASC
                """,
                (viewer.id,),
            ).fetchall()
        out: list[Profile] = []
        for r in rows:
            p = self._row_to_profile(r)
            if p.id in exclude_ids:
                continue
            if viewer.preferred_gender != "any" and p.gender != viewer.preferred_gender:
                continue
            if not (viewer.age_min <= p.age <= viewer.age_max):
                continue
            if p.preferred_gender != "any" and p.preferred_gender != viewer.gender:
                continue
            if not (p.age_min <= viewer.age <= p.age_max):
                continue
            out.append(p)
            if len(out) >= limit:
                break
        return out

    def has_liked(
        self,
        from_profile_id: int,
        to_profile_id: int,
    ) -> bool:
        with self._connect() as conn:
            r = conn.execute(
                """
                SELECT is_like FROM interactions
                WHERE from_profile_id = ? AND to_profile_id = ?
                """,
                (from_profile_id, to_profile_id),
            ).fetchone()
        return r is not None and int(r[0]) == 1

    def add_interaction(
        self,
        from_profile_id: int,
        to_profile_id: int,
        is_like: bool,
    ) -> tuple[bool, bool, int | None]:
        """(created, mutual_match, match_id|None). created=False — дубликат swipes (race)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO interactions (from_profile_id, to_profile_id, is_like, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (from_profile_id, to_profile_id, 1 if is_like else 0, now),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                return False, False, None
            match_id: int | None = None
            mutual = False
            if is_like:
                prev = conn.execute(
                    """
                    SELECT is_like FROM interactions
                    WHERE from_profile_id = ? AND to_profile_id = ?
                    """,
                    (to_profile_id, from_profile_id),
                ).fetchone()
                mutual = prev is not None and int(prev[0]) == 1
                if mutual:
                    lo = min(from_profile_id, to_profile_id)
                    hi = max(from_profile_id, to_profile_id)
                    try:
                        c = conn.execute(
                            """
                            INSERT INTO matches (profile_low, profile_high, created_at)
                            VALUES (?, ?, ?)
                            """,
                            (lo, hi, now),
                        )
                        match_id = c.lastrowid
                    except sqlite3.IntegrityError:
                        row = conn.execute(
                            "SELECT id FROM matches WHERE profile_low = ? AND profile_high = ?",
                            (lo, hi),
                        ).fetchone()
                        match_id = int(row["id"]) if row else None
            conn.commit()
        return True, mutual, match_id

    def recompute_aggregates_from_db(self, profile_id: int) -> tuple[int, int, int]:
        return self.get_survey_counts(profile_id)

    def to_jsonable_profile(self, p: Profile) -> dict:
        return {
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "gender": p.gender,
            "city": p.city,
            "bio": p.bio,
            "interests": p.interests,
            "photo_count": p.photo_count,
        }

    def get_event_log_payload(
        self,
        event_type: str,
        from_profile_id: int,
        to_profile_id: int,
        extra: dict | None = None,
    ) -> str:
        payload = {
            "type": event_type,
            "from": from_profile_id,
            "to": to_profile_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload["extra"] = extra
        return json.dumps(payload, ensure_ascii=False)

        def save_photo(self, telegram_id: int, file_path: str) -> bool:
            """Сохраняет фото и увеличивает счётчик"""
            try:
                with self._connect() as conn:
                    # Обновляем счётчик
                    cur = conn.execute(
                        "UPDATE profiles SET photo_count = photo_count + 1 WHERE user_id = ?",
                        (telegram_id,)
                    )
                    conn.commit()
                    
                    if cur.rowcount > 0:
                        logger.info(f"Фото добавлено пользователю {telegram_id}")
                        return True
                    return False
            except Exception as e:
                logger.error(f"save_photo error: {e}")
                return False
                
        def get_all_active_profiles(self) -> list[int]:
            """Нужен для Celery"""
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM profiles WHERE deleted_at IS NULL"
                ).fetchall()
            return [int(r[0]) for r in rows]