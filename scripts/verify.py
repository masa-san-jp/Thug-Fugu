"""Run the repository's canonical local verification checks.

This wrapper intentionally invokes fixed module commands without a shell so an
implementation agent has one predictable verification entry point.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Step:
    name: str
    argv: List[str]


def build_steps(*, fast: bool) -> List[Step]:
    python = sys.executable
    lint_targets = ["src", "tests", "scripts"]
    steps = [
        Step("ruff lint", [python, "-m", "ruff", "check", *lint_targets]),
        Step("ruff format", [python, "-m", "ruff", "format", "--check", *lint_targets]),
    ]
    if fast:
        steps.append(
            Step(
                "unit tests",
                [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
            )
        )
        return steps

    steps.extend(
        [
            Step("coverage erase", [python, "-m", "coverage", "erase"]),
            Step(
                "unit tests with coverage",
                [
                    python,
                    "-m",
                    "coverage",
                    "run",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-v",
                ],
            ),
            Step(
                "coverage threshold",
                [python, "-m", "coverage", "report", "--fail-under=85"],
            ),
            Step("package build", [python, "-m", "build"]),
        ]
    )
    return steps


def run_step(step: Step) -> int:
    env = os.environ.copy()
    source_path = str(ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        source_path
        if not existing_pythonpath
        else os.pathsep.join([source_path, existing_pythonpath])
    )
    print(f"\n==> {step.name}: {shlex.join(step.argv)}", flush=True)
    completed = subprocess.run(step.argv, cwd=ROOT, env=env, check=False)
    if completed.returncode:
        print(f"FAIL: {step.name} (exit {completed.returncode})", file=sys.stderr)
    else:
        print(f"PASS: {step.name}")
    return completed.returncode


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fast",
        action="store_true",
        help="run lint, format, and unit tests without coverage or package build",
    )
    args = parser.parse_args(argv)
    for step in build_steps(fast=args.fast):
        if run_step(step):
            return 1
    print("\nAll verification checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
