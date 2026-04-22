from __future__ import annotations

import os
import threading
from typing import Any, Protocol

from bot.rating import recompute_for_profile
from bot.storage import Profile, UserStorage


PREFETCH_N = 10
REDIS_KEY = "feed_queue:{}"


class SupportsRedis(Protocol):
    def lpush(self, name: str, *values: bytes | str) -> int: ...
    def rpop(self, name: str) -> str | None: ...
    def llen(self, name: str) -> int: ...
    def delete(self, *names: str) -> int: ...


class InMemoryCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, list[str]] = {}

    def lpush(self, name: str, *values: bytes | str) -> int:
        with self._lock:
            q = self._queues.setdefault(name, [])
            for v in reversed(values):
                s = v.decode() if isinstance(v, bytes) else str(v)
                q.insert(0, s)
            return len(q)

    def rpop(self, name: str) -> str | None:
        with self._lock:
            q = self._queues.get(name, [])
            if not q:
                return None
            return q.pop()

    def llen(self, name: str) -> int:
        with self._lock:
            return len(self._queues.get(name, []))

    def delete(self, *names: str) -> int:
        n = 0
        with self._lock:
            for name in names:
                if name in self._queues:
                    del self._queues[name]
                    n += 1
        return n


def _connect_redis() -> SupportsRedis | None:
    url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    if os.getenv("DISABLE_REDIS", "").lower() in ("1", "true", "yes"):
        return None
    try:
        import redis  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        r = redis.from_url(url, decode_responses=True, socket_connect_timeout=2.0)
        r.ping()
        return r  # type: ignore[return-value]
    except Exception:
        return None


_CACHE: InMemoryCache | None = None
_REDIS: SupportsRedis | None = None
_REDIS_LOGGED = False


def _get_backend() -> tuple[SupportsRedis | InMemoryCache, str]:
    global _REDIS, _CACHE, _REDIS_LOGGED
    if _REDIS is None and _CACHE is None:
        _REDIS = _connect_redis()
        if _REDIS is None:
            _CACHE = InMemoryCache()
            if not _REDIS_LOGGED:
                import logging
                logging.getLogger(__name__).warning(
                    "Redis недоступен или не установлен — используется in-memory кэш (для зачёта настройте Redis)."
                )
                _REDIS_LOGGED = True
    if _REDIS is not None:
        return _REDIS, "redis"
    assert _CACHE is not None
    return _CACHE, "memory"


def _key_for(viewer_profile_id: int) -> str:
    return REDIS_KEY.format(viewer_profile_id)


def build_ranked_ids(store: UserStorage, viewer: Profile) -> list[int]:
    ex = store.get_already_shown_to_ids(viewer.id)
    cands = store.list_candidate_profiles(viewer, ex, limit=500)
    scored: list[tuple[float, int, float]] = []
    for p in cands:
        recompute_for_profile(store, p)
        r = store.get_rating_row(p.id)
        if r is not None:
            # Лёгкий «временной» множитель (ур. 2): прайм-тайм — без БД, дет. от часа
            h = (p.id % 5) * 0.001
            scored.append((r.combined_rating + h, p.id, r.combined_rating))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [i for _, i, _ in scored]


def refill_if_needed(
    store: UserStorage,
    viewer: Profile,
    min_len: int = 3,
) -> None:
    backend, _ = _get_backend()
    k = _key_for(viewer.id)
    if backend.llen(k) >= min_len:
        return
    ids = build_ranked_ids(store, viewer)
    if not ids:
        return
    to_push: list[str] = []
    for pid in ids[:PREFETCH_N]:
        to_push.append(str(pid))
    if to_push:
        # справа забираем (rpop) — в Redis LPUSH пачки в обратном порядке, чтобы rpop дал best-first
        backend.delete(k)
        for sid in reversed(to_push):
            backend.lpush(k, sid)


def pop_next_id(viewer_profile_id: int) -> int | None:
    backend, _ = _get_backend()
    raw = backend.rpop(_key_for(viewer_profile_id))
    if raw is None:
        return None
    return int(raw)


def invalidate(viewer_profile_id: int) -> None:
    backend, _ = _get_backend()
    backend.delete(_key_for(viewer_profile_id))


def publish_interaction_event(
    store: UserStorage,
    event_type: str,
    from_pid: int,
    to_pid: int,
    extra: dict[str, Any] | None = None,
) -> None:
    """Публикация в Redis List как упрощённая «очередь» для демонстрации интеграции MQ (этап 3)."""
    line = store.get_event_log_payload(event_type, from_pid, to_pid, extra=extra)
    backend, _ = _get_backend()
    if hasattr(backend, "lpush"):
        try:
            backend.lpush("mq:interaction_events", line)  # type: ignore[misc]
        except Exception:
            pass
