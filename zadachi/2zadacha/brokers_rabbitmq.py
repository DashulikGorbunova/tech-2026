from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aio_pika


@dataclass
class RabbitConfig:
    url: str = "amqp://guest:guest@localhost:5672/"
    queue_name: str = "bench_queue"
    connect_timeout_sec: float = 30.0


async def rabbit_setup(cfg: RabbitConfig) -> None:
    connection = await aio_pika.connect_robust(cfg.url, timeout=cfg.connect_timeout_sec)
    try:
        channel = await connection.channel()
        # non-durable for "single instance throughput" focus
        await channel.declare_queue(cfg.queue_name, durable=False, auto_delete=False)
    finally:
        await connection.close()


async def rabbit_purge(cfg: RabbitConfig) -> None:
    connection = await aio_pika.connect_robust(cfg.url, timeout=cfg.connect_timeout_sec)
    try:
        channel = await connection.channel()
        q = await channel.declare_queue(cfg.queue_name, durable=False, auto_delete=False)
        await q.purge()
    finally:
        await connection.close()


async def rabbit_backlog(cfg: RabbitConfig) -> int | None:
    connection = await aio_pika.connect_robust(cfg.url, timeout=cfg.connect_timeout_sec)
    try:
        channel = await connection.channel()
        q = await channel.declare_queue(cfg.queue_name, durable=False, auto_delete=False, passive=True)
        return int(q.declaration_result.message_count)
    except Exception:
        return None
    finally:
        await connection.close()


class RabbitProducer:
    def __init__(self, cfg: RabbitConfig):
        self.cfg = cfg
        self._conn: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.RobustChannel | None = None
        self._exchange: aio_pika.RobustExchange | None = None

    async def __aenter__(self) -> "RabbitProducer":
        self._conn = await aio_pika.connect_robust(self.cfg.url, timeout=self.cfg.connect_timeout_sec)
        self._channel = await self._conn.channel(publisher_confirms=False)
        self._exchange = self._channel.default_exchange
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def send(self, body: bytes) -> None:
        if self._exchange is None:
            raise RuntimeError("producer not started")
        msg = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT)
        await self._exchange.publish(msg, routing_key=self.cfg.queue_name)


class RabbitConsumer:
    def __init__(self, cfg: RabbitConfig):
        self.cfg = cfg
        self._conn: aio_pika.RobustConnection | None = None
        self._channel: aio_pika.RobustChannel | None = None
        self._queue: aio_pika.RobustQueue | None = None

    async def __aenter__(self) -> "RabbitConsumer":
        self._conn = await aio_pika.connect_robust(self.cfg.url, timeout=self.cfg.connect_timeout_sec)
        self._channel = await self._conn.channel()
        self._queue = await self._channel.declare_queue(self.cfg.queue_name, durable=False, auto_delete=False)
        await self._channel.set_qos(prefetch_count=200)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            await self._conn.close()

    async def consume_loop(self, handler, stop_event: asyncio.Event) -> None:
        if self._queue is None:
            raise RuntimeError("consumer not started")

        async with self._queue.iterator() as it:
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(it.__anext__(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    break

                try:
                    await handler(message.body)
                    await message.ack()
                except Exception:
                    try:
                        await message.nack(requeue=True)
                    except Exception:
                        pass

