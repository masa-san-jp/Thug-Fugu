#!/usr/bin/env python3
"""Measure throughput, latency, and failover-degradation for a model_pool.

This measures **performance only** -- throughput (req/s), latency
percentiles, and request success rate, optionally comparing a healthy
baseline against a run with one pool member simulated as stopped. It does
**not** measure answer quality/accuracy in any way, and its output must
never be read as evidence for or against the "multiple coordinated models
answer better" hypothesis (that is WP-1/WP-6/WP-7's job). This script only
speaks to the separate "cheap machines, pooled" performance/cost hypothesis
(WP-8, docs/plans/phase2-decision-implementation-plan.md).
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import socket
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional

from fugu_local.backends import ChatMessage, ChatRequest, ModelConfig, build_backend
from fugu_local.config import ModelPoolConfig, load_config
from fugu_local.routing import ModelRouter, RouterMember

SCHEMA_VERSION = 1
NOTE = (
    "Throughput, latency, and request success-rate only. This is a "
    "performance/degradation benchmark, NOT a quality/accuracy measurement -- "
    "see docs/operations/multi-node-benchmark.md."
)


@dataclass(frozen=True)
class LevelResult:
    concurrency: int
    total_requests: int
    successes: int
    failures: int
    success_rate_pct: float
    throughput_rps: float
    latency_p50_ms: Optional[float]
    latency_p95_ms: Optional[float]
    latency_p99_ms: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="fugu-local config JSON path")
    parser.add_argument("--pool", required=True, help="model_pools[].name to benchmark")
    parser.add_argument(
        "--concurrency-levels",
        default="1,2,4",
        help="comma-separated list of concurrent request counts, e.g. '1,2,4,8'",
    )
    parser.add_argument(
        "--requests-per-level",
        type=int,
        default=20,
        help="total requests fired at each concurrency level",
    )
    parser.add_argument("--prompt", default="ping", help="prompt text sent on every request")
    parser.add_argument(
        "--outage-member",
        default=None,
        help=(
            "one of the pool's endpoint URLs to simulate as stopped after the "
            "baseline sweep, producing a second 'degraded' sweep for the "
            "success-rate/latency degradation curve"
        ),
    )
    parser.add_argument(
        "--hardware-json",
        type=Path,
        default=None,
        help="manual hardware/power metadata to embed verbatim (never auto-measured)",
    )
    parser.add_argument("--output", required=True, type=Path, help="output JSON path")
    args = parser.parse_args(argv)

    concurrency_levels = _parse_levels(args.concurrency_levels)
    if args.requests_per_level < 1:
        parser.error("--requests-per-level must be positive")

    config = load_config(str(args.config))
    pool = next((p for p in config.model_pools if p.name == args.pool), None)
    if pool is None:
        parser.error(f"model_pools[].name '{args.pool}' not found in {args.config}")

    router = _build_pool_router(pool)
    call_fn = _make_call_fn(router, args.prompt)

    result = {
        "schema_version": SCHEMA_VERSION,
        "note": NOTE,
        "config": str(args.config),
        "pool": pool.name,
        "concurrency_levels": concurrency_levels,
        "requests_per_level": args.requests_per_level,
        "hardware": _load_hardware(args.hardware_json) if args.hardware_json else _auto_hardware(),
        "conditions": {
            "baseline": [
                level.to_dict()
                for level in run_concurrency_sweep(
                    call_fn,
                    concurrency_levels=concurrency_levels,
                    requests_per_level=args.requests_per_level,
                )
            ]
        },
    }

    if args.outage_member:
        inject_member_outage(router, args.outage_member)
        result["outage_member"] = _safe_endpoint(args.outage_member)
        result["conditions"]["degraded"] = [
            level.to_dict()
            for level in run_concurrency_sweep(
                call_fn,
                concurrency_levels=concurrency_levels,
                requests_per_level=args.requests_per_level,
            )
        ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _print_summary(result)
    return 0


def _build_pool_router(pool: ModelPoolConfig) -> ModelRouter:
    members = [
        RouterMember(
            key=base_url,
            backend=build_backend(
                ModelConfig(
                    name=f"{pool.name}@{base_url}",
                    backend=pool.backend,
                    model=pool.model,
                    base_url=base_url,
                    api_key=pool.api_key,
                    timeout_seconds=pool.timeout_seconds,
                )
            ),
        )
        for base_url in pool.endpoints
    ]
    return ModelRouter(
        pool.model,
        members,
        policy=pool.policy,
        cooldown_seconds=pool.cooldown_seconds,
        active_health_enabled=pool.health.enabled,
        health_failure_threshold=pool.health.failure_threshold,
        health_success_threshold=pool.health.success_threshold,
    )


def _make_call_fn(router: ModelRouter, prompt: str) -> Callable[[], None]:
    def call_fn() -> None:
        router.chat(
            ChatRequest(
                model=router.model_string,
                messages=[ChatMessage(role="user", content=prompt)],
                temperature=0.0,
                max_tokens=16,
            )
        )

    return call_fn


def inject_member_outage(router: ModelRouter, member_key: str) -> None:
    """Simulate one pool member being stopped: subsequent calls routed to it
    raise instead of reaching a real (or stub) backend. Router-level failover
    to other members, if any, is unaffected -- that is the point."""

    member = next((m for m in router.members if m.key == member_key), None)
    if member is None:
        raise ValueError(f"unknown router member: {member_key}")
    member.backend = _OutageBackend()


class _OutageBackend:
    """Backend stand-in for a simulated member outage. Every call fails."""

    def chat(self, request: ChatRequest):
        raise ConnectionError("simulated member outage (benchmark_cluster --outage-member)")


def run_concurrency_sweep(
    call_fn: Callable[[], None],
    *,
    concurrency_levels: List[int],
    requests_per_level: int,
) -> List[LevelResult]:
    return [
        run_benchmark_level(call_fn, concurrency=level, total_requests=requests_per_level)
        for level in concurrency_levels
    ]


def run_benchmark_level(
    call_fn: Callable[[], None],
    *,
    concurrency: int,
    total_requests: int,
) -> LevelResult:
    """Fire ``total_requests`` calls to ``call_fn`` across ``concurrency``
    worker threads and report throughput, latency percentiles, and success
    rate. ``call_fn`` is expected to raise on failure and return normally on
    success; this function never raises for an individual call failure."""

    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    if total_requests < 1:
        raise ValueError("total_requests must be positive")

    counts = [n for n in _split_evenly(total_requests, concurrency) if n > 0]
    outcomes: List[tuple] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        local = []
        for _ in range(n):
            start = time.perf_counter()
            try:
                call_fn()
                ok = True
            except Exception:  # noqa: BLE001 - any failure counts as a failed request
                ok = False
            local.append((ok, (time.perf_counter() - start) * 1000.0))
        with lock:
            outcomes.extend(local)

    start_wall = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(n,)) for n in counts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall_seconds = time.perf_counter() - start_wall

    successes = sum(1 for ok, _ in outcomes if ok)
    failures = len(outcomes) - successes
    latencies_ms = [ms for ok, ms in outcomes if ok]

    return LevelResult(
        concurrency=concurrency,
        total_requests=len(outcomes),
        successes=successes,
        failures=failures,
        success_rate_pct=round(100.0 * successes / len(outcomes), 2) if outcomes else 0.0,
        throughput_rps=round(len(outcomes) / wall_seconds, 3) if wall_seconds > 0 else 0.0,
        latency_p50_ms=_percentile_ms(latencies_ms, 50),
        latency_p95_ms=_percentile_ms(latencies_ms, 95),
        latency_p99_ms=_percentile_ms(latencies_ms, 99),
    )


def _split_evenly(total: int, parts: int) -> List[int]:
    base, remainder = divmod(total, parts)
    return [base + (1 if i < remainder else 0) for i in range(parts)]


def _percentile_ms(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; no numpy/scipy dependency."""

    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return round(ordered[index], 3)


def _safe_endpoint(value: str) -> str:
    """Drop URL credentials, query strings, and fragments before writing an
    endpoint identifier into the output JSON."""

    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme or not parsed.hostname:
        return parsed.path or value
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def _parse_levels(raw: str) -> List[int]:
    levels = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item)
        if value < 1:
            raise ValueError(f"concurrency level must be positive, got {value}")
        levels.append(value)
    if not levels:
        raise ValueError("--concurrency-levels must contain at least one value")
    return levels


def _load_hardware(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("--hardware-json must contain a JSON object")
    return {"source": "provided", **payload}


def _auto_hardware() -> dict:
    return {
        "source": "auto",
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "note": "Provide --hardware-json for GPU/VRAM/RAM/power details.",
    }


def _print_summary(result: dict) -> None:
    print("Cluster benchmark summary")
    print("-------------------------")
    print(result["note"])
    for condition, levels in result["conditions"].items():
        for level in levels:
            print(
                f"{condition}: concurrency={level['concurrency']} "
                f"throughput={level['throughput_rps']}req/s "
                f"success_rate={level['success_rate_pct']}% "
                f"p50={level['latency_p50_ms']}ms "
                f"p95={level['latency_p95_ms']}ms "
                f"p99={level['latency_p99_ms']}ms"
            )


if __name__ == "__main__":
    raise SystemExit(main())
