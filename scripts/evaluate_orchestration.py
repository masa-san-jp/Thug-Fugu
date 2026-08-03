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
import math
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

from fugu_local import answers
from fugu_local.backends import ChatMessage
from fugu_local.config import FuguLocalConfig, load_config
from fugu_local.orchestrator import FuguLocalOrchestrator, derive_seed

SCHEMA_VERSION = 3
PAIRED_BOOTSTRAP_ITERATIONS = 10000
PAIRED_BOOTSTRAP_RNG_SEED = 20260802


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
    domain: str = "unspecified"


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
        "--seeds",
        help="Comma-separated experiment seeds (for uncertainty estimates)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help=(
            "Stochastic repeats per (condition, case), seeded from a single base "
            "seed via derive_seed(base_seed, 'repeat#i'). Requires --seed, not --seeds."
        ),
    )
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
        "--budget-manifest",
        help=(
            "Path to a frozen budget-manifest.json (scripts/make_budget_manifest.py). "
            "When set, each case's family-specific token_budget/wall_clock_budget_ms is "
            "pre-allocated to max_tokens/request_timeout_seconds before the call; a run "
            "whose actual usage still exceeds its budget is recorded with "
            "budget_exceeded=true and counted as incorrect (WP-7 budget-matched harness)."
        ),
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
    if args.seed is not None and args.seeds:
        raise SystemExit("use either --seed or --seeds, not both")
    if args.repeats < 1:
        raise SystemExit("--repeats must be a positive integer")
    if args.repeats > 1 and args.seeds:
        raise SystemExit(
            "--repeats requires --seed (not --seeds): repeat seeds derive from a "
            "single base seed, so the base seed to derive from would be ambiguous "
            "with multiple --seeds"
        )

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
            "seeds": _parse_seeds(args.seeds, args.seed),
            "repeats": args.repeats,
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
    seeds = [int(seed) for seed in run_spec["seeds"]]
    repeats = int(run_spec["repeats"])
    temperature = run_spec["temperature"]
    cases = list(_load_cases(cases_path))
    budget_by_family = (
        _load_budget_manifest(Path(args.budget_manifest)) if args.budget_manifest else {}
    )

    bundle = None
    if output_dir is not None:
        bundle = _prepare_bundle(
            output_dir,
            cases_path=cases_path,
            conditions=conditions,
            case_count=len(cases),
            seeds=seeds,
            repeats=repeats,
            temperature=temperature,
            hardware=run_spec["hardware"],
            source_manifest=run_spec["source_manifest"],
        )

    rows = []
    for condition in conditions:
        config = load_config(str(condition.config_path))
        orchestrator = FuguLocalOrchestrator(config)
        for seed in seeds:
            for repeat_index in range(repeats):
                repeat_seed = derive_seed(seed, f"repeat#{repeat_index}")
                for case in cases:
                    family_budget = budget_by_family.get(case.domain, {})
                    rows.append(
                        _run_case(
                            condition,
                            orchestrator,
                            case,
                            seed=seed,
                            repeat_index=repeat_index,
                            repeat_seed=repeat_seed,
                            temperature=temperature,
                            token_budget=family_budget.get("token_budget"),
                            wall_clock_budget_ms=family_budget.get("wall_clock_budget_ms"),
                        )
                    )

    summary = _summarize(rows, repeats=repeats)
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


def _parse_seeds(raw: Optional[str], single_seed: Optional[int]) -> list[int]:
    if raw is None:
        return [0 if single_seed is None else single_seed]
    values = []
    for item in raw.split(","):
        stripped = item.strip()
        if not stripped:
            raise SystemExit("--seeds must be a comma-separated list of integers")
        try:
            values.append(int(stripped))
        except ValueError as exc:
            raise SystemExit("--seeds must contain integers") from exc
    if not values:
        raise SystemExit("--seeds must not be empty")
    return values


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
            domain = raw.get("domain", "unspecified")
            if not isinstance(case_id, str) or not case_id:
                raise ValueError(f"case line {line_number}: id must be a non-empty string")
            if not isinstance(prompt, str) or not prompt:
                raise ValueError(f"case line {line_number}: prompt must be a non-empty string")
            if not isinstance(grader, dict):
                raise ValueError(f"case line {line_number}: grader must be an object")
            if not isinstance(domain, str) or not domain:
                raise ValueError(f"case line {line_number}: domain must be a non-empty string")
            yield EvalCase(case_id=case_id, prompt=prompt, grader=grader, domain=domain)


def _run_case(
    condition: Condition,
    orchestrator: FuguLocalOrchestrator,
    case: EvalCase,
    *,
    seed: int = 0,
    repeat_index: int = 0,
    repeat_seed: Optional[int] = None,
    temperature: Optional[float] = None,
    token_budget: Optional[int] = None,
    wall_clock_budget_ms: Optional[float] = None,
) -> dict:
    random.seed(f"{seed}:{repeat_index}:{condition.label}:{case.case_id}")
    started = time.perf_counter()
    error = ""
    content = ""
    passed = False
    pattern = ""
    worker_count = 0
    selected_roles = []
    usage = None
    worker_outputs: list[dict] = []
    try:
        result = orchestrator.chat(
            [ChatMessage(role="user", content=case.prompt)],
            temperature=temperature,
            seed=repeat_seed,
            max_tokens=token_budget,
            request_timeout_seconds=(
                wall_clock_budget_ms / 1000.0 if wall_clock_budget_ms is not None else None
            ),
        )
        content = result.content
        passed = _grade(content, case.grader)
        pattern = result.pattern
        worker_count = len(result.worker_results)
        selected_roles = list(getattr(result, "selected_roles", []))
        usage = _usage_payload(getattr(result, "usage", None))
        worker_outputs = _worker_outputs_payload(result.worker_results, case.grader)
    except Exception as exc:  # noqa: BLE001 - evaluator records failures as rows.
        error = str(exc)
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    seed_sent = repeat_seed is not None and _condition_uses_real_backend(orchestrator.config)

    # Budget is pre-allocated (max_tokens/request_timeout_seconds above), not
    # just a post-hoc penalty -- but prompt tokens are only known after the
    # call, and the deadline is only checked between orchestration steps, so
    # actual usage can still exceed the budget. When it does, the run counts
    # as incorrect regardless of what the grader said (plan section 8.4):
    # "budget-matched" means only answers actually returned within budget
    # count as correct.
    budget_exceeded: Optional[bool] = None
    if token_budget is not None or wall_clock_budget_ms is not None:
        budget_exceeded = False
        if token_budget is not None and usage and usage.get("total_tokens") is not None:
            if usage["total_tokens"] > token_budget:
                budget_exceeded = True
        if wall_clock_budget_ms is not None and wall_ms > wall_clock_budget_ms:
            budget_exceeded = True
        if budget_exceeded:
            passed = False

    return {
        "condition": condition.label,
        "config": str(condition.config_path),
        "condition_metadata": condition.metadata or {},
        "case_id": case.case_id,
        "domain": case.domain,
        "seed": seed,
        "repeat_index": repeat_index,
        "seed_sent": seed_sent,
        "passed": passed,
        "wall_ms": wall_ms,
        "pattern": pattern,
        "worker_count": worker_count,
        "selected_roles": selected_roles,
        "worker_outputs": worker_outputs,
        "stage_results": [],
        "usage": usage,
        "error": error,
        "content": content,
        "content_preview": content[:240].replace("\n", "\\n"),
        "token_budget": token_budget,
        "wall_clock_budget_ms": wall_clock_budget_ms,
        "budget_exceeded": budget_exceeded,
    }


def _worker_outputs_payload(worker_results, grader: dict) -> list[dict]:
    """Apply the task grader to each worker's own output.

    WP-6's synthesizer damage/repair rate analysis needs to know whether an
    individual worker was right or wrong, independent of the final
    synthesized answer.
    """

    outputs = []
    for worker in worker_results:
        ok = bool(getattr(worker, "ok", False))
        content = worker.content if ok else ""
        passed = _grade(content, grader) if ok else False
        outputs.append(
            {
                "role": worker.role,
                "model": worker.model,
                "ok": ok,
                "content": content,
                "passed": passed,
                "usage": _usage_payload(getattr(worker, "usage", None)),
            }
        )
    return outputs


def _condition_uses_real_backend(config: object) -> bool:
    """Whether this condition can carry a seed to a real backend payload.

    ``EchoBackend`` never builds an outbound request (see
    ``fugu_local.backends.EchoBackend``), so a seed handed to it is recorded
    on the ``ChatRequest`` but never actually sent anywhere. Only Ollama and
    OpenAI-compatible backends put ``seed`` on the wire.
    """

    models = getattr(config, "models", None) or []
    pools = getattr(config, "model_pools", None) or []
    return any(getattr(model, "backend", None) != "echo" for model in models) or any(
        getattr(pool, "backend", None) != "echo" for pool in pools
    )


def _grade(content: str, grader: dict) -> bool:
    grader_type = grader.get("type")
    normalize = grader.get("normalize", False)
    if not isinstance(normalize, bool):
        raise ValueError("grader normalize must be a boolean when provided")
    text = _normalize_answer(content) if normalize else content
    if grader_type == "contains":
        value = grader.get("value")
        if not isinstance(value, str):
            raise ValueError("contains grader requires string value")
        target = _normalize_answer(value) if normalize else value
        return target.casefold() in text.casefold()
    if grader_type == "regex":
        pattern = grader.get("pattern")
        if not isinstance(pattern, str):
            raise ValueError("regex grader requires string pattern")
        return re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) is not None
    if grader_type == "exact":
        value = grader.get("value")
        if not isinstance(value, str):
            raise ValueError("exact grader requires string value")
        target = _normalize_answer(value) if normalize else value
        return text.strip() == target.strip()
    raise ValueError(f"unsupported grader type: {grader_type}")


_SUBSCRIPT_MAP = {ord(c): str(i) for i, c in enumerate("₀₁₂₃₄₅₆₇₈₉")}
_SUPERSCRIPT_MAP = {ord(c): str(i) for i, c in enumerate("⁰¹²³⁴⁵⁶⁷⁸⁹")}


def _normalize_answer(text: str) -> str:
    """Reduce LaTeX / Markdown / Unicode formatting noise before grading.

    This lets deterministic graders match equivalent answers such as ``H2O``,
    ``H_2O``, ``$\\text{H}_2\\text{O}$``, ``**H2O**``, and ``H₂O`` without adding
    per-case special cases. LaTeX math delimiters (``$``, ``{}``, bare ``\\``)
    are stripped here since they are unique to this grader; Markdown emphasis/
    code-fence stripping and whitespace/case/prefix/number normalization
    delegate to ``fugu_local.answers.normalize_answer`` (shared with the
    orchestrator's ensemble voting) instead of duplicating that logic.
    """

    text = text.translate(_SUBSCRIPT_MAP).translate(_SUPERSCRIPT_MAP)
    text = re.sub(r"\\text\s*\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    for char in "${}\\":
        text = text.replace(char, "")
    return answers.normalize_answer(text)


def _prepare_bundle(
    output_dir: Path,
    *,
    cases_path: Path,
    conditions: list[Condition],
    case_count: int,
    seeds: list[int],
    repeats: int,
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
        "seed": seeds[0],
        "seeds": seeds,
        "repeats": repeats,
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


_SUPPORTED_MANIFEST_SCHEMA_VERSIONS = (1, 2, SCHEMA_VERSION)


def _load_rerun_spec(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_version = manifest.get("schema_version")
    if schema_version not in _SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
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
        "seeds": manifest.get("seeds", [manifest["seed"]]),
        "repeats": manifest.get("repeats", 1),
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
            "seed": config.orchestrator.seed,
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


def _load_budget_manifest(path: Path) -> dict:
    """Load a frozen budget-manifest.json (scripts/make_budget_manifest.py)
    and return its ``by_family`` mapping. Never regenerate a manifest from
    the run it's controlling -- load a manifest that was committed ahead of
    time (plan section 8.4)."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    by_family = payload.get("by_family")
    if not isinstance(by_family, dict):
        raise ValueError(f"{path}: budget manifest is missing a 'by_family' object")
    return by_family


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
        "domain",
        "seed",
        "repeat_index",
        "seed_sent",
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
                    "domain": row["domain"],
                    "seed": row["seed"],
                    "repeat_index": row.get("repeat_index", 0),
                    "seed_sent": row.get("seed_sent", False),
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


def _summarize(rows: list[dict], *, repeats: int = 1) -> dict:
    """Aggregate results with unique task (not run) as the sample unit.

    A condition's ``accuracy`` is the mean of its per-task scores, where a
    task's score is its own pass rate across repeats/seeds. This intentionally
    replaces run-level accuracy: with ``--repeats``/``--seeds`` > 1, run-level
    accuracy silently double-counts easy tasks and is not paired across
    conditions.
    """

    by_condition: dict[str, list[dict]] = {}
    for row in rows:
        by_condition.setdefault(row["condition"], []).append(row)

    conditions_summary = {
        label: _condition_summary(condition_rows) for label, condition_rows in by_condition.items()
    }

    condition_labels = list(by_condition.keys())
    paired = []
    if len(condition_labels) >= 2:
        baseline_label = condition_labels[0]
        baseline_scores = conditions_summary[baseline_label]["task_scores"]
        for candidate_label in condition_labels[1:]:
            candidate_scores = conditions_summary[candidate_label]["task_scores"]
            paired.append(
                _paired_comparison(
                    baseline_label, baseline_scores, candidate_label, candidate_scores
                )
            )

    summary = {
        "schema_version": SCHEMA_VERSION,
        "sample_unit": "unique_task",
        "n_tasks": len({row["case_id"] for row in rows}),
        "repeats": repeats,
        "conditions": conditions_summary,
        "paired": paired,
    }

    # Auxiliary view for budget-matched runs (WP-7, plan section 8.4):
    # `conditions` above already counts a budget_exceeded run as incorrect
    # ("what got returned within budget"); `budget_filtered` instead drops
    # those runs entirely, showing accuracy only among runs that actually
    # completed within their pre-allocated budget. Present only when budget
    # tracking was actually used (some row carries a non-None
    # budget_exceeded), so a plain non-budget-matched run's summary.json
    # doesn't grow a redundant, always-identical extra section.
    if any(row.get("budget_exceeded") is not None for row in rows):
        within_budget_rows = [row for row in rows if row.get("budget_exceeded") is not True]
        by_condition_filtered: dict[str, list[dict]] = {}
        for row in within_budget_rows:
            by_condition_filtered.setdefault(row["condition"], []).append(row)
        summary["budget_filtered"] = {
            label: _condition_summary(condition_rows)
            for label, condition_rows in by_condition_filtered.items()
        }

    return summary


def _condition_summary(rows: list[dict]) -> dict:
    task_scores = _task_scores(rows)
    scores = list(task_scores.values())
    latencies = [float(row["wall_ms"]) for row in rows]
    token_values = [
        row["usage"]["total_tokens"]
        for row in rows
        if row.get("usage") and row["usage"].get("total_tokens") is not None
    ]
    return {
        "task_scores": task_scores,
        "accuracy": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "accuracy_stderr": _task_level_stderr(scores),
        "by_domain": _by_domain_accuracy(rows, task_scores),
        "tokens_total": sum(token_values) if token_values else None,
        "wall_ms_p50": _percentile(latencies, 50),
        "wall_ms_p95": _percentile(latencies, 95),
        "runs": len(rows),
        "unique_cases": len(task_scores),
        "passed": sum(1 for row in rows if row["passed"]),
        "errors": sum(1 for row in rows if row["error"]),
        "seeds": sorted({row["seed"] for row in rows}),
    }


def _task_scores(rows: list[dict]) -> dict[str, float]:
    """Per-task pass rate: the fraction of that task's repeats/seeds that passed."""

    by_case: dict[str, list[bool]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(bool(row["passed"]))
    return {case_id: round(sum(passes) / len(passes), 4) for case_id, passes in by_case.items()}


def _task_level_stderr(scores: list[float]) -> float:
    if len(scores) < 2:
        return 0.0
    return round(statistics.stdev(scores) / math.sqrt(len(scores)), 4)


def _by_domain_accuracy(rows: list[dict], task_scores: dict[str, float]) -> dict[str, float]:
    domain_of_case: dict[str, str] = {}
    for row in rows:
        domain_of_case.setdefault(row["case_id"], row["domain"])
    cases_by_domain: dict[str, list[str]] = {}
    for case_id, domain in domain_of_case.items():
        cases_by_domain.setdefault(domain, []).append(case_id)
    return {
        domain: round(sum(task_scores[case_id] for case_id in case_ids) / len(case_ids), 4)
        for domain, case_ids in sorted(cases_by_domain.items())
    }


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; no numpy/scipy dependency."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return round(ordered[index], 1)


def _paired_comparison(
    baseline_label: str,
    baseline_scores: dict[str, float],
    candidate_label: str,
    candidate_scores: dict[str, float],
) -> dict:
    common_ids = sorted(set(baseline_scores) & set(candidate_scores))
    all_ids = set(baseline_scores) | set(candidate_scores)
    diffs = [candidate_scores[case_id] - baseline_scores[case_id] for case_id in common_ids]
    if diffs:
        mean_diff, ci_low, ci_high = _paired_bootstrap_ci(diffs)
    else:
        mean_diff, ci_low, ci_high = 0.0, 0.0, 0.0
    return {
        "baseline": baseline_label,
        "candidate": candidate_label,
        "n_tasks": len(common_ids),
        "n_excluded": len(all_ids) - len(common_ids),
        "mean_diff": round(mean_diff, 4),
        "ci_low": round(ci_low, 4),
        "ci_high": round(ci_high, 4),
        "method": "paired_bootstrap",
        "iterations": PAIRED_BOOTSTRAP_ITERATIONS,
        "rng_seed": PAIRED_BOOTSTRAP_RNG_SEED,
    }


def _paired_bootstrap_ci(
    diffs: list[float],
    iterations: int = PAIRED_BOOTSTRAP_ITERATIONS,
    rng_seed: int = PAIRED_BOOTSTRAP_RNG_SEED,
) -> tuple[float, float, float]:
    """Paired bootstrap CI over unique-task score differences.

    ``diffs`` must be ordered by case_id (callers sort by case_id) so the
    resampling sequence, and therefore the CI, is deterministic for a given
    input. Uses only stdlib ``random`` — no scipy/numpy dependency.
    """

    rng = random.Random(rng_seed)
    n = len(diffs)
    means = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    mean_diff = sum(diffs) / n
    lo = means[int(0.025 * iterations)]
    hi = means[int(0.975 * iterations) - 1]
    return mean_diff, lo, hi


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
        tokens_total = metrics["tokens_total"]
        token_text = f" tokens_total={tokens_total}" if tokens_total is not None else ""
        print(
            f"{condition}: accuracy={metrics['accuracy']:.2%} "
            f"passed={metrics['passed']}/{metrics['runs']} "
            f"wall_ms_p50={metrics['wall_ms_p50']} errors={metrics['errors']}"
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
