from __future__ import annotations

import base64
import json
import os
import time
import uuid
from dataclasses import dataclass


def now_ns() -> int:
    return time.time_ns()


def b64encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode_bytes(data_b64: str) -> bytes:
    return base64.b64decode(data_b64.encode("ascii"))


def make_payload(payload_bytes: int) -> bytes:
    # deterministic bytes (stable size, low CPU)
    if payload_bytes <= 0:
        return b""
    chunk = (b"0123456789abcdef" * ((payload_bytes // 16) + 1))[:payload_bytes]
    return bytes(chunk)


def make_message(payload: bytes) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "sent_ts_ns": now_ns(),
        "payload_b64": b64encode_bytes(payload),
    }


def encode_message(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def decode_message(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


@dataclass
class RunResult:
    broker: str
    payload_bytes: int
    rate: int
    duration_sec: float
    sent: int
    received: int
    send_errors: int
    recv_errors: int
    avg_ms: float
    p95_ms: float
    max_ms: float
    recv_msg_per_sec: float
    sent_msg_per_sec: float
    backlog: int | None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if p <= 0:
        return float(min(values))
    if p >= 100:
        return float(max(values))
    values_sorted = sorted(values)
    k = (len(values_sorted) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(values_sorted) - 1)
    if f == c:
        return float(values_sorted[f])
    d0 = values_sorted[f] * (c - k)
    d1 = values_sorted[c] * (k - f)
    return float(d0 + d1)

