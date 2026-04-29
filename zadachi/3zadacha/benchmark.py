import argparse
import csv
import random
import sqlite3
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import redis


@dataclass
class Metrics:
    strategy: str
    profile: str
    total_requests: int
    reads: int
    writes: int
    duration_sec: float
    throughput_rps: float
    avg_latency_ms: float
    db_reads: int
    db_writes: int
    cache_hits: int
    cache_misses: int
    cache_hit_rate: float
    max_write_back_queue: int = 0


class Database:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS items (k TEXT PRIMARY KEY, v TEXT NOT NULL)"
        )
        self.conn.commit()
        self._lock = threading.Lock()
        self.read_count = 0
        self.write_count = 0

    def reset_counters(self) -> None:
        self.read_count = 0
        self.write_count = 0

    def upsert_many(self, values: Dict[str, str]) -> None:
        with self._lock:
            self.conn.executemany(
                "INSERT INTO items(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                list(values.items()),
            )
            self.conn.commit()

    def read(self, key: str) -> Optional[str]:
        with self._lock:
            self.read_count += 1
            row = self.conn.execute("SELECT v FROM items WHERE k=?", (key,)).fetchone()
            return row[0] if row else None

    def write(self, key: str, value: str) -> None:
        with self._lock:
            self.write_count += 1
            self.conn.execute(
                "INSERT INTO items(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, value),
            )
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class InMemoryCache:
    def __init__(self) -> None:
        self.data: Dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)

    def clear(self) -> None:
        self.data.clear()


class RedisCache:
    def __init__(self, host: str, port: int, db: int = 0, prefix: str = "item:") -> None:
        self.client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.prefix = prefix
        self.client.ping()

    def _k(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Optional[str]:
        return self.client.get(self._k(key))

    def set(self, key: str, value: str) -> None:
        self.client.set(self._k(key), value)

    def delete(self, key: str) -> None:
        self.client.delete(self._k(key))

    def clear(self) -> None:
        for key in self.client.scan_iter(f"{self.prefix}*"):
            self.client.delete(key)


class App:
    def __init__(self, db: Database, cache: InMemoryCache | RedisCache, strategy: str) -> None:
        self.db = db
        self.cache = cache
        self.strategy = strategy
        self.cache_hits = 0
        self.cache_misses = 0
        self._wb_queue: deque[Tuple[str, str]] = deque()
        self._wb_lock = threading.Lock()
        self._wb_stop = threading.Event()
        self._max_wb_queue = 0
        self._flusher: Optional[threading.Thread] = None
        if strategy == "write-back":
            self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
            self._flusher.start()

    def _flush_loop(self) -> None:
        while not self._wb_stop.is_set():
            item: Optional[Tuple[str, str]] = None
            with self._wb_lock:
                if self._wb_queue:
                    item = self._wb_queue.popleft()
            if item:
                self.db.write(item[0], item[1])
            else:
                time.sleep(0.002)

    def stop(self) -> None:
        if self.strategy != "write-back":
            return
        while True:
            with self._wb_lock:
                if not self._wb_queue:
                    break
            time.sleep(0.01)
        self._wb_stop.set()
        if self._flusher:
            self._flusher.join(timeout=1.0)

    @property
    def max_wb_queue(self) -> int:
        return self._max_wb_queue

    def read(self, key: str) -> Optional[str]:
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        self.cache_misses += 1
        value = self.db.read(key)
        if value is not None:
            self.cache.set(key, value)
        return value

    def write(self, key: str, value: str) -> None:
        if self.strategy == "cache-aside":
            self.db.write(key, value)
            self.cache.delete(key)
        elif self.strategy == "write-through":
            self.db.write(key, value)
            self.cache.set(key, value)
        elif self.strategy == "write-back":
            self.cache.set(key, value)
            with self._wb_lock:
                self._wb_queue.append((key, value))
                self._max_wb_queue = max(self._max_wb_queue, len(self._wb_queue))
        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")


def run_profile(
    *,
    db: Database,
    cache: InMemoryCache | RedisCache,
    strategy: str,
    profile_name: str,
    read_ratio: float,
    total_requests: int,
    keys: List[str],
    duration_sec: Optional[float],
) -> Metrics:
    app = App(db=db, cache=cache, strategy=strategy)
    latencies_ms: List[float] = []
    reads = 0
    writes = 0
    executed_requests = 0
    started = time.perf_counter()

    def one_request() -> None:
        nonlocal reads, writes, executed_requests
        key = random.choice(keys)
        do_read = random.random() < read_ratio
        t0 = time.perf_counter()
        if do_read:
            reads += 1
            app.read(key)
        else:
            writes += 1
            value = f"v-{random.randint(1, 1_000_000)}"
            app.write(key, value)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        executed_requests += 1

    if duration_sec is not None:
        while True:
            if (time.perf_counter() - started) >= duration_sec:
                break
            one_request()
    else:
        for _ in range(total_requests):
            one_request()

    app.stop()
    duration = time.perf_counter() - started
    throughput = executed_requests / duration if duration else 0.0
    cache_total = app.cache_hits + app.cache_misses
    hit_rate = (app.cache_hits / cache_total) if cache_total else 0.0

    return Metrics(
        strategy=strategy,
        profile=profile_name,
        total_requests=executed_requests,
        reads=reads,
        writes=writes,
        duration_sec=duration,
        throughput_rps=throughput,
        avg_latency_ms=statistics.fmean(latencies_ms) if latencies_ms else 0.0,
        db_reads=db.read_count,
        db_writes=db.write_count,
        cache_hits=app.cache_hits,
        cache_misses=app.cache_misses,
        cache_hit_rate=hit_rate,
        max_write_back_queue=app.max_wb_queue,
    )


def write_csv(path: Path, rows: List[Metrics]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "strategy",
                "profile",
                "total_requests",
                "reads",
                "writes",
                "duration_sec",
                "throughput_rps",
                "avg_latency_ms",
                "db_reads",
                "db_writes",
                "cache_hits",
                "cache_misses",
                "cache_hit_rate",
                "max_write_back_queue",
            ]
        )
        for m in rows:
            writer.writerow(
                [
                    m.strategy,
                    m.profile,
                    m.total_requests,
                    m.reads,
                    m.writes,
                    f"{m.duration_sec:.4f}",
                    f"{m.throughput_rps:.2f}",
                    f"{m.avg_latency_ms:.3f}",
                    m.db_reads,
                    m.db_writes,
                    m.cache_hits,
                    m.cache_misses,
                    f"{m.cache_hit_rate:.3f}",
                    m.max_write_back_queue,
                ]
            )


def write_report(path: Path, rows: List[Metrics], total_requests: int, key_count: int, duration_sec: Optional[float]) -> None:
    lines: List[str] = []
    lines.append("# Отчет: сравнение типов кеширования")
    lines.append("")
    lines.append("## Условия теста")
    lines.append("")
    lines.append(f"- Набор ключей: `{key_count}`")
    if duration_sec is not None:
        lines.append(f"- Длительность каждого профиля: `{duration_sec}` сек (фиксированная)")
    else:
        lines.append(f"- Запросов на профиль: `{total_requests}`")
    lines.append("- Профили: read-heavy 80/20, balanced 50/50, write-heavy 20/80")
    lines.append("- Измерения: throughput, средняя задержка, обращения в БД, cache hit rate")
    lines.append("")
    lines.append("## Результаты")
    lines.append("")
    lines.append(
        "| Strategy | Profile | Throughput req/s | Avg latency ms | DB reads | DB writes | Cache hit rate | WB queue max |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for m in rows:
        lines.append(
            f"| {m.strategy} | {m.profile} | {m.throughput_rps:.2f} | {m.avg_latency_ms:.3f} | "
            f"{m.db_reads} | {m.db_writes} | {m.cache_hit_rate:.3f} | {m.max_write_back_queue} |"
        )

    lines.append("")
    lines.append("## Краткие выводы")
    lines.append("")
    profiles = ["read-heavy", "balanced", "write-heavy"]
    for profile in profiles:
        candidates = [m for m in rows if m.profile == profile]
        winner = max(candidates, key=lambda x: x.throughput_rps)
        lines.append(
            f"- `{profile}`: лучший throughput у `{winner.strategy}` ({winner.throughput_rps:.2f} req/s), "
            f"avg latency {winner.avg_latency_ms:.3f} ms, hit rate {winner.cache_hit_rate:.3f}."
        )
    wb_rows = [m for m in rows if m.strategy == "write-back"]
    lines.append(
        f"- `write-back`: максимальная накопленная flush-очередь = `{max(m.max_write_back_queue for m in wb_rows)}`."
    )

    lines.append("")
    lines.append("## Логи")
    lines.append("")
    lines.append("- Лог последнего прогона на Redis: `results/console.log`.")
    lines.append("")
    lines.append("## Скриншоты консоли")
    lines.append("")
    lines.append("Прогон единого теста с Redis:")
    lines.append("")
    lines.append("![](./screens/01-run-log.png)")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache strategy benchmark")
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--db-file", default="benchmark.sqlite")
    parser.add_argument("--total-requests", type=int, default=15000)
    parser.add_argument("--duration-sec", type=float, default=None)
    parser.add_argument("--keys", type=int, default=1000)
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--print-live", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = Database(args.db_file)
    try:
        cache: InMemoryCache | RedisCache = RedisCache(host=args.redis_host, port=args.redis_port)
        print(f"Using Redis cache at {args.redis_host}:{args.redis_port}")
    except Exception:
        cache = InMemoryCache()
        print("Redis is unavailable. Using in-memory cache fallback.")

    keys = [f"k{i}" for i in range(args.keys)]
    seed_data = {k: f"seed-{i}" for i, k in enumerate(keys)}
    db.upsert_many(seed_data)

    profiles = [
        ("read-heavy", 0.80),
        ("balanced", 0.50),
        ("write-heavy", 0.20),
    ]
    strategies = ["cache-aside", "write-through", "write-back"]
    rows: List[Metrics] = []

    for strategy in strategies:
        for profile_name, read_ratio in profiles:
            cache.clear()
            db.reset_counters()
            metrics = run_profile(
                db=db,
                cache=cache,
                strategy=strategy,
                profile_name=profile_name,
                read_ratio=read_ratio,
                total_requests=args.total_requests,
                keys=keys,
                duration_sec=args.duration_sec,
            )
            rows.append(metrics)
            if args.print_live:
                print(
                    f"[{strategy:12}] {profile_name:11} "
                    f"thr={metrics.throughput_rps:8.2f} req/s  "
                    f"lat={metrics.avg_latency_ms:7.3f} ms  "
                    f"db(r/w)={metrics.db_reads}/{metrics.db_writes}  "
                    f"hit={metrics.cache_hit_rate:.3f}  wbq={metrics.max_write_back_queue}"
                )

    write_csv(out_dir / "results.csv", rows)
    write_report(out_dir / "report.md", rows, args.total_requests, args.keys, args.duration_sec)
    db.close()
    print(f"Done. Results are saved to {out_dir.resolve()}")


if __name__ == "__main__":
    main()
