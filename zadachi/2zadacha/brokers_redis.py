from __future__ import annotations

import asyncio
from dataclasses import dataclass

import redis.asyncio as redis


@dataclass
class RedisConfig:
    url: str = "redis://localhost:6379/0"
    stream: str = "bench_stream"
    group: str = "bench_group"
    consumer: str = "c1"


async def redis_setup(cfg: RedisConfig) -> None:
    r = redis.from_url(cfg.url, decode_responses=False)
    try:
        # create group (mkstream)
        try:
            await r.xgroup_create(name=cfg.stream, groupname=cfg.group, id="$", mkstream=True)
        except Exception as e:
            # BUSYGROUP is fine
            if "BUSYGROUP" not in str(e):
                raise
    finally:
        await r.aclose()


async def redis_purge(cfg: RedisConfig) -> None:
    r = redis.from_url(cfg.url, decode_responses=False)
    try:
        await r.delete(cfg.stream)
    finally:
        await r.aclose()


async def redis_backlog(cfg: RedisConfig) -> int | None:
    r = redis.from_url(cfg.url, decode_responses=False)
    try:
        info = await r.xinfo_stream(cfg.stream)
        # redis-py may return bytes or str keys depending on client config/version
        length = None
        if isinstance(info, dict):
            length = info.get(b"length")
            if length is None:
                length = info.get("length")  # type: ignore[arg-type]
        if length is None:
            return None
        return int(length)
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
        # store as field "body"
        await self._r.xadd(self.cfg.stream, fields={b"body": body}, maxlen=None, approximate=False)


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

        # ensure group exists (mkstream behavior)
        try:
            await self._r.xgroup_create(name=self.cfg.stream, groupname=self.cfg.group, id="$", mkstream=True)
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

        while not stop_event.is_set():
            try:
                resp = await self._r.xreadgroup(
                    groupname=self.cfg.group,
                    consumername=self.cfg.consumer,
                    streams={self.cfg.stream: b">"},
                    count=200,
                    block=1000,
                )
                if not resp:
                    continue
                # resp: [(stream, [(id, {field: value}), ...])]
                for _stream, entries in resp:
                    for entry_id, fields in entries:
                        body = fields.get(b"body", b"")
                        await handler(body)
                        await self._r.xack(self.cfg.stream, self.cfg.group, entry_id)
            except Exception:
                await asyncio.sleep(0.05)

