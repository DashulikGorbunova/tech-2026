from __future__ import annotations

import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass


def now_ms() -> int:
    return int(time.time() * 1000)


def generate_payload(size_bytes: int) -> str:
    return "x" * max(1, int(size_bytes))


@dataclass(frozen=True)
class BenchmarkConfig:
    """Same shape as the reference (tasks/22.04-broker/src/types.ts + benchmark matrix)."""

    broker: str  # "rabbitmq" | "redis"
    message_size_bytes: int
    target_rate_per_sec: int  # 0 = unlimited (as fast as possible, capped per batch in runner)
    duration_seconds: float
    label: str


def make_test_message(*, payload: str) -> dict:
    return {"id": str(uuid.uuid4()), "sentAt": now_ms(), "payload": payload}


def encode_test_message(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def ensure_dir(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


@dataclass
class RunResult:
    broker: str
    label: str
    payload_bytes: int
    rate: int
    duration_sec: float
    producer_duration_sec: float
    sent: int
    received: int
    send_errors: int
    recv_errors: int
    lost: int
    avg_ms: float
    p95_ms: float
    max_ms: float
    # Reference-style “actual” rates use producer phase duration, not the configured duration.
    recv_msg_per_sec: float
    sent_msg_per_sec: float
    backlog: int | None = None

    def to_csv_row(self) -> dict:
        d = asdict(self)
        return d


def build_run_result(
    *,
    config: BenchmarkConfig,
    sent: int,
    received: int,
    send_errors: int,
    recv_errors: int,
    latencies_ms: list[float],
    producer_duration_ms: float,
    backlog: int | None = None,
) -> RunResult:
    sorted_lats = sorted(latencies_ms)
    avg = sum(sorted_lats) / len(sorted_lats) if sorted_lats else 0.0
    p95 = _percentile_nearest(sorted_lats, 95.0) if sorted_lats else 0.0
    max_ms = float(sorted_lats[-1]) if sorted_lats else 0.0
    pds = max(1e-6, producer_duration_ms / 1000.0)
    lost = max(0, int(sent - received))
    return RunResult(
        broker=config.broker,
        label=config.label,
        payload_bytes=config.message_size_bytes,
        rate=int(config.target_rate_per_sec),
        duration_sec=float(config.duration_seconds),
        producer_duration_sec=producer_duration_ms / 1000.0,
        sent=sent,
        received=received,
        send_errors=send_errors,
        recv_errors=recv_errors,
        lost=lost,
        avg_ms=round(avg, 1),
        p95_ms=round(p95, 1),
        max_ms=round(max_ms, 1),
        recv_msg_per_sec=round(received / pds, 1),
        sent_msg_per_sec=round(sent / pds, 1),
        backlog=backlog,
    )


def _percentile_nearest(values_sorted: list[float], p: float) -> float:
    """Match utils.ts: idx = ceil((p/100)*n) - 1, clamped."""
    if not values_sorted:
        return 0.0
    n = len(values_sorted)
    idx = int(math.ceil((p / 100.0) * n) - 1)
    idx = max(0, min(idx, n - 1))
    return float(values_sorted[idx])


def format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{(n / 1024.0):.0f}KB"
    return f"{(n / (1024.0 * 1024.0)):.1f}MB"
