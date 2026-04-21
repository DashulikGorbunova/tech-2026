from __future__ import annotations

import argparse
import csv
import os
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    broker: str
    payload_bytes: int
    rate: int


def read_csv(path: str) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def f(x: str) -> float:
    return float(x)


def i(x: str) -> int:
    return int(float(x))


def build_summary(rows: list[dict[str, str]]):
    grouped: dict[Key, list[dict[str, str]]] = {}
    for r in rows:
        k = Key(broker=r["broker"], payload_bytes=i(r["payload_bytes"]), rate=i(r["rate"]))
        grouped.setdefault(k, []).append(r)

    out = []
    for k, items in sorted(grouped.items(), key=lambda t: (t[0].broker, t[0].payload_bytes, t[0].rate)):
        recv_mps = [f(x["recv_msg_per_sec"]) for x in items]
        p95_ms = [f(x["p95_ms"]) for x in items]
        sent = [i(x["sent"]) for x in items]
        received = [i(x["received"]) for x in items]
        lost = [max(0, s - rcv) for s, rcv in zip(sent, received)]
        backlog = []
        for x in items:
            b = x.get("backlog") or ""
            if b.strip() == "":
                continue
            backlog.append(i(b))

        out.append(
            {
                "broker": k.broker,
                "payload_bytes": k.payload_bytes,
                "rate": k.rate,
                "runs": len(items),
                "recv_mps_avg": statistics.fmean(recv_mps),
                "recv_mps_min": min(recv_mps),
                "recv_mps_max": max(recv_mps),
                "p95_ms_avg": statistics.fmean(p95_ms),
                "p95_ms_min": min(p95_ms),
                "p95_ms_max": max(p95_ms),
                "lost_avg": statistics.fmean(lost),
                "lost_min": min(lost),
                "lost_max": max(lost),
                "backlog_avg": (statistics.fmean(backlog) if backlog else None),
                "backlog_min": (min(backlog) if backlog else None),
                "backlog_max": (max(backlog) if backlog else None),
            }
        )
    return out


def fmt_float(x: float) -> str:
    return f"{x:.1f}"


def fmt_ms(x: float) -> str:
    return f"{x:.0f}"


def fmt_int(x: float) -> str:
    return f"{int(round(x))}"


def fmt_opt_int(x) -> str:
    return "" if x is None else str(int(x))


def to_markdown(summary_rows: list[dict]) -> str:
    lines: list[str] = []
    lines.append("| broker | payload | rate | runs | recv_mps avg (min..max) | p95_ms avg (min..max) | lost avg (min..max) | backlog avg (min..max) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in summary_rows:
        lines.append(
            "| {broker} | {payload_bytes} | {rate} | {runs} | {recv_avg} ({recv_min}..{recv_max}) | {p95_avg} ({p95_min}..{p95_max}) | {lost_avg} ({lost_min}..{lost_max}) | {b_avg} ({b_min}..{b_max}) |".format(
                broker=r["broker"],
                payload_bytes=r["payload_bytes"],
                rate=r["rate"],
                runs=r["runs"],
                recv_avg=fmt_float(r["recv_mps_avg"]),
                recv_min=fmt_float(r["recv_mps_min"]),
                recv_max=fmt_float(r["recv_mps_max"]),
                p95_avg=fmt_ms(r["p95_ms_avg"]),
                p95_min=fmt_ms(r["p95_ms_min"]),
                p95_max=fmt_ms(r["p95_ms_max"]),
                lost_avg=fmt_int(r["lost_avg"]),
                lost_min=r["lost_min"],
                lost_max=r["lost_max"],
                b_avg=fmt_opt_int(r["backlog_avg"]),
                b_min=fmt_opt_int(r["backlog_min"]),
                b_max=fmt_opt_int(r["backlog_max"]),
            )
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        default="results_repeats_full",
        help="directory with results_runN subfolders",
    )
    ap.add_argument(
        "--out",
        default="",
        help="optional output .md path (UTF-8). If omitted, prints to stdout.",
    )
    args = ap.parse_args()

    root = args.root
    all_rows: list[dict[str, str]] = []

    # read results_run1/results.csv, results_run2/results.csv, ...
    for name in sorted(os.listdir(root)):
        if not name.startswith("results_run"):
            continue
        path = os.path.join(root, name, "results.csv")
        if not os.path.exists(path):
            continue
        all_rows.extend(read_csv(path))

    summary = build_summary(all_rows)
    md = to_markdown(summary) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(md)
    else:
        print(md, end="")


if __name__ == "__main__":
    main()

