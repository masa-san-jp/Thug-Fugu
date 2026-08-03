#!/usr/bin/env python3
"""Compute a frozen, deterministic per-family token/wall-clock budget from
baseline results.jsonl row(s) (WP-7 budget-matched experiment harness,
docs/plans/phase2-decision-implementation-plan.md section 8.4).

Budget = baseline family median x coefficient (default 1.0). This is
PRE-commitment, not a post-hoc penalty: run this once against baseline
(single-model, natural-configuration) results on calibration/dev, commit
the output, and never regenerate it from the same experiment run being
budget-controlled -- doing that would make the budget depend on execution
order, defeating the point of a fixed budget.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
DEFAULT_COEFFICIENT = 1.0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "results_paths", nargs="+", type=Path, help="baseline results.jsonl file(s)"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--coefficient", type=float, default=DEFAULT_COEFFICIENT)
    parser.add_argument(
        "--source-conditions",
        default=None,
        help="comma-separated condition labels this manifest was computed from (informational)",
    )
    args = parser.parse_args(argv)

    if args.coefficient <= 0:
        parser.error("--coefficient must be positive")

    rows: List[dict] = []
    for path in args.results_paths:
        rows.extend(_load_rows(path))

    manifest = build_manifest(
        rows,
        coefficient=args.coefficient,
        source_conditions=_parse_csv(args.source_conditions),
        source_paths=[str(path) for path in args.results_paths],
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(manifest['by_family'])} families)")
    return 0


def _load_rows(path: Path) -> List[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _parse_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_manifest(
    rows: List[dict],
    *,
    coefficient: float = DEFAULT_COEFFICIENT,
    source_conditions: Optional[List[str]] = None,
    source_paths: Optional[List[str]] = None,
) -> dict:
    """Pure function: results.jsonl rows in, manifest dict out.
    Deterministic -- statistics.median has no randomness and doesn't
    depend on input order, so the same rows always produce the same
    manifest regardless of row order or how many times this runs."""

    by_family: Dict[str, Dict[str, list]] = defaultdict(lambda: {"tokens": [], "wall_ms": []})
    for row in rows:
        family = row.get("domain")
        if not family:
            continue
        tokens = (row.get("usage") or {}).get("total_tokens")
        if tokens is not None:
            by_family[family]["tokens"].append(tokens)
        wall_ms = row.get("wall_ms")
        if wall_ms is not None:
            by_family[family]["wall_ms"].append(wall_ms)

    families: Dict[str, dict] = {}
    for family, values in by_family.items():
        token_median = statistics.median(values["tokens"]) if values["tokens"] else None
        wall_ms_median = statistics.median(values["wall_ms"]) if values["wall_ms"] else None
        families[family] = {
            "token_budget": round(token_median * coefficient) if token_median is not None else None,
            "wall_clock_budget_ms": (
                round(wall_ms_median * coefficient, 1) if wall_ms_median is not None else None
            ),
            "n_samples": len(values["tokens"]),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "coefficient": coefficient,
        "source_conditions": source_conditions or [],
        "source_paths": source_paths or [],
        "by_family": families,
    }


if __name__ == "__main__":
    raise SystemExit(main())
