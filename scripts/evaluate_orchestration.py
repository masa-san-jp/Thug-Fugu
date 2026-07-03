#!/usr/bin/env python3
"""Evaluate one or more Thug-Fugu configs on a JSONL task set.

The harness is intentionally dependency-free. It is a first step toward the
Fugu-style coordinator make-or-break evaluation loop: compare direct/static/adaptive
conditions, record latency, and grade outputs with deterministic checks.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fugu_local.backends import ChatMessage
from fugu_local.config import load_config
from fugu_local.orchestrator import FuguLocalOrchestrator


@dataclass(frozen=True)
class Condition:
    label: str
    config_path: Path


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    prompt: str
    grader: dict


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="JSONL eval cases")
    parser.add_argument(
        "--condition",
        action="append",
        required=True,
        help="Condition as LABEL=CONFIG_PATH. Repeat for A/B/C comparisons.",
    )
    parser.add_argument("--csv", required=True, help="Per-case CSV output path")
    parser.add_argument("--summary", required=True, help="Aggregate summary JSON output path")
    args = parser.parse_args(argv)

    conditions = [_parse_condition(raw) for raw in args.condition]
    cases = list(_load_cases(Path(args.cases)))
    rows = []

    for condition in conditions:
        config = load_config(str(condition.config_path))
        orchestrator = FuguLocalOrchestrator(config)
        for case in cases:
            rows.append(_run_case(condition, orchestrator, case))

    _write_csv(Path(args.csv), rows)
    summary = _summarize(rows)
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    _print_summary(summary)
    return 0


def _parse_condition(raw: str) -> Condition:
    if "=" not in raw:
        raise SystemExit("--condition must be LABEL=CONFIG_PATH")
    label, path = raw.split("=", 1)
    if not label:
        raise SystemExit("condition label must not be empty")
    return Condition(label=label, config_path=Path(path))


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


def _run_case(condition: Condition, orchestrator: FuguLocalOrchestrator, case: EvalCase) -> dict:
    started = time.perf_counter()
    error = ""
    content = ""
    passed = False
    pattern = ""
    worker_count = 0
    try:
        result = orchestrator.chat([ChatMessage(role="user", content=case.prompt)])
        content = result.content
        passed = _grade(content, case.grader)
        pattern = result.pattern
        worker_count = len(result.worker_results)
    except Exception as exc:  # noqa: BLE001 - evaluator records failures as rows.
        error = str(exc)
    wall_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "condition": condition.label,
        "config": str(condition.config_path),
        "case_id": case.case_id,
        "passed": passed,
        "wall_ms": wall_ms,
        "pattern": pattern,
        "worker_count": worker_count,
        "error": error,
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
        "error",
        "content_preview",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
        summary["conditions"][condition] = {
            "cases": total,
            "passed": passed,
            "accuracy": round(passed / total, 4) if total else 0.0,
            "errors": errors,
            "mean_wall_ms": round(statistics.mean(latencies), 1) if latencies else 0.0,
            "median_wall_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        }
    return summary


def _print_summary(summary: dict) -> None:
    print("Evaluation summary")
    print("------------------")
    for condition, metrics in summary["conditions"].items():
        print(
            f"{condition}: accuracy={metrics['accuracy']:.2%} "
            f"passed={metrics['passed']}/{metrics['cases']} "
            f"mean_wall_ms={metrics['mean_wall_ms']} errors={metrics['errors']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
