from __future__ import annotations

import argparse
import asyncio
import csv
import math
import os
import statistics
import time
from dataclasses import asdict

from bench_common import RunResult, decode_message, encode_message, ensure_dir, make_message, make_payload, now_ns, percentile
from brokers_rabbitmq import RabbitConfig, RabbitConsumer, RabbitProducer, rabbit_backlog, rabbit_purge, rabbit_setup
from brokers_redis import RedisConfig, RedisConsumer, RedisProducer, redis_backlog, redis_purge, redis_setup


async def producer_loop(
    *,
    send_one,
    payload_bytes: int,
    rate: int,
    duration_sec: float,
    workers: int = 20,
    flush_timeout_sec: float = 10.0,
) -> tuple[int, int]:
    """
    Rate-limited scheduler + concurrent send workers.
    This reduces "await per message" bottlenecks and makes the target rate closer to reality.
    """
    payload = make_payload(payload_bytes)
    sent = 0
    errors = 0
    start_ns = now_ns()
    end_ns = start_ns + int(duration_sec * 1e9)

    q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=max(1, workers * 200))

    async def worker() -> None:
        nonlocal sent, errors
        while True:
            item = await q.get()
            try:
                if item is None:
                    return
                await send_one(item)
                sent += 1
            except Exception:
                errors += 1
            finally:
                q.task_done()

    worker_tasks = [asyncio.create_task(worker()) for _ in range(max(1, workers))]

    try:
        interval_ns = 0 if rate <= 0 else int(1e9 / rate)
        next_ns = start_ns

        while now_ns() < end_ns:
            if interval_ns > 0:
                now = now_ns()
                if now < next_ns:
                    await asyncio.sleep((next_ns - now) / 1e9)
                next_ns += interval_ns

            msg = make_message(payload)
            await q.put(encode_message(msg))

        # Flush remaining sends, but don't hang forever under overload/backpressure.
        try:
            await asyncio.wait_for(q.join(), timeout=flush_timeout_sec)
        except asyncio.TimeoutError:
            # items still in queue are treated as send failures (not published)
            try:
                remaining = q.qsize()
            except Exception:
                remaining = 0
            errors += int(max(0, remaining))
    finally:
        # Best-effort shutdown: under heavy backpressure a worker can hang inside send_one().
        # We cancel workers to guarantee producer termination.
        for _ in worker_tasks:
            try:
                await q.put(None)
            except Exception:
                break
        for t in worker_tasks:
            t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    return sent, errors


async def retry_async(fn, *, attempts: int = 20, base_delay_sec: float = 0.5, max_delay_sec: float = 3.0):
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:
            last_exc = e
            delay = min(max_delay_sec, base_delay_sec * (2**i))
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def run_one(*, broker: str, payload_bytes: int, rate: int, duration_sec: float) -> RunResult:
    lat_ms: list[float] = []
    recv_errors = 0
    received = 0

    async def handler(raw: bytes) -> None:
        nonlocal received, recv_errors
        try:
            msg = decode_message(raw)
            sent_ts_ns = int(msg["sent_ts_ns"])
            latency_ms = (now_ns() - sent_ts_ns) / 1e6
            lat_ms.append(float(latency_ms))
            received += 1
        except Exception:
            recv_errors += 1

    stop_event = asyncio.Event()
    # drain window: allow consumer to catch up after producer stops
    drain_deadline = time.time() + min(60.0, max(10.0, duration_sec * 5))

    if broker == "rabbitmq":
        cfg = RabbitConfig()
        await retry_async(lambda: rabbit_setup(cfg))
        await retry_async(lambda: rabbit_purge(cfg))
        async with RabbitProducer(cfg) as prod, RabbitConsumer(cfg) as cons:
            consumer_task = asyncio.create_task(cons.consume_loop(handler, stop_event))
            sent, send_errors = await producer_loop(
                send_one=prod.send,
                payload_bytes=payload_bytes,
                rate=rate,
                duration_sec=duration_sec,
            )
            # drain: give consumer time to catch up to avoid undercounting
            while time.time() < drain_deadline:
                backlog_snapshot = await retry_async(lambda: rabbit_backlog(cfg))
                if backlog_snapshot in (0, None):
                    break
                await asyncio.sleep(0.5)
            stop_event.set()
            await asyncio.wait_for(consumer_task, timeout=10)
            backlog = await retry_async(lambda: rabbit_backlog(cfg))
    elif broker == "redis":
        cfg = RedisConfig()
        await retry_async(lambda: redis_purge(cfg))
        async with RedisProducer(cfg) as prod, RedisConsumer(cfg) as cons:
            consumer_task = asyncio.create_task(cons.consume_loop(handler, stop_event))
            sent, send_errors = await producer_loop(
                send_one=prod.send,
                payload_bytes=payload_bytes,
                rate=rate,
                duration_sec=duration_sec,
            )
            while time.time() < drain_deadline:
                backlog_snapshot = await retry_async(lambda: redis_backlog(cfg))
                if backlog_snapshot in (0, None):
                    break
                await asyncio.sleep(0.5)
            stop_event.set()
            await asyncio.wait_for(consumer_task, timeout=10)
            backlog = await retry_async(lambda: redis_backlog(cfg))
    else:
        raise ValueError("unknown broker")

    avg_ms = statistics.fmean(lat_ms) if lat_ms else 0.0
    p95_ms = percentile(lat_ms, 95.0)
    max_ms = max(lat_ms) if lat_ms else 0.0

    recv_mps = received / duration_sec if duration_sec > 0 else 0.0
    sent_mps = sent / duration_sec if duration_sec > 0 else 0.0

    return RunResult(
        broker=broker,
        payload_bytes=payload_bytes,
        rate=rate,
        duration_sec=duration_sec,
        sent=sent,
        received=received,
        send_errors=send_errors,
        recv_errors=recv_errors,
        avg_ms=float(avg_ms),
        p95_ms=float(p95_ms),
        max_ms=float(max_ms),
        recv_msg_per_sec=float(recv_mps),
        sent_msg_per_sec=float(sent_mps),
        backlog=backlog,
    )


async def run_one_with_timeout(*, broker: str, payload_bytes: int, rate: int, duration_sec: float) -> RunResult:
    # hard-stop a run if it gets stuck (connection issues, broker restart, etc.)
    timeout_sec = duration_sec + min(30.0, max(5.0, duration_sec * 2)) + 90.0
    return await asyncio.wait_for(
        run_one(broker=broker, payload_bytes=payload_bytes, rate=rate, duration_sec=duration_sec),
        timeout=timeout_sec,
    )


def write_results_csv(path: str, results: list[RunResult]) -> None:
    ensure_dir(os.path.dirname(path))
    rows = [asdict(r) for r in results]
    fieldnames = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_report_md(path: str, results: list[RunResult]) -> None:
    ensure_dir(os.path.dirname(path))
    lines: list[str] = []
    lines.append("## Отчёт: сравнение RabbitMQ vs Redis\n")
    lines.append("### Результаты (сводная таблица)\n")
    lines.append("| broker | payload_bytes | rate (msg/s) | duration (s) | sent | received | lost | send_err | recv_err | sent_mps | recv_mps | avg_ms | p95_ms | max_ms | backlog |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        lost = max(0, int(r.sent - r.received))
        lines.append(
            f"| {r.broker} | {r.payload_bytes} | {r.rate} | {r.duration_sec:g} | {r.sent} | {r.received} | {lost} | {r.send_errors} | {r.recv_errors} | "
            f"{r.sent_msg_per_sec:.1f} | {r.recv_msg_per_sec:.1f} | {r.avg_ms:.2f} | {r.p95_ms:.2f} | {r.max_ms:.2f} | {r.backlog if r.backlog is not None else ''} |"
        )
    lines.append("\n### Выводы (заполнить)\n")
    lines.append("- **Пропускная способность**: ...\n")
    lines.append("- **Влияние размера сообщения**: ...\n")
    lines.append("- **Точка деградации single instance**: ...\n")
    lines.append("- **Инструмент для нагрузочного тестирования и почему**: ...\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="single run")
    run.add_argument("--broker", choices=["rabbitmq", "redis"], required=True)
    run.add_argument("--payload-bytes", type=int, required=True)
    run.add_argument("--rate", type=int, required=True, help="target msg/sec")
    run.add_argument("--duration-sec", type=float, default=10.0)

    suite = sub.add_parser("suite", help="matrix runs and produce report")
    suite.add_argument("--brokers", nargs="+", default=["rabbitmq", "redis"], choices=["rabbitmq", "redis"])
    suite.add_argument("--payload-bytes", nargs="+", type=int, default=[128, 1024, 10_240, 102_400])
    suite.add_argument("--rates", nargs="+", type=int, default=[1000, 5000, 10_000])
    suite.add_argument("--duration-sec", type=float, default=10.0)
    suite.add_argument("--out-dir", default="results")
    suite.add_argument("--runs", type=int, default=1, help="how many repeats (results_run1, results_run2, ...)")

    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.cmd == "run":
        r = asyncio.run(run_one(broker=args.broker, payload_bytes=args.payload_bytes, rate=args.rate, duration_sec=args.duration_sec))
        print(asdict(r))
        return

    if args.cmd == "suite":
        out_dir = args.out_dir
        runs = max(1, int(args.runs))

        for run_idx in range(1, runs + 1):
            run_dir = out_dir
            if runs > 1:
                run_dir = os.path.join(out_dir, f"results_run{run_idx}")

            results: list[RunResult] = []
            for broker in args.brokers:
                for pb in args.payload_bytes:
                    for rate in args.rates:
                        print(
                            f"[run {run_idx}/{runs}] broker={broker} payload_bytes={pb} rate={rate} duration={args.duration_sec}",
                            flush=True,
                        )
                        r = asyncio.run(
                            run_one_with_timeout(broker=broker, payload_bytes=pb, rate=rate, duration_sec=args.duration_sec)
                        )
                        results.append(r)

            csv_path = os.path.join(run_dir, "results.csv")
            md_path = os.path.join(run_dir, "report.md")
            write_results_csv(csv_path, results)
            write_report_md(md_path, results)
            print(f"wrote {csv_path}")
            print(f"wrote {md_path}")

        return

    raise RuntimeError("unknown command")


if __name__ == "__main__":
    main()

