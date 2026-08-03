#!/usr/bin/env python3
"""Validate the WP-2 hard-benchmark-v2 task schema and the
calibration/dev/test split's structural rules
(docs/plans/phase2-decision-implementation-plan.md, WP-2, section 3.6).

Exit non-zero on any violation. This checks SCHEMA and STRUCTURE only --
it cannot verify a `gold` answer is actually correct, only that the
grader accepts it (a self-consistency check that catches authoring
mistakes, not wrong answers). Human review of task content and gold
answers is a separate, mandatory HUMAN GATE (see the plan's WP-2 section
3.9); this script is not a substitute for it and does not touch
`review_status`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from fugu_local.answers import normalize_answer

ALLOWED_FAMILIES = {"math", "coding", "logic", "planning", "long_context", "japanese"}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
ALLOWED_ANSWER_TYPES = {"single", "multi"}
ALLOWED_GRADER_TYPES = {"contains", "regex", "exact"}
ALLOWED_REVIEW_STATUSES = {"pending", "approved"}

ROLE_MIN_COUNTS = {"calibration": 30, "dev": 60, "test": 60}
MIN_TOTAL_TASKS = 150
MIN_TASKS_PER_FAMILY_TOTAL = 20
MIN_TASKS_PER_FAMILY_TEST = 10
MAX_EASY_RATIO = 0.20


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="task JSONL file path(s)")
    args = parser.parse_args(argv)

    errors = validate_files(args.paths)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"{len(errors)} validation error(s)", file=sys.stderr)
        return 1

    total = sum(_count_tasks(path) for path in args.paths)
    print(f"OK: {len(args.paths)} file(s), {total} task(s) validated")
    return 0


def validate_files(paths: List[Path]) -> List[str]:
    """Pure function: file paths in, a list of human-readable error
    strings out (empty list means every check passed). Never raises for a
    malformed input file -- unreadable files, invalid JSON lines, and
    schema violations all become entries in the returned list instead."""

    errors: List[str] = []
    tasks_by_role: Dict[str, List[dict]] = defaultdict(list)
    seen_ids: Dict[str, Path] = {}

    for path in paths:
        role = _infer_role(path)
        if role is None:
            errors.append(
                f"{path}: cannot infer role (calibration/dev/test) from the filename; "
                "expected the filename to contain exactly one of those words"
            )

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append(f"{path}: cannot read file: {exc}")
            continue

        for line_number, raw_line in enumerate(lines, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                task = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_number}: invalid JSON: {exc}")
                continue
            if not isinstance(task, dict):
                errors.append(f"{path}:{line_number}: task must be a JSON object")
                continue

            for message in _validate_task_schema(task):
                errors.append(f"{path}:{line_number} (id={task.get('id', '?')}): {message}")

            task_id = task.get("id")
            if isinstance(task_id, str) and task_id:
                if task_id in seen_ids:
                    errors.append(
                        f"{path}:{line_number}: duplicate id '{task_id}' "
                        f"(first seen in {seen_ids[task_id]})"
                    )
                else:
                    seen_ids[task_id] = path

            if role is not None:
                tasks_by_role[role].append(task)

    errors.extend(_validate_role_counts(tasks_by_role))
    errors.extend(_validate_family_counts(tasks_by_role))
    errors.extend(_validate_easy_ratio(tasks_by_role))
    return errors


def _grade(content: str, grader: dict) -> bool:
    """A minimal re-implementation of evaluate_orchestration.py's grader,
    using fugu_local.answers.normalize_answer directly instead of that
    script's LaTeX-augmented normalizer -- gold-answer self-consistency
    doesn't need LaTeX-specific stripping (author gold values in plain
    text). Kept local rather than imported from scripts.evaluate_
    orchestration so this file works when invoked directly
    (`python3 scripts/validate_tasks.py ...`), which puts only this
    file's own directory on sys.path, not the repository root."""

    grader_type = grader.get("type")
    normalize = grader.get("normalize", False)
    if not isinstance(normalize, bool):
        raise ValueError("grader normalize must be a boolean when provided")
    text = normalize_answer(content) if normalize else content
    if grader_type == "contains":
        value = grader.get("value")
        if not isinstance(value, str):
            raise ValueError("contains grader requires string value")
        target = normalize_answer(value) if normalize else value
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
        target = normalize_answer(value) if normalize else value
        return text.strip() == target.strip()
    raise ValueError(f"unsupported grader type: {grader_type}")


def _infer_role(path: Path) -> Optional[str]:
    name = path.name.lower()
    matches = [role for role in ROLE_MIN_COUNTS if role in name]
    return matches[0] if len(matches) == 1 else None


def _validate_task_schema(task: dict) -> List[str]:
    errors: List[str] = []

    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        errors.append("'id' must be a non-empty string")

    family = task.get("family")
    if family not in ALLOWED_FAMILIES:
        errors.append(f"'family' must be one of {sorted(ALLOWED_FAMILIES)}, got {family!r}")

    difficulty = task.get("difficulty")
    if difficulty not in ALLOWED_DIFFICULTIES:
        errors.append(
            f"'difficulty' must be one of {sorted(ALLOWED_DIFFICULTIES)}, got {difficulty!r}"
        )

    answer_type = task.get("answer_type")
    if answer_type not in ALLOWED_ANSWER_TYPES:
        errors.append(
            f"'answer_type' must be one of {sorted(ALLOWED_ANSWER_TYPES)}, got {answer_type!r}"
        )

    prompt = task.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        errors.append("'prompt' must be a non-empty string")

    grader = task.get("grader")
    if not isinstance(grader, dict):
        errors.append("'grader' must be an object")
        grader = {}
    grader_type = grader.get("type")
    if grader_type not in ALLOWED_GRADER_TYPES:
        errors.append(
            f"'grader.type' must be one of {sorted(ALLOWED_GRADER_TYPES)}, got {grader_type!r} "
            "(exec/rubric/freeform grading is out of scope for the decision set)"
        )

    review_status = task.get("review_status")
    if review_status not in ALLOWED_REVIEW_STATUSES:
        errors.append(
            f"'review_status' must be one of {sorted(ALLOWED_REVIEW_STATUSES)}, "
            f"got {review_status!r}"
        )

    gold = task.get("gold")
    if not isinstance(gold, str) or not gold.strip():
        errors.append("'gold' must be a non-empty string")
    elif grader_type in ALLOWED_GRADER_TYPES:
        try:
            gold_passes = _grade(gold, grader)
        except ValueError as exc:
            errors.append(f"grader definition is invalid: {exc}")
        else:
            if not gold_passes:
                errors.append(
                    f"'gold' value {gold!r} does not pass its own grader {grader!r} "
                    "(self-consistency check failed)"
                )

    return errors


def _validate_role_counts(tasks_by_role: Dict[str, List[dict]]) -> List[str]:
    errors: List[str] = []
    total = sum(len(tasks) for tasks in tasks_by_role.values())
    if total < MIN_TOTAL_TASKS:
        errors.append(f"total task count {total} is below the required minimum {MIN_TOTAL_TASKS}")
    for role, minimum in ROLE_MIN_COUNTS.items():
        count = len(tasks_by_role.get(role, []))
        if count < minimum:
            errors.append(
                f"'{role}' file has {count} task(s), below the required minimum {minimum}"
            )
    return errors


def _validate_family_counts(tasks_by_role: Dict[str, List[dict]]) -> List[str]:
    errors: List[str] = []
    all_tasks = [task for tasks in tasks_by_role.values() for task in tasks]
    family_totals = Counter(
        task.get("family") for task in all_tasks if task.get("family") in ALLOWED_FAMILIES
    )
    for family in sorted(ALLOWED_FAMILIES):
        count = family_totals.get(family, 0)
        if count < MIN_TASKS_PER_FAMILY_TOTAL:
            errors.append(
                f"family '{family}' has {count} task(s) total, below the required "
                f"minimum {MIN_TASKS_PER_FAMILY_TOTAL}"
            )

    test_tasks = tasks_by_role.get("test", [])
    test_family_totals = Counter(
        task.get("family") for task in test_tasks if task.get("family") in ALLOWED_FAMILIES
    )
    for family in sorted(ALLOWED_FAMILIES):
        count = test_family_totals.get(family, 0)
        if count < MIN_TASKS_PER_FAMILY_TEST:
            errors.append(
                f"family '{family}' has {count} task(s) in the test file, below the "
                f"required minimum {MIN_TASKS_PER_FAMILY_TEST}"
            )
    return errors


def _validate_easy_ratio(tasks_by_role: Dict[str, List[dict]]) -> List[str]:
    errors: List[str] = []
    for role, tasks in sorted(tasks_by_role.items()):
        if not tasks:
            continue
        easy_count = sum(1 for task in tasks if task.get("difficulty") == "easy")
        ratio = easy_count / len(tasks)
        if ratio > MAX_EASY_RATIO:
            errors.append(
                f"'{role}' file has {ratio:.1%} easy tasks, above the maximum {MAX_EASY_RATIO:.0%}"
            )
    return errors


def _count_tasks(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
