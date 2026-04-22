from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone

from bench_common import BenchmarkConfig, RunResult, format_bytes
from brokers_rabbitmq import run_rabbitmq_benchmark
from brokers_redis import run_redis_benchmark

# Same matrix as https://github.com/SGulsim/tech-2026/tree/main/tasks/22.04-broker/src/benchmark.ts

BASIC: list[BenchmarkConfig] = [
    BenchmarkConfig("rabbitmq", 1024, 1000, 20, "Basic 1KB @1k/s"),
    BenchmarkConfig("redis", 1024, 1000, 20, "Basic 1KB @1k/s"),
]

SIZE: list[BenchmarkConfig] = []
for size in (128, 1024, 10_240, 102_400):
    label = f"{format_bytes(int(size))} @1k/s"
    SIZE.append(BenchmarkConfig("rabbitmq", int(size), 1000, 15, label))
    SIZE.append(BenchmarkConfig("redis", int(size), 1000, 15, label))

RATE: list[BenchmarkConfig] = []
for rate in (1000, 5000, 10_000, 20_000, 0):
    label = f"1KB @{rate if rate else 'MAX'}/s"
    RATE.append(BenchmarkConfig("rabbitmq", 1024, int(rate), 15, label))
    RATE.append(BenchmarkConfig("redis", 1024, int(rate), 15, label))

EXPERIMENTS: dict[str, tuple[str, list[BenchmarkConfig]]] = {
    "basic": ("Experiment 1 - Basic comparison (1KB, 1000/s)", BASIC),
    "size": ("Experiment 2 - Message size impact (1000/s fixed)", SIZE),
    "rate": ("Experiment 3 - Rate intensity (1KB fixed)", RATE),
}


def _ts_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


async def _run_one_config(cfg: BenchmarkConfig) -> RunResult:
    if cfg.broker == "rabbitmq":
        return await run_rabbitmq_benchmark(cfg)
    if cfg.broker == "redis":
        return await run_redis_benchmark(cfg)
    raise ValueError(cfg.broker)


async def _run_with_timeout(cfg: BenchmarkConfig) -> RunResult:
    tmo = max(120.0, float(cfg.duration_seconds) * 3.0 + 90.0)
    return await asyncio.wait_for(_run_one_config(cfg), timeout=tmo)


def print_table(results: list[RunResult], title: str) -> None:
    w = 120
    print("\n" + "=" * w)
    print(" " + title)
    print("=" * w)
    print(
        f"{'Broker':9} | {'Label':32} | {'Size':7} | {'Tgt/s':5} | {'Sent':7} | {'Recv':7} | "
        f"{'Lost':5} | {'Err':4} | {'Act/s':6} | {'Avg':5} | {'p95':5} | {'Max':5}"
    )
    print("-" * w)
    for r in results:
        tgt = "MAX" if r.rate == 0 else str(r.rate)
        err = r.send_errors + r.recv_errors
        print(
            f"{r.broker:9} | {r.label[:32]:32} | {format_bytes(r.payload_bytes):>7} | {tgt:>5} | "
            f"{r.sent:7d} | {r.received:7d} | {r.lost:5d} | {err:4d} | "
            f"{r.recv_msg_per_sec:6.1f} | {r.avg_ms:5.1f} | {r.p95_ms:5.1f} | {r.max_ms:5.0f}"
        )
    print("=" * w + "\n")


def write_results_csv(path: str, results: list[RunResult]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = [asdict(r) for r in results]
    fieldnames = [f.name for f in fields(RunResult)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def write_json(path: str, results: list[RunResult]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = [asdict(r) for r in results]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Results JSON -> {path}")


def write_report_md(path: str, results: list[RunResult], title: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines: list[str] = [f"## {title}\n", "### Сводная таблица\n"]
    lines.append(
        "| broker | label | size | target/s | sent | received | lost | err | actual recv/s | "
        "avg ms | p95 ms | max ms | backlog (Rabbit) |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        tgt = "MAX" if r.rate == 0 else str(r.rate)
        b = r.backlog if r.backlog is not None else ""
        err = r.send_errors + r.recv_errors
        lines.append(
            f"| {r.broker} | {r.label} | {format_bytes(r.payload_bytes)} | {tgt} | {r.sent} | {r.received} | "
            f"{r.lost} | {err} | {r.recv_msg_per_sec:.1f} | {r.avg_ms:.1f} | {r.p95_ms:.1f} | {r.max_ms:.1f} | {b} |"
        )
    lines.append("\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


async def run_experiment(
    name: str,
    *,
    settle_sec: float = 2.5,
) -> list[RunResult]:
    if name not in EXPERIMENTS:
        raise ValueError("unknown experiment; try: basic, size, rate, or all")
    title, cfgs = EXPERIMENTS[name]
    print(f"\n{'=' * 60}\n {title}\n{'=' * 60}\n")
    out: list[RunResult] = []
    for i, cfg in enumerate(cfgs, start=1):
        t0 = time.time()
        label = f"[{cfg.broker.upper()}] {cfg.label}"
        print(f"\n>> [{i}/{len(cfgs)}] Starting: {label}", flush=True)
        r = await _run_with_timeout(cfg)
        out.append(r)
        dt = time.time() - t0
        print(
            f"OK ({dt:.1f}s): {label} recv={r.received} lost={r.lost} "
            f"act/s={r.recv_msg_per_sec} avg={r.avg_ms}ms p95={r.p95_ms}ms"
        )
        if i < len(cfgs):
            await asyncio.sleep(settle_sec)
    print_table(out, title)
    return out


async def _main_experiments(which: str) -> None:
    os.makedirs("results", exist_ok=True)
    if which == "all":
        all_r: list[RunResult] = []
        for name in ("basic", "size", "rate"):
            all_r.extend(await run_experiment(name))
            if name != "rate":
                await asyncio.sleep(2.0)
        print_table(all_r, "FULL SUMMARY")
        ts = _ts_filename()
        write_results_csv("results/results.csv", all_r)
        write_report_md("results/report.md", all_r, "FULL SUMMARY (basic + size + rate)")
        write_json(f"results/results-{ts}.json", all_r)
    else:
        r = await run_experiment(which)
        ts = _ts_filename()
        write_results_csv("results/results.csv", r)
        write_report_md("results/report.md", r, EXPERIMENTS[which][0])
        write_json(f"results/results-{which}-{ts}.json", r)


def _legacy_experiment(
    brokers: list[str], payload_bytes: list[int], rates: list[int], duration_sec: float, out_dir: str, runs: int
) -> None:
    results_all: list[RunResult] = []
    nruns = max(1, int(runs))
    for run_idx in range(1, nruns + 1):
        d = out_dir
        if nruns > 1:
            d = os.path.join(out_dir, f"results_run{run_idx}")
        one: list[RunResult] = []
        for broker in brokers:
            for pb in payload_bytes:
                for rate in rates:
                    print(f"[legacy] run{run_idx}/{nruns} {broker} payload={pb} rate={rate}", flush=True)
                    cfg = BenchmarkConfig(broker, int(pb), int(rate), float(duration_sec), f"legacy {pb}B@{rate}/s")
                    r = asyncio.run(_run_with_timeout(cfg))
                    one.append(r)
        write_results_csv(os.path.join(d, "results.csv"), one)
        write_report_md(os.path.join(d, "report.md"), one, f"legacy suite run {run_idx}")
        print(f"wrote {d}/results.csv and report.md")
        results_all.extend(one)
    write_json(os.path.join(out_dir, f"legacy-{_ts_filename()}.json"), results_all)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="RabbitMQ vs Redis: same experiments as tasks/22.04-broker (TypeScript / bash runner)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("all", help="Run basic + size + rate, write results/results.csv and JSON (full)")

    for name in ("basic", "size", "rate"):
        sub.add_parser(name, help=f"Run only {name} (see reference benchmark.ts)")

    run = sub.add_parser("run", help="Arbitrary one-off (same message format and batching as reference)")
    run.add_argument("--broker", choices=["rabbitmq", "redis"], required=True)
    run.add_argument("--payload-bytes", type=int, required=True)
    run.add_argument("--rate", type=int, required=True, help="0 = MAX / unlimited (500 msgs per 50ms cap)")
    run.add_argument("--duration-sec", type=float, required=True)
    run.add_argument("--label", type=str, default="custom run")

    suite = sub.add_parser(
        "legacy-suite",
        help="Old matrix: 4×3×2 brokers; not the classmate’s three experiments (basic/size/rate).",
    )
    suite.add_argument("--brokers", nargs="+", default=["rabbitmq", "redis"], choices=["rabbitmq", "redis"])
    suite.add_argument("--payload-bytes", nargs="+", type=int, default=[128, 1024, 10_240, 102_400])
    suite.add_argument("--rates", nargs="+", type=int, default=[1000, 5000, 10_000])
    suite.add_argument("--duration-sec", type=float, default=10.0)
    suite.add_argument("--out-dir", default="results")
    suite.add_argument("--runs", type=int, default=1)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.cmd == "legacy-suite":
        _legacy_experiment(
            list(args.brokers),
            list(args.payload_bytes),
            list(args.rates),
            float(args.duration_sec),
            args.out_dir,
            int(args.runs),
        )
        return
    if args.cmd == "run":
        c = BenchmarkConfig(
            args.broker, int(args.payload_bytes), int(args.rate), float(args.duration_sec), str(args.label)
        )
        r = asyncio.run(_run_with_timeout(c))
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2))
        return
    if args.cmd in ("basic", "size", "rate", "all"):
        asyncio.run(_main_experiments(args.cmd))
        return
    raise RuntimeError(f"unhandled: {args.cmd}")


if __name__ == "__main__":
    main()
