from __future__ import annotations

import asyncio
import json
import os
import time

import aio_pika
from aio_pika import DeliveryMode, IncomingMessage, Message

from bench_common import BenchmarkConfig, build_run_result, encode_test_message, generate_payload, make_test_message

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
QUEUE = "benchmark"

BATCH_INTERVAL_MS = 50
BATCHES_PER_SEC = 1000 / BATCH_INTERVAL_MS


async def run_rabbitmq_benchmark(
    config: BenchmarkConfig,
    *,
    connect_timeout_sec: float = 30.0,
):
    if config.broker != "rabbitmq":
        raise ValueError("wrong broker for run_rabbitmq_benchmark")

    payload = generate_payload(config.message_size_bytes)
    msgs_per_batch = 500 if config.target_rate_per_sec == 0 else max(1, round(config.target_rate_per_sec / BATCHES_PER_SEC))

    sent = 0
    received = 0
    send_errors = 0
    recv_errors = 0
    latencies: list[float] = []

    connection = await aio_pika.connect(RABBITMQ_URL, timeout=connect_timeout_sec)
    producer_ch = await connection.channel()
    consumer_ch = await connection.channel()
    default_exchange = producer_ch.default_exchange

    await producer_ch.declare_queue(QUEUE, durable=False)
    await producer_ch.purge_queue(QUEUE)
    await consumer_ch.set_qos(prefetch_count=200)
    queue = await consumer_ch.declare_queue(QUEUE, durable=False)

    async def on_message(msg: IncomingMessage) -> None:
        nonlocal received, recv_errors
        if msg is None:
            return
        try:
            data = json.loads(msg.body.decode("utf-8"))
            end_to_end = float(time.time() * 1000 - int(data["sentAt"]))
            latencies.append(end_to_end)
            received += 1
            await msg.ack()
        except Exception:
            recv_errors += 1
            try:
                await msg.nack(requeue=False)
            except Exception:
                pass

    await queue.consume(on_message, no_ack=False)

    start_time_ms = int(time.time() * 1000)
    end_time_ms = start_time_ms + int(config.duration_seconds * 1000)

    while int(time.time() * 1000) < end_time_ms:
        batch_start_ms = int(time.time() * 1000)
        count = min(msgs_per_batch, 500)
        for _i in range(count):
            if int(time.time() * 1000) >= end_time_ms:
                break
            msg = make_test_message(payload=payload)
            body = encode_test_message(msg)
            try:
                m = Message(body=body, delivery_mode=DeliveryMode.NOT_PERSISTENT)
                await default_exchange.publish(m, routing_key=QUEUE)
                sent += 1
            except Exception:
                send_errors += 1
        if config.target_rate_per_sec > 0:
            wait = BATCH_INTERVAL_MS - (int(time.time() * 1000) - batch_start_ms)
            if wait > 0:
                await asyncio.sleep(wait / 1000.0)

    producer_end_ms = int(time.time() * 1000)
    producer_duration_ms = float(producer_end_ms - start_time_ms)

    grace_ms = min(5000, int(config.duration_seconds * 300))
    sys.stdout.write(f" [RabbitMQ] sent={sent}, waiting {grace_ms}ms for consumer...\n")
    sys.stdout.flush()
    if grace_ms > 0:
        await asyncio.sleep(grace_ms / 1000.0)

    # Backlog: messages still in the broker queue after the fixed grace window
    back_ch = await connection.channel()
    q_info = await back_ch.declare_queue(QUEUE, durable=False, passive=True)
    backlog = int(q_info.declaration_result.message_count)
    await back_ch.close()

    await connection.close()

    return build_run_result(
        config=config,
        sent=sent,
        received=received,
        send_errors=send_errors,
        recv_errors=recv_errors,
        latencies_ms=latencies,
        producer_duration_ms=producer_duration_ms,
        backlog=backlog,
    )
