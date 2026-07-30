#!/usr/bin/env python3
"""Compare one or more Thug-Fugu configs on the same JSONL task set.

Legacy mode writes caller-selected CSV and summary paths. Experiment mode
(``--output-dir``) additionally snapshots inputs, records a reproducible
manifest, preserves full raw outputs as JSONL, and emits a rerun command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import random
import re
import shlex
import socket
import statistics
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fugu_local.backends import ChatMessage
from fugu_local.config import FuguLocalConfig, load_config
from fugu_local.orchestrator import FuguLocalOrchestrator

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Condition:
    label: str
    config_path: Path
    metadata: Optional[dict] = None


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    prompt: str
    grader: dict


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", help="JSONL eval cases")
    parser.add_argument(
        "--condition",
        action="append",
        help="Condition as LABEL=CONFIG_PATH. Repeat for comparisons.",
    )
    parser.add_argument(
        "--condition-meta",
        action="append",
        default=[],
        help="Optional per-condition metadata as LABEL=JSON_PATH (e.g. quantization).",
    )
    parser.add_argument("--seed", type=int, default=None, help="Recorded experiment seed")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Optional temperature override applied to every condition",
    )
    parser.add_argument(
        "--hardware-json",
        help="Optional JSON object describing hardware, power meter, or node layout",
    )
    parser.add_argument(
        "--output-dir",
        help="Experiment bundle directory (manifest, snapshots, JSONL, CSV, summary)",
    )
    parser.add_argument(
        "--rerun-manifest",
        help="Re-run conditions and cases recorded in a previous manifest",
    )
    parser.add_argument("--csv", help="Legacy per-case CSV output path")
    parser.add_argument("--summary", help="Legacy aggregate summary JSON output path")
    args = parser.parse_args(argv)

    if args.rerun_manifest:
        if not args.output_dir:
            raise SystemExit("--rerun-manifest requires --output-dir")
        run_spec = _load_rerun_spec(Path(args.rerun_manifest))
    else:
        if not args.cases or not args.condition:
            raise SystemExit("--cases and at least one --condition are required")
        metadata = _parse_condition_metadata(args.condition_meta)
        conditions = [
            _condition_with_metadata(_parse_condition(raw), metadata) for raw in args.condition
        ]
        run_spec = {
            "cases_path": Path(args.cases),
            "conditions": conditions,
            "seed": 0 if args.seed is None else args.seed,
            "temperature": args.temperature,
            "hardware": _load_hardware(Path(args.hardware_json))
            if args.hardware_json
            else _auto_hardware(),
            "source_manifest": None,
        }

    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    if output_dir is None and (not args.csv or not args.summary):
        raise SystemExit("use --output-dir, or provide both --csv and --summary")

    cases_path = Path(run_spec["cases_path"]).resolve()
    conditions = list(run_spec["conditions"])
    seed = int(run_spec["seed"])
    temperature = run_spec["temperature"]
    cases = list(_load_cases(cases_path))

    bundle = None
    if output_dir is not None:
        bundle = _prepare_bundle(
            output_dir,
            cases_path=cases_path,
            conditions=conditions,
            case_count=len(cases),
            seed=seed,
            temperature=temperature,
            hardware=run_spec["hardware"],
            source_manifest=run_spec["source_manifest"],
        )

    rows = []
    for condition in conditions:
        config = load_config(str(condition.config_path))
        orchestrator = FuguLocalOrchestrator(config)
        for case in cases:
            rows.append(
                _run_case(
                    condition,
                    orchestrator,
                    case,
                    seed=seed,
                    temperature=temperature,
                )
            )

    summary = _summarize(rows)
    if bundle is not None:
        _write_jsonl(bundle["results_jsonl"], rows)
        _write_csv(bundle["csv"], rows)
        _write_json(bundle["summary"], summary)
    else:
        _write_csv(Path(args.csv), rows)
        _write_json(Path(args.summary), summary)
    _print_summary(summary)
    return 0


def _parse_condition(raw: str) -> Condition:
    if "=" not in raw:
        raise SystemExit("--condition must be LABEL=CONFIG_PATH")
    label, path = raw.split("=", 1)
    if not label:
        raise SystemExit("condition label must not be empty")
    return Condition(label=label, config_path=Path(path))


def _parse_condition_metadata(raw_values: list[str]) -> dict[str, dict]:
    metadata = {}
    for raw in raw_values:
        if "=" not in raw:
            raise SystemExit("--condition-meta must be LABEL=JSON_PATH")
        label, path = raw.split("=", 1)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"condition metadata for {label!r} must be a JSON object")
        metadata[label] = payload
    return metadata


def _condition_with_metadata(condition: Condition, metadata: dict[str, dict]) -> Condition:
    return Condition(
        label=condition.label,
        config_path=condition.config_path,
        metadata=metadata.get(condition.label, {}),
    )


def _load_cases(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raw = json.loads(stripped)
            case_id = raw.get("id")
            prompt = raw.get("prompt")
            grader = raw.get("grader")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"case line {line_number}: id must be a non-empty string")
            if not isinstance(prompt, str) or not prompt:
                raise ValueError(f"case line {line_number}: prompt must be a non-empty string")
            if not isinstance(grader, dict):
                raise ValueError(f"case line {line_number}: grader must be an object")
            yield EvalCase(case_id=case_id, prompt=prompt, grader=grader)


def _run_case(
    condition: Condition,
    orchestrator: FuguLocalOrchestrator,
    case: EvalCase,
    *,
    seed: int = 0,
    temperature: Optional[float] = None,
) -> dict:
    random.seed(f"{seed}:{condition.label}:{case.case_id}")
    started = time.perf_counter()
    error = ""
    content = ""
    passed = False
    pattern = ""
    worker_count = 0
    selected_roles = []
    usage = None
    try:
        result = orchestrator.chat(
            [ChatMessage(role="user", content=case.prompt)],
            temperature=temperature,
        )
        content = result.content
        passed = _grade(content, case.grader)
        pattern = result.pattern
        worker_count = len(result.worker_results)
        selected_roles = list(getattr(result, "selected_roles", []))
        usage = _usage_payload(getattr(result, "usage", None))
    except Exception as exc:  # noqa: BLE001 - evaluator records failures as rows.
        error = str(exc)
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "condition": condition.label,
        "config": str(condition.config_path),
        "condition_metadata": condition.metadata or {},
        "case_id": case.case_id,
        "passed": passed,
        "wall_ms": wall_ms,
        "pattern": pattern,
        "worker_count": worker_count,
        "selected_roles": selected_roles,
        "usage": usage,
        "error": error,
        "content": content,
        "content_preview": content[:240].replace("\n", "\\n"),
    }


def _grade(content: str, grader: dict) -> bool:
    grader_type = grader.get("type")
    if grader_type == "contains":
        value = grader.get("value")
        if not isinstance(value, str):
            raise ValueError("contains grader requires string value")
        return value.casefold() in content.casefold()
    if grader_type == "regex":
        pattern = grader.get("pattern")
        if not isinstance(pattern, str):
            raise ValueError("regex grader requires string pattern")
        return re.search(pattern, content, flags=re.IGNORECASE | re.MULTILINE) is not None
    if grader_type == "exact":
        value = grader.get("value")
        if not isinstance(value, str):
            raise ValueError("exact grader requires string value")
        return content.strip() == value.strip()
    raise ValueError(f"unsupported grader type: {grader_type}")


def _prepare_bundle(
    output_dir: Path,
    *,
    cases_path: Path,
    conditions: list[Condition],
    case_count: int,
    seed: int,
    temperature: Optional[float],
    hardware: dict,
    source_manifest: Optional[Path],
) -> dict[str, Path]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"output directory is not empty: {output_dir}")
    inputs_dir = output_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    cases_snapshot = inputs_dir / "cases.jsonl"
    cases_snapshot.write_bytes(cases_path.read_bytes())
    condition_records = []
    credentials_redacted = False
    for index, condition in enumerate(conditions):
        snapshot_name = f"{index + 1:02d}-{_slug(condition.label)}.json"
        snapshot_path = inputs_dir / snapshot_name
        raw_config = json.loads(condition.config_path.read_text(encoding="utf-8"))
        sanitized, redacted = _sanitize_config(raw_config)
        credentials_redacted = credentials_redacted or redacted
        snapshot_path.write_text(
            json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        config = load_config(str(condition.config_path))
        condition_records.append(
            {
                "label": condition.label,
                "source_config_path": str(condition.config_path.resolve()),
                "config_path": str(snapshot_path.relative_to(output_dir)),
                "config_sha256": _sha256(condition.config_path),
                "metadata": condition.metadata or {},
                "resolved": _config_manifest(config, condition.metadata or {}),
            }
        )

    manifest_path = output_dir / "manifest.json"
    rerun_output = output_dir.with_name(f"{output_dir.name}-rerun")
    rerun_command = (
        "PYTHONPATH=src python3 scripts/evaluate_orchestration.py "
        f"--rerun-manifest {shlex.quote(str(manifest_path))} "
        f"--output-dir {shlex.quote(str(rerun_output))}"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "temperature_override": temperature,
        "cases": {
            "source_path": str(cases_path),
            "path": str(cases_snapshot.relative_to(output_dir)),
            "sha256": _sha256(cases_path),
            "count": case_count,
        },
        "conditions": condition_records,
        "hardware": hardware,
        "outputs": {
            "results_jsonl": "results.jsonl",
            "csv": "results.csv",
            "summary": "summary.json",
        },
        "source_manifest": str(source_manifest) if source_manifest else None,
        "rerun_command": rerun_command,
        "reproducibility_notes": (
            "Config snapshots redact literal api_key values. Re-runs prefer the "
            "original config when its SHA-256 still matches; otherwise environment-"
            "based credentials may need to be restored."
            if credentials_redacted
            else "Config snapshots contain no detected literal api_key values."
        ),
    }
    _write_json(manifest_path, manifest)
    rerun_script = output_dir / "rerun.sh"
    rerun_script.write_text(f"#!/bin/sh\nset -eu\n{rerun_command}\n", encoding="utf-8")
    rerun_script.chmod(0o755)
    return {
        "manifest": manifest_path,
        "results_jsonl": output_dir / "results.jsonl",
        "csv": output_dir / "results.csv",
        "summary": output_dir / "summary.json",
    }


def _load_rerun_spec(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported evaluation manifest schema_version")
    root = manifest_path.parent
    cases_path = _prefer_matching_source(
        manifest["cases"].get("source_path"),
        root / manifest["cases"]["path"],
        manifest["cases"]["sha256"],
    )
    conditions = []
    for record in manifest["conditions"]:
        path = _prefer_matching_source(
            record.get("source_config_path"),
            root / record["config_path"],
            record["config_sha256"],
        )
        conditions.append(
            Condition(
                label=record["label"],
                config_path=path,
                metadata=record.get("metadata", {}),
            )
        )
    return {
        "cases_path": cases_path,
        "conditions": conditions,
        "seed": manifest["seed"],
        "temperature": manifest.get("temperature_override"),
        "hardware": manifest.get("hardware", {}),
        "source_manifest": manifest_path,
    }


def _prefer_matching_source(
    source_path: Optional[str],
    snapshot_path: Path,
    expected_sha256: str,
) -> Path:
    if source_path:
        source = Path(source_path)
        if source.exists() and _sha256(source) == expected_sha256:
            return source
    return snapshot_path


def _config_manifest(config: FuguLocalConfig, metadata: dict) -> dict:
    return {
        "models": [
            {
                "name": model.name,
                "backend": model.backend,
                "model": model.model,
                "base_url": _safe_endpoint(model.base_url),
                "timeout_seconds": model.timeout_seconds,
            }
            for model in config.models
        ],
        "model_pools": [
            {
                "name": pool.name,
                "backend": pool.backend,
                "model": pool.model,
                "endpoints": [_safe_endpoint(endpoint) for endpoint in pool.endpoints],
                "policy": pool.policy,
            }
            for pool in config.model_pools
        ],
        "roles": [
            {
                "name": role.name,
                "model": role.model,
                "is_synthesizer": role.is_synthesizer,
                "is_verifier": role.is_verifier,
            }
            for role in config.roles
        ],
        "orchestrator": {
            "selection_policy": config.orchestrator.selection_policy,
            "max_parallel_workers": config.orchestrator.max_parallel_workers,
            "temperature": config.orchestrator.temperature,
            "max_tokens": config.orchestrator.max_tokens,
            "request_timeout_seconds": config.orchestrator.request_timeout_seconds,
        },
        "coordinator": {
            "enabled": config.coordinator.enabled,
            "default_pattern": config.coordinator.default_pattern,
            "ensemble_n": config.coordinator.ensemble.n,
            "ensemble_vote": config.coordinator.ensemble.vote,
            "verify_enabled": config.coordinator.verify.enabled,
            "verify_max_retries": config.coordinator.verify.max_retries,
        },
        "quantization": metadata.get("quantization"),
    }


def _sanitize_config(value: Any, key: str = "") -> tuple[Any, bool]:
    if isinstance(value, dict):
        output = {}
        redacted = False
        for child_key, child_value in value.items():
            if child_key == "api_key" and isinstance(child_value, str):
                if child_value.startswith("${") and child_value.endswith("}"):
                    output[child_key] = child_value
                elif child_value:
                    output[child_key] = "<redacted>"
                    redacted = True
                else:
                    output[child_key] = child_value
                continue
            sanitized, child_redacted = _sanitize_config(child_value, child_key)
            output[child_key] = sanitized
            redacted = redacted or child_redacted
        return output, redacted
    if isinstance(value, list):
        output = []
        redacted = False
        for child in value:
            sanitized, child_redacted = _sanitize_config(child, key)
            output.append(sanitized)
            redacted = redacted or child_redacted
        return output, redacted
    return value, False


def _safe_endpoint(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
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


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "condition",
        "config",
        "case_id",
        "passed",
        "wall_ms",
        "pattern",
        "worker_count",
        "selected_roles",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "error",
        "content_preview",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            usage = row.get("usage") or {}
            writer.writerow(
                {
                    "condition": row["condition"],
                    "config": row["config"],
                    "case_id": row["case_id"],
                    "passed": row["passed"],
                    "wall_ms": row["wall_ms"],
                    "pattern": row["pattern"],
                    "worker_count": row["worker_count"],
                    "selected_roles": json.dumps(row.get("selected_roles", []), ensure_ascii=False),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "error": row["error"],
                    "content_preview": row["content_preview"],
                }
            )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _summarize(rows: list[dict]) -> dict:
    by_condition: dict[str, list[dict]] = {}
    for row in rows:
        by_condition.setdefault(row["condition"], []).append(row)
    summary: dict[str, Any] = {"conditions": {}}
    for condition, condition_rows in by_condition.items():
        total = len(condition_rows)
        passed = sum(1 for row in condition_rows if row["passed"])
        latencies = [float(row["wall_ms"]) for row in condition_rows]
        errors = sum(1 for row in condition_rows if row["error"])
        token_values = [
            row["usage"]["total_tokens"]
            for row in condition_rows
            if row.get("usage") and row["usage"].get("total_tokens") is not None
        ]
        summary["conditions"][condition] = {
            "cases": total,
            "passed": passed,
            "accuracy": round(passed / total, 4) if total else 0.0,
            "errors": errors,
            "mean_wall_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
            "median_wall_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
            "total_tokens": sum(token_values) if token_values else None,
            "mean_total_tokens": (
                round(statistics.mean(token_values), 1) if token_values else None
            ),
        }
    return summary


def _usage_payload(usage) -> Optional[dict]:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }


def _print_summary(summary: dict) -> None:
    print("Evaluation summary")
    print("------------------")
    for condition, metrics in summary["conditions"].items():
        token_text = (
            f" total_tokens={metrics['total_tokens']}"
            if metrics["total_tokens"] is not None
            else ""
        )
        print(
            f"{condition}: accuracy={metrics['accuracy']:.2%} "
            f"passed={metrics['passed']}/{metrics['cases']} "
            f"mean_wall_ms={metrics['mean_wall_ms']} errors={metrics['errors']}"
            f"{token_text}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return slug or "condition"


if __name__ == "__main__":
    raise SystemExit(main())
