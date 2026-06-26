#!/usr/bin/env python3
"""Benchmark Thug-Fugu role parallelism across one or more configs.

This is intentionally small and dependency-free. It runs the orchestrator against
one or more config files, prints per-run wall-clock timings, and optionally writes
CSV output for issue/PR evidence.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import time
from pathlib import Path
from typing import Iterable, Optional

from fugu_local.backends import ChatMessage
from fugu_local.config import load_config
from fugu_local.orchestrator import FuguLocalOrchestrator


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        required=True,
        help="Config path to benchmark. Repeat to compare baseline vs multi-GPU.",
    )
    parser.add_argument("--prompt", required=True, help="Prompt to send to each config")
    parser.add_argument("--runs", type=int, default=3, help="Runs per config")
    parser.add_argument("--csv", dest="csv_path", help="Optional CSV output path")
    args = parser.parse_args(argv)

    if args.runs <= 0:
        parser.error("--runs must be positive")

    rows = []
    for config_path in args.config:
        rows.extend(_benchmark_config(Path(config_path), args.prompt, args.runs))

    _print_summary(rows)
    if args.csv_path:
        _write_csv(Path(args.csv_path), rows)
    return 0


def _benchmark_config(config_path: Path, prompt: str, runs: int) -> list[dict]:
    config = load_config(str(config_path))
    rows = []
    for run_index in range(1, runs + 1):
        orchestrator = FuguLocalOrchestrator(config)
        started = time.perf_counter()
        result = orchestrator.chat([ChatMessage(role="user", content=prompt)])
        wall_ms = round((time.perf_counter() - started) * 1000, 1)
        workers = ";".join(
            f"{worker.role}:{worker.model}:ok={worker.ok}:latency_ms={worker.latency_ms}"
            for worker in result.worker_results
        )
        row = {
            "config": str(config_path),
            "run": run_index,
            "wall_ms": wall_ms,
            "orchestrator_latency_ms": result.latency_ms,
            "pattern": result.pattern,
            "selected_roles": ";".join(result.selected_roles),
            "synthesizer_role": result.synthesizer_role or "",
            "worker_count": len(result.worker_results),
            "workers": workers,
        }
        rows.append(row)
        print(
            f"{config_path} run={run_index} wall_ms={wall_ms} "
            f"pattern={result.pattern} roles={row['selected_roles']}"
        )
    return rows


def _print_summary(rows: Iterable[dict]) -> None:
    by_config: dict[str, list[float]] = {}
    for row in rows:
        by_config.setdefault(row["config"], []).append(float(row["wall_ms"]))
    print("\nSummary")
    print("-------")
    for config, values in by_config.items():
        mean = round(statistics.mean(values), 1)
        minimum = round(min(values), 1)
        maximum = round(max(values), 1)
        print(f"{config}: runs={len(values)} mean_ms={mean} min_ms={minimum} max_ms={maximum}")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "config",
        "run",
        "wall_ms",
        "orchestrator_latency_ms",
        "pattern",
        "selected_roles",
        "synthesizer_role",
        "worker_count",
        "workers",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote CSV: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
