from __future__ import annotations

import asyncio
from dataclasses import dataclass

import redis.asyncio as redis


@dataclass
class RedisConfig:
    url: str = "redis://localhost:6379/0"
    queue: str = "bench_list"


async def redis_setup(cfg: RedisConfig) -> None:
    # no-op for list-based queue
    return None


async def redis_purge(cfg: RedisConfig) -> None:
    r = redis.from_url(cfg.url, decode_responses=False)
    try:
        await r.delete(cfg.queue)
    finally:
        await r.aclose()


async def redis_backlog(cfg: RedisConfig) -> int | None:
    r = redis.from_url(cfg.url, decode_responses=False)
    try:
        return int(await r.llen(cfg.queue))
    except Exception:
        return None
    finally:
        await r.aclose()


class RedisProducer:
    def __init__(self, cfg: RedisConfig):
        self.cfg = cfg
        self._r: redis.Redis | None = None

    async def __aenter__(self) -> "RedisProducer":
        self._r = redis.from_url(self.cfg.url, decode_responses=False)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._r is not None:
            await self._r.aclose()

    async def send(self, body: bytes) -> None:
        if self._r is None:
            raise RuntimeError("producer not started")
        # queue semantics: append right, consumer pops left
        await self._r.rpush(self.cfg.queue, body)


class RedisConsumer:
    def __init__(self, cfg: RedisConfig):
        self.cfg = cfg
        self._r: redis.Redis | None = None

    async def __aenter__(self) -> "RedisConsumer":
        self._r = redis.from_url(self.cfg.url, decode_responses=False)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._r is not None:
            await self._r.aclose()

    async def consume_loop(self, handler, stop_event: asyncio.Event) -> None:
        if self._r is None:
            raise RuntimeError("consumer not started")
        while not stop_event.is_set():
            try:
                # Redis 7+ supports LPOP with count (batch pop).
                # This drastically reduces round-trips vs BLPOP per item.
                batch = await self._r.lpop(self.cfg.queue, count=200)
                if not batch:
                    await asyncio.sleep(0.01)
                    continue
                if isinstance(batch, (bytes, bytearray, memoryview)):
                    await handler(bytes(batch))
                    continue
                # list[bytes]
                for body in batch:
                    await handler(body)
            except Exception:
                await asyncio.sleep(0.05)

