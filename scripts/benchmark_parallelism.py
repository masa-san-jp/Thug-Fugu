#!/usr/bin/env python3
"""Create and validate a dependency-free parallelism benchmark artifact.

The benchmark deliberately uses a deterministic fake backend.  It measures
the orchestration shapes needed by the performance work packages without
starting Ollama, opening a socket, or changing the production orchestrator.
The JSON artifact is the canonical format; JSONL is provided for streaming
consumers and is validated by the same schema checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
ARTIFACT_TYPE = "thug-fugu.parallel-performance"
BENCHMARK_NAME = "parallelism-contract"
REQUIRED_SCENARIOS = {
    "serial_n2",
    "serial_n4",
    "fanout_n2",
    "fanout_n4",
    "concurrent_requests",
    "unequal_worker_latency",
    "worker_failure",
    "deadline_expiration",
    "capacity_1",
    "capacity_2",
    "capacity_4",
    "cold_run",
    "warm_run",
}
GATE_THRESHOLDS = {2: 1.8, 4: 3.4}
SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|hostname|password|secret|"
    r"raw[_-]?content|worker[_-]?content)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RES = (
    re.compile(r"(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}"),
    re.compile(r"bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"https?://[^\s/@]+:[^\s/@]+@", re.IGNORECASE),
)
PRIVACY_POLICY_FIELDS = {
    "raw_worker_content_saved",
    "api_keys_saved",
    "hostnames_saved",
}


class FakeBackendError(Exception):
    """An expected fake-backend failure, never persisted with its message."""


class FakeEndpoint:
    """A bounded, latency-controlled endpoint shared by benchmark requests."""

    def __init__(self, name: str, capacity: int, cold_start_ms: float) -> None:
        if capacity <= 0:
            raise ValueError("endpoint capacity must be positive")
        self.name = name
        self.capacity = capacity
        self.cold_start_ms = cold_start_ms
        self._slots = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._inflight = 0
        self._max_inflight = 0
        self._used = False

    def invoke(
        self,
        worker_id: str,
        request_started: float,
        submitted_at: float,
        latency_ms: float,
        deadline_at: Optional[float],
        should_fail: bool,
    ) -> Dict[str, Any]:
        queue_started = time.perf_counter()
        acquired = self._acquire(deadline_at)
        acquired_at = time.perf_counter()
        queue_wait_ms = (acquired_at - queue_started) * 1000
        submitted_ms = (submitted_at - request_started) * 1000
        if not acquired:
            return {
                "worker_id": worker_id,
                "endpoint": self.name,
                "submitted_ms": _round_ms(submitted_ms),
                "start_ms": None,
                "end_ms": _round_ms((acquired_at - request_started) * 1000),
                "queue_wait_ms": _round_ms(queue_wait_ms),
                "warm": None,
                "success": False,
                "error_kind": "deadline",
            }

        try:
            start = time.perf_counter()
            with self._lock:
                cold = not self._used
                self._used = True
                self._inflight += 1
                self._max_inflight = max(self._max_inflight, self._inflight)
            effective_latency_ms = latency_ms + (self.cold_start_ms if cold else 0.0)
            time.sleep(max(0.0, effective_latency_ms) / 1000)
            end = time.perf_counter()
            timed_out = deadline_at is not None and end > deadline_at
            error_kind = "deadline" if timed_out else "backend_failure" if should_fail else None
            return {
                "worker_id": worker_id,
                "endpoint": self.name,
                "submitted_ms": _round_ms(submitted_ms),
                "start_ms": _round_ms((start - request_started) * 1000),
                "end_ms": _round_ms((end - request_started) * 1000),
                "queue_wait_ms": _round_ms(queue_wait_ms),
                "warm": not cold,
                "success": error_kind is None,
                "error_kind": error_kind,
            }
        except FakeBackendError:
            raise
        finally:
            with self._lock:
                self._inflight -= 1
            self._slots.release()

    def _acquire(self, deadline_at: Optional[float]) -> bool:
        if deadline_at is None:
            self._slots.acquire()
            return True
        remaining = deadline_at - time.perf_counter()
        if remaining <= 0:
            return False
        return self._slots.acquire(timeout=remaining)

    def capacity_violation(self) -> bool:
        with self._lock:
            return self._max_inflight > self.capacity


class FakeBackendPool:
    """A pool of fake endpoints with no network or model dependency."""

    def __init__(self, endpoint_count: int, capacity: int, cold_start_ms: float) -> None:
        if endpoint_count <= 0:
            raise ValueError("endpoint_count must be positive")
        self.endpoints = [
            FakeEndpoint(f"endpoint-{index}", capacity, cold_start_ms)
            for index in range(endpoint_count)
        ]

    @property
    def total_capacity(self) -> int:
        return sum(endpoint.capacity for endpoint in self.endpoints)

    def endpoint_for(self, worker_index: int) -> FakeEndpoint:
        return self.endpoints[worker_index % len(self.endpoints)]

    def capacity_violation(self) -> bool:
        return any(endpoint.capacity_violation() for endpoint in self.endpoints)


def percentile(values: Iterable[float], percent: float) -> Optional[float]:
    """Return nearest-rank percentile, or ``None`` when no samples exist."""

    if not 0 < percent <= 100:
        raise ValueError("percent must be in the range (0, 100]")
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return _round_ms(ordered[rank - 1])


def percentiles(values: Iterable[float]) -> Dict[str, Optional[float]]:
    values = list(values)
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
    }


def run_request(
    pool: FakeBackendPool,
    request_id: str,
    worker_count: int,
    mode: str,
    latency_ms: Sequence[float],
    fail_worker: Optional[int] = None,
    deadline_ms: Optional[float] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one request and return a privacy-safe request-level sample."""

    if worker_count <= 0:
        raise ValueError("worker_count must be positive")
    if len(latency_ms) != worker_count:
        raise ValueError("latency_ms must have one value per worker")
    if mode not in {"serial", "parallel"}:
        raise ValueError("mode must be serial or parallel")

    request_started = time.perf_counter()
    deadline_at = (
        request_started + deadline_ms / 1000 if deadline_ms is not None else None
    )

    def execute(worker_index: int, submitted_at: float) -> Dict[str, Any]:
        endpoint = pool.endpoint_for(worker_index)
        return endpoint.invoke(
            worker_id=f"worker-{worker_index}",
            request_started=request_started,
            submitted_at=submitted_at,
            latency_ms=latency_ms[worker_index],
            deadline_at=deadline_at,
            should_fail=fail_worker == worker_index,
        )

    workers: List[Dict[str, Any]] = []
    max_workers = 1 if mode == "serial" else worker_count
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="fugu-bench"
    ) as pool_executor:
        futures = []
        for worker_index in range(worker_count):
            submitted_at = time.perf_counter()
            futures.append(pool_executor.submit(execute, worker_index, submitted_at))
        workers = [future.result() for future in futures]

    wall_ms = (time.perf_counter() - request_started) * 1000
    starts = [worker["start_ms"] for worker in workers if worker["start_ms"] is not None]
    ends = [worker["end_ms"] for worker in workers if worker["end_ms"] is not None]
    submitted = [worker["submitted_ms"] for worker in workers]
    queue_waits = [worker["queue_wait_ms"] for worker in workers]
    critical_path_ms = max(ends) - min(starts) if starts and ends else None
    successes = sum(1 for worker in workers if worker["success"])
    failures = len(workers) - successes
    timeouts = sum(1 for worker in workers if worker["error_kind"] == "deadline")
    sample: Dict[str, Any] = {
        "sample_type": "request",
        "request_id": request_id,
        "batch_id": batch_id,
        "mode": mode,
        "n": pool.total_capacity,
        "worker_count": worker_count,
        "warm": bool(workers) and all(worker["warm"] is True for worker in workers),
        "worker_results": workers,
        "successes": successes,
        "failures": failures,
        "timeouts": timeouts,
        "success_rate_pct": _round_pct(successes / len(workers) * 100),
        "capacity_violation": pool.capacity_violation(),
        "metrics": {
            "wall_ms": _round_ms(wall_ms),
            "critical_path_ms": _round_optional(critical_path_ms),
            "dispatch_skew_ms": _round_ms(max(submitted) - min(submitted)),
            "queue_wait_ms": _round_ms(sum(queue_waits) / len(queue_waits)),
            "orchestration_overhead_ms": _round_optional(
                max(0.0, wall_ms - critical_path_ms) if critical_path_ms is not None else None
            ),
            "throughput_rps": _round_rate(successes, wall_ms),
        },
    }
    return sample


def run_concurrent_requests(
    pool: FakeBackendPool,
    request_count: int,
    request_concurrency: int,
    worker_count: int,
    latency_ms: Sequence[float],
    run_index: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run independent orchestration requests concurrently on one endpoint pool."""

    batch_id = f"batch-{run_index}"
    batch_started = time.perf_counter()

    def execute(request_index: int) -> Dict[str, Any]:
        return run_request(
            pool,
            request_id=f"{batch_id}-request-{request_index}",
            worker_count=worker_count,
            mode="parallel",
            latency_ms=latency_ms,
            batch_id=batch_id,
        )

    with ThreadPoolExecutor(max_workers=request_concurrency) as executor:
        samples = list(executor.map(execute, range(request_count)))
    batch_wall_ms = (time.perf_counter() - batch_started) * 1000
    successes = sum(sample["successes"] for sample in samples)
    total_workers = sum(sample["worker_count"] for sample in samples)
    return samples, {
        "batch_id": batch_id,
        "request_count": request_count,
        "request_concurrency": request_concurrency,
        "wall_ms": _round_ms(batch_wall_ms),
        "throughput_rps": _round_rate(request_count, batch_wall_ms),
        "worker_success_rate_pct": _round_pct(successes / total_workers * 100),
    }


def build_artifact(
    runs: int = 3,
    seed: int = 7,
    latency_ms: float = 25.0,
    cold_start_ms: float = 10.0,
    commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute all required scenarios and return the versioned artifact."""

    if runs <= 0:
        raise ValueError("runs must be positive")
    if latency_ms < 0 or cold_start_ms < 0:
        raise ValueError("latency values must be non-negative")

    scenario_specs = _scenario_specs(latency_ms, cold_start_ms)
    config = {
        "runs_per_scenario": runs,
        "seed": seed,
        "latency_ms": latency_ms,
        "cold_start_ms": cold_start_ms,
        "scenario_specs": scenario_specs,
    }
    artifact: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "benchmark": BENCHMARK_NAME,
        "commit": commit or _git_commit(),
        "config_hash": _config_hash(config),
        "seed": seed,
        "backend": {
            "kind": "fake",
            "model": "deterministic-latency-v1",
            "runtime": "python-threadpool",
        },
        "hardware": _hardware_metadata(),
        "privacy": {
            "raw_worker_content_saved": False,
            "api_keys_saved": False,
            "hostnames_saved": False,
        },
        "config": config,
        "scenarios": [],
        "comparisons": [],
    }

    for spec in scenario_specs:
        scenario, comparison_samples = _run_scenario(spec, runs, latency_ms, cold_start_ms)
        artifact["scenarios"].append(scenario)
        if comparison_samples is not None:
            artifact.setdefault("_comparison_samples", {})[spec["id"]] = comparison_samples

    artifact["comparisons"] = _build_comparisons(artifact)
    artifact.pop("_comparison_samples", None)
    errors = validate_artifact(artifact)
    if errors:
        raise ValueError("generated invalid artifact: " + "; ".join(errors))
    return artifact


def validate_artifact(artifact: Any) -> List[str]:
    """Return schema/privacy errors; an empty list means the artifact is valid."""

    errors: List[str] = []
    if not isinstance(artifact, dict):
        return ["artifact must be a JSON object"]
    required = {
        "schema_version",
        "artifact_type",
        "benchmark",
        "commit",
        "config_hash",
        "seed",
        "backend",
        "hardware",
        "privacy",
        "config",
        "scenarios",
        "comparisons",
    }
    missing = sorted(required - set(artifact))
    errors.extend(f"missing required field: {field}" for field in missing)
    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if artifact.get("artifact_type") != ARTIFACT_TYPE:
        errors.append(f"artifact_type must be {ARTIFACT_TYPE!r}")
    if not isinstance(artifact.get("scenarios"), list):
        errors.append("scenarios must be a list")
    else:
        scenario_ids = [
            scenario.get("id")
            for scenario in artifact["scenarios"]
            if isinstance(scenario, dict)
        ]
        errors.extend(
            f"missing required scenario: {scenario_id}"
            for scenario_id in sorted(REQUIRED_SCENARIOS - set(scenario_ids))
        )
        for index, scenario in enumerate(artifact["scenarios"]):
            errors.extend(_validate_scenario(scenario, index))
    errors.extend(_find_sensitive_fields(artifact))
    return errors


def validate_artifact_file(path: Path) -> List[str]:
    try:
        artifact = _read_artifact(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"cannot read artifact: {exc}"]
    return validate_artifact(artifact)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="write a newly generated artifact")
    action.add_argument("--validate", type=Path, help="validate an existing JSON or JSONL artifact")
    parser.add_argument("--format", choices=("json", "jsonl"), default="json")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--latency-ms", type=float, default=25.0)
    parser.add_argument("--cold-start-ms", type=float, default=10.0)
    args = parser.parse_args(argv)

    if args.validate:
        errors = validate_artifact_file(args.validate)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print(f"OK: valid parallel-performance artifact: {args.validate}")
        return 0

    try:
        artifact = build_artifact(
            runs=args.runs,
            seed=args.seed,
            latency_ms=args.latency_ms,
            cold_start_ms=args.cold_start_ms,
        )
    except ValueError as exc:
        parser.error(str(exc))
    _write_artifact(args.output, artifact, args.format)
    for comparison in artifact["comparisons"]:
        status = "PASS" if comparison["gate"]["passed"] else "FAIL"
        print(
            f"N={comparison['n']} speedup={comparison['worker_speedup']} "
            f"efficiency={comparison['parallel_efficiency']} gate={status}"
        )
    return 0


def _scenario_specs(latency_ms: float, cold_start_ms: float) -> List[Dict[str, Any]]:
    return [
        _spec("serial_n2", "fanout", "serial", 2, 2, 2, 1, latency_ms),
        _spec("serial_n4", "fanout", "serial", 4, 4, 4, 1, latency_ms),
        _spec("fanout_n2", "fanout", "parallel", 2, 2, 2, 1, latency_ms),
        _spec("fanout_n4", "fanout", "parallel", 4, 4, 4, 1, latency_ms),
        {
            **_spec(
                "concurrent_requests",
                "request-throughput",
                "parallel",
                2,
                2,
                2,
                1,
                latency_ms,
            ),
            "request_count": 4,
            "request_concurrency": 4,
        },
        {
            **_spec("unequal_worker_latency", "fanout", "parallel", 4, 4, 4, 1, latency_ms),
            "latency_multiplier": [1.0, 1.0, 2.0, 3.0],
        },
        {
            **_spec("worker_failure", "failure", "parallel", 4, 4, 4, 1, latency_ms),
            "fail_worker": 2,
        },
        {
            **_spec("deadline_expiration", "timeout", "parallel", 4, 4, 4, 1, latency_ms),
            "deadline_ms": max(0.1, latency_ms / 2),
        },
        _spec("capacity_1", "capacity", "parallel", 1, 4, 1, 1, latency_ms),
        _spec("capacity_2", "capacity", "parallel", 2, 4, 1, 2, latency_ms),
        _spec("capacity_4", "capacity", "parallel", 4, 4, 1, 4, latency_ms),
        {
            **_spec("cold_run", "temperature", "parallel", 4, 4, 4, 1, latency_ms),
            "cold_start_ms": cold_start_ms,
            "warmup": False,
        },
        {
            **_spec("warm_run", "temperature", "parallel", 4, 4, 4, 1, latency_ms),
            "cold_start_ms": cold_start_ms,
            "warmup": True,
        },
    ]


def _spec(
    scenario_id: str,
    kind: str,
    mode: str,
    n: int,
    worker_count: int,
    endpoint_count: int,
    capacity: int,
    latency_ms: float,
) -> Dict[str, Any]:
    return {
        "id": scenario_id,
        "kind": kind,
        "mode": mode,
        "n": n,
        "worker_count": worker_count,
        "endpoint_count": endpoint_count,
        "endpoint_capacity": capacity,
        "latency_ms": [latency_ms] * worker_count,
    }


def _run_scenario(
    spec: Dict[str, Any], runs: int, latency_ms: float, cold_start_ms: float
) -> Tuple[Dict[str, Any], Optional[List[Dict[str, Any]]]]:
    scenario_cold_start = spec.get("cold_start_ms", 0.0)
    pool = FakeBackendPool(spec["endpoint_count"], spec["endpoint_capacity"], scenario_cold_start)
    samples: List[Dict[str, Any]] = []
    batch_metrics: List[Dict[str, Any]] = []
    if spec.get("warmup"):
        run_request(
            pool,
            request_id=f"{spec['id']}-warmup",
            worker_count=spec["worker_count"],
            mode=spec["mode"],
            latency_ms=[latency_ms] * spec["worker_count"],
        )

    for run_index in range(1, runs + 1):
        worker_latencies = [
            latency_ms * multiplier
            for multiplier in spec.get(
                "latency_multiplier", [1.0] * spec["worker_count"]
            )
        ]
        if spec["id"] == "concurrent_requests":
            request_samples, batch = run_concurrent_requests(
                pool,
                request_count=spec["request_count"],
                request_concurrency=spec["request_concurrency"],
                worker_count=spec["worker_count"],
                latency_ms=worker_latencies,
                run_index=run_index,
            )
            samples.extend(request_samples)
            batch_metrics.append(batch)
            continue
        sample = run_request(
            pool,
            request_id=f"{spec['id']}-run-{run_index}",
            worker_count=spec["worker_count"],
            mode=spec["mode"],
            latency_ms=worker_latencies,
            fail_worker=spec.get("fail_worker"),
            deadline_ms=spec.get("deadline_ms"),
        )
        samples.append(sample)

    scenario = {
        "id": spec["id"],
        "kind": spec["kind"],
        "mode": spec["mode"],
        "n": spec["n"],
        "worker_count": spec["worker_count"],
        "endpoint_count": spec["endpoint_count"],
        "endpoint_capacity": spec["endpoint_capacity"],
        "warm_run": bool(spec.get("warmup")),
        "cold_start_ms": _round_ms(scenario_cold_start),
        "sample_unit": "request",
        "samples": samples,
        "summary": _summarize(samples, batch_metrics),
    }
    if batch_metrics:
        scenario["batches"] = batch_metrics
    comparison_ids = {"serial_n2", "serial_n4", "fanout_n2", "fanout_n4"}
    return scenario, samples if spec["id"] in comparison_ids else None


def _summarize(samples: List[Dict[str, Any]], batches: List[Dict[str, Any]]) -> Dict[str, Any]:
    wall = [sample["metrics"]["wall_ms"] for sample in samples]
    critical = _present_metric(samples, "critical_path_ms")
    dispatch = [sample["metrics"]["dispatch_skew_ms"] for sample in samples]
    queue_wait = [sample["metrics"]["queue_wait_ms"] for sample in samples]
    overhead = _present_metric(samples, "orchestration_overhead_ms")
    throughput = [batch["throughput_rps"] for batch in batches] or [
        sample["metrics"]["throughput_rps"] for sample in samples
    ]
    total = sum(sample["worker_count"] for sample in samples)
    successes = sum(sample["successes"] for sample in samples)
    return {
        "sample_count": len(samples),
        "worker_count": total,
        "successes": successes,
        "failures": total - successes,
        "timeouts": sum(sample["timeouts"] for sample in samples),
        "success_rate_pct": _round_pct(successes / total * 100) if total else 0.0,
        "wall_ms": percentiles(wall),
        "critical_path_ms": percentiles(critical),
        "dispatch_skew_ms": percentiles(dispatch),
        "queue_wait_ms": percentiles(queue_wait),
        "orchestration_overhead_ms": percentiles(overhead),
        "throughput_rps": percentiles(throughput),
        "capacity_violation": any(sample["capacity_violation"] for sample in samples),
    }


def _build_comparisons(artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_id = {scenario["id"]: scenario for scenario in artifact["scenarios"]}
    comparisons = []
    for n in (2, 4):
        serial = by_id[f"serial_n{n}"]["summary"]
        parallel = by_id[f"fanout_n{n}"]["summary"]
        serial_time = serial["critical_path_ms"]["p50"]
        parallel_time = parallel["critical_path_ms"]["p50"]
        speedup = serial_time / parallel_time if serial_time and parallel_time else None
        efficiency = speedup / n if speedup is not None else None
        gate = {
            "required_worker_speedup": GATE_THRESHOLDS[n],
            "dispatch_skew_p95_max_ms": 50.0,
            "passed": bool(
                speedup is not None
                and speedup >= GATE_THRESHOLDS[n]
                and (parallel["dispatch_skew_ms"]["p95"] or 0.0) <= 50.0
                and not parallel["capacity_violation"]
            ),
        }
        comparisons.append(
            {
                "n": n,
                "T_serial_workers_ms": serial_time,
                "T_parallel_workers_ms": parallel_time,
                "worker_speedup": _round_ratio(speedup),
                "parallel_efficiency": _round_ratio(efficiency),
                "dispatch_skew_ms": parallel["dispatch_skew_ms"],
                "orchestration_overhead_ms": parallel["orchestration_overhead_ms"],
                "queue_wait_ms": parallel["queue_wait_ms"],
                "throughput_rps": parallel["throughput_rps"],
                "capacity_violation": parallel["capacity_violation"],
                "gate": gate,
            }
        )
    return comparisons


def _validate_scenario(scenario: Any, index: int) -> List[str]:
    prefix = f"scenarios[{index}]"
    if not isinstance(scenario, dict):
        return [f"{prefix} must be an object"]
    errors = []
    for field in (
        "id",
        "kind",
        "mode",
        "n",
        "worker_count",
        "endpoint_count",
        "endpoint_capacity",
        "warm_run",
        "sample_unit",
        "samples",
        "summary",
    ):
        if field not in scenario:
            errors.append(f"{prefix} missing required field: {field}")
    if scenario.get("sample_unit") != "request":
        errors.append(f"{prefix}.sample_unit must be 'request'")
    if all(
        isinstance(scenario.get(field), int)
        for field in ("n", "endpoint_count", "endpoint_capacity")
    ) and scenario["n"] != scenario["endpoint_count"] * scenario["endpoint_capacity"]:
        errors.append(f"{prefix}.n must equal endpoint_count * endpoint_capacity")
    samples = scenario.get("samples")
    if not isinstance(samples, list) or not samples:
        errors.append(f"{prefix}.samples must be a non-empty list")
    else:
        for sample_index, sample in enumerate(samples):
            errors.extend(_validate_sample(sample, f"{prefix}.samples[{sample_index}]"))
    if not isinstance(scenario.get("summary"), dict):
        errors.append(f"{prefix}.summary must be an object")
    return errors


def _validate_sample(sample: Any, prefix: str) -> List[str]:
    if not isinstance(sample, dict):
        return [f"{prefix} must be an object"]
    errors = []
    required = {
        "sample_type",
        "request_id",
        "mode",
        "n",
        "worker_count",
        "warm",
        "worker_results",
        "successes",
        "failures",
        "timeouts",
        "success_rate_pct",
        "capacity_violation",
        "metrics",
    }
    errors.extend(
        f"{prefix} missing required field: {field}"
        for field in sorted(required - set(sample))
    )
    if sample.get("sample_type") != "request":
        errors.append(f"{prefix}.sample_type must be 'request'")
    workers = sample.get("worker_results")
    if not isinstance(workers, list) or len(workers) != sample.get("worker_count"):
        errors.append(f"{prefix}.worker_results must match worker_count")
    else:
        for worker_index, worker in enumerate(workers):
            errors.extend(_validate_worker(worker, f"{prefix}.worker_results[{worker_index}]"))
    metrics = sample.get("metrics")
    if not isinstance(metrics, dict):
        errors.append(f"{prefix}.metrics must be an object")
    else:
        errors.extend(
            f"{prefix}.metrics missing required field: {field}"
            for field in (
                "wall_ms",
                "critical_path_ms",
                "dispatch_skew_ms",
                "queue_wait_ms",
                "orchestration_overhead_ms",
                "throughput_rps",
            )
            if field not in metrics
        )
    return errors


def _validate_worker(worker: Any, prefix: str) -> List[str]:
    if not isinstance(worker, dict):
        return [f"{prefix} must be an object"]
    required = {
        "worker_id",
        "endpoint",
        "submitted_ms",
        "start_ms",
        "end_ms",
        "queue_wait_ms",
        "warm",
        "success",
        "error_kind",
    }
    return [f"{prefix} missing required field: {field}" for field in sorted(required - set(worker))]


def _find_sensitive_fields(value: Any, path: str = "artifact") -> List[str]:
    errors: List[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            allowed_policy_field = path == "artifact.privacy" and key in PRIVACY_POLICY_FIELDS
            if SENSITIVE_KEY_RE.search(str(key)) and not allowed_policy_field:
                errors.append(f"sensitive field is not allowed: {child_path}")
            errors.extend(_find_sensitive_fields(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_find_sensitive_fields(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_RES):
            errors.append(f"sensitive value is not allowed at {path}")
    return errors


def _present_metric(samples: List[Dict[str, Any]], key: str) -> List[float]:
    return [sample["metrics"][key] for sample in samples if sample["metrics"][key] is not None]


def _write_artifact(path: Path, artifact: Dict[str, Any], output_format: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote JSON artifact: {path}")
        return
    manifest = {key: value for key, value in artifact.items() if key != "scenarios"}
    records = [{"record_type": "manifest", **manifest}]
    records.extend(
        {"record_type": "scenario", "scenario": scenario}
        for scenario in artifact["scenarios"]
    )
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    print(f"Wrote JSONL artifact: {path}")


def _read_artifact(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() != ".jsonl":
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("JSON artifact must be an object")
        return parsed
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    if (
        not records
        or not isinstance(records[0], dict)
        or records[0].get("record_type") != "manifest"
    ):
        raise ValueError("JSONL artifact must start with a manifest record")
    artifact = {key: value for key, value in records[0].items() if key != "record_type"}
    scenarios = []
    for record in records[1:]:
        if not isinstance(record, dict):
            raise ValueError("JSONL records must be objects")
        if record.get("record_type") == "scenario":
            if "scenario" not in record:
                raise ValueError("JSONL scenario record must contain scenario")
            scenarios.append(record["scenario"])
    artifact["scenarios"] = scenarios
    return artifact


def _config_hash(config: Dict[str, Any]) -> str:
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if re.fullmatch(r"[0-9a-f]{7,40}", commit) else "unknown"


def _hardware_metadata() -> Dict[str, Any]:
    return {
        "source": "local-process",
        "platform": platform.system().lower(),
        "python_version": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
    }


def _round_ms(value: float) -> float:
    return round(float(value), 3)


def _round_optional(value: Optional[float]) -> Optional[float]:
    return None if value is None else _round_ms(value)


def _round_pct(value: float) -> float:
    return round(float(value), 2)


def _round_ratio(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def _round_rate(successes: int, wall_ms: float) -> float:
    return round(successes / (wall_ms / 1000), 4) if wall_ms > 0 else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
