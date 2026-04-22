from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import redis.asyncio as redis
from redis.exceptions import ResponseError

from bench_common import BenchmarkConfig, RunResult, build_run_result, generate_payload, make_test_message

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

STREAM = "benchmark-stream"
GROUP = "benchmark-group"
CONSUMER = "consumer-1"

BATCH_INTERVAL_MS = 50
BATCHES_PER_SEC = 1000 / BATCH_INTERVAL_MS


async def run_redis_benchmark(config: BenchmarkConfig) -> RunResult:
    if config.broker != "redis":
        raise ValueError("wrong broker for run_redis_benchmark")

    producer = redis.from_url(REDIS_URL, decode_responses=True)
    consumer = redis.from_url(REDIS_URL, decode_responses=True)
    await producer.connect()
    await consumer.connect()

    await producer.delete(STREAM)
    try:
        await producer.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e).upper():
            raise

    sent = 0
    received = 0
    errors = 0
    latencies: list[float] = []
    stopped = False

    async def consumer_loop() -> None:
        nonlocal received, errors, stopped
        while not stopped:
            try:
                res = await consumer.xreadgroup(
                    groupname=GROUP,
                    consumername=CONSUMER,
                    streams={STREAM: ">"},
                    count=200,
                    block=100,
                )
                if not res:
                    continue
                for _stream, messages in res:
                    ids: list[str] = []
                    for msg_id, fields in messages:
                        if not isinstance(fields, dict):
                            continue
                        raw = fields.get("data")
                        if raw is None:
                            errors += 1
                            continue
                        try:
                            s = raw if isinstance(raw, str) else str(raw, "utf-8", "replace")
                            data = json.loads(s)
                            latencies.append(float(time.time() * 1000 - int(data["sentAt"])))
                            received += 1
                            ids.append(str(msg_id))
                        except Exception:
                            errors += 1
                    if ids:
                        await consumer.xack(STREAM, GROUP, *ids)
            except Exception:
                if not stopped:
                    await asyncio.sleep(0.01)

    consumer_task = asyncio.create_task(consumer_loop())

    payload = generate_payload(config.message_size_bytes)
    msgs_per_batch = 500 if config.target_rate_per_sec == 0 else max(1, round(config.target_rate_per_sec / BATCHES_PER_SEC))

    start_time_ms = int(time.time() * 1000)
    end_time_ms = start_time_ms + int(config.duration_seconds * 1000)

    while int(time.time() * 1000) < end_time_ms:
        batch_start_ms = int(time.time() * 1000)
        this_batch = min(msgs_per_batch, 500)
        pipe = producer.pipeline()
        n = 0
        for _i in range(this_batch):
            if int(time.time() * 1000) >= end_time_ms:
                break
            m = make_test_message(payload=payload)
            body = json.dumps(m, separators=(",", ":"), ensure_ascii=False)
            pipe.xadd(STREAM, {"data": body})
            n += 1
            sent += 1
        if n:
            try:
                await pipe.execute()
            except Exception:
                errors += n
                sent -= n
        if config.target_rate_per_sec > 0:
            wait = BATCH_INTERVAL_MS - (int(time.time() * 1000) - batch_start_ms)
            if wait > 0:
                await asyncio.sleep(wait / 1000.0)

    producer_end_ms = int(time.time() * 1000)
    producer_duration_ms = float(producer_end_ms - start_time_ms)

    grace_ms = min(5000, int(config.duration_seconds * 300))
    sys.stdout.write(f" [Redis] sent={sent}, waiting {grace_ms}ms for consumer...\n")
    sys.stdout.flush()
    if grace_ms > 0:
        await asyncio.sleep(grace_ms / 1000.0)

    stopped = True
    try:
        await consumer.aclose()
    except Exception:
        pass
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

    await producer.aclose()
    return build_run_result(
        config=config,
        sent=sent,
        received=received,
        send_errors=errors,
        recv_errors=0,
        latencies_ms=latencies,
        producer_duration_ms=producer_duration_ms,
        backlog=None,
    )
