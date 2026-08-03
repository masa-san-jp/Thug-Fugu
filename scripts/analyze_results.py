#!/usr/bin/env python3
"""Error correlation and complementarity analysis for a results.jsonl
produced by scripts/evaluate_orchestration.py (WP-6).

Coordinating multiple models is only useful if their errors aren't
correlated, and an accuracy improvement needs to be attributable to
something specific (the synthesizer, a cooperating stage) rather than
assumed. This script computes, from an existing results.jsonl, whether
conditions'/workers' mistakes overlap, how often the synthesizer damages an
otherwise-correct worker answer (or repairs an otherwise-wrong one), and
cost/quality ratios -- without re-running any orchestration and without
re-applying task graders (it trusts the `passed` fields WP-1 already
recorded).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
PHI_BOOTSTRAP_ITERATIONS = 2000
PHI_BOOTSTRAP_RNG_SEED = 20260802


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "results_path", type=Path, help="results.jsonl from evaluate_orchestration.py"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    rows = _load_rows(args.results_path)
    analysis = analyze(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_dir / "analysis.md").write_text(_render_markdown(analysis), encoding="utf-8")
    _print_summary(analysis)
    return 0


def _load_rows(path: Path) -> List[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def analyze(rows: List[dict]) -> dict:
    """Pure function: results.jsonl rows in, the full analysis dict out.
    Never raises for missing worker_outputs/stage_results/passed data --
    the affected metric is set to None and a reason is appended to
    ``warnings`` instead (see WP-6, docs/plans/
    phase2-decision-implementation-plan.md)."""

    warnings: List[str] = []
    conditions = _conditions_in_order(rows)
    overall = _compute_metrics(rows, warnings, context="overall")
    by_domain = {
        domain: _compute_metrics(domain_rows, warnings, context=f"domain={domain}")
        for domain, domain_rows in sorted(_group_by(rows, "domain").items())
    }
    stage_contributions, stage_warning = _stage_contributions(rows)
    if stage_warning:
        warnings.append(stage_warning)

    return {
        "schema_version": SCHEMA_VERSION,
        "n_rows": len(rows),
        "conditions": conditions,
        **overall,
        "stage_contributions": stage_contributions,
        "by_domain": by_domain,
        "warnings": warnings,
    }


def _compute_metrics(rows: List[dict], warnings: List[str], *, context: str) -> dict:
    matrix = _correctness_matrix(rows)
    binarized = _binarize_matrix(matrix)
    conditions = _conditions_in_order(rows)
    by_condition_rows = _group_by(rows, "condition")

    oracle_upper_bound: Dict[str, Optional[float]] = {}
    damage_rate: Dict[str, Optional[float]] = {}
    repair_rate: Dict[str, Optional[float]] = {}
    for condition in conditions:
        condition_rows = by_condition_rows.get(condition, [])
        oracle_matrix, any_missing = _oracle_task_matrix(condition_rows)
        if not oracle_matrix:
            warnings.append(
                f"worker_outputs[].passed unavailable for condition '{condition}' "
                f"({context}); oracle upper bound and synthesizer damage/repair "
                "rate set to null"
            )
            oracle_upper_bound[condition] = None
            damage_rate[condition] = None
            repair_rate[condition] = None
            continue
        if any_missing:
            warnings.append(
                f"worker_outputs[].passed missing on some rows for condition "
                f"'{condition}' ({context}); those rows excluded from oracle "
                "upper bound and synthesizer damage/repair rate"
            )

        binarized_oracle = [1 if value >= 0.5 else 0 for value in oracle_matrix.values()]
        oracle_upper_bound[condition] = round(sum(binarized_oracle) / len(binarized_oracle), 6)

        damage_num = damage_den = repair_num = repair_den = 0
        for case_id, oracle_avg in oracle_matrix.items():
            final_avg = matrix.get(case_id, {}).get(condition)
            if final_avg is None:
                continue
            oracle_bin = 1 if oracle_avg >= 0.5 else 0
            final_bin = 1 if final_avg >= 0.5 else 0
            if oracle_bin == 1:
                damage_den += 1
                damage_num += 1 if final_bin == 0 else 0
            else:
                repair_den += 1
                repair_num += 1 if final_bin == 1 else 0
        damage_rate[condition] = (damage_num / damage_den) if damage_den else None
        repair_rate[condition] = (repair_num / repair_den) if repair_den else None

    return {
        "correctness_matrix": matrix,
        "condition_pair_correlation": _pairwise_condition_correlation(binarized, conditions),
        "worker_pair_correlation": _worker_pair_correlation(rows, warnings, context=context),
        "oracle_upper_bound": oracle_upper_bound,
        "synthesizer_damage_rate": damage_rate,
        "synthesizer_repair_rate": repair_rate,
        "quality_per_1k_tokens": _quality_per_1k_tokens(by_condition_rows),
        "cost_per_correct": _cost_per_correct(by_condition_rows),
    }


# -- correctness matrix ------------------------------------------------------


def _correctness_matrix(rows: List[dict]) -> Dict[str, Dict[str, float]]:
    """task (case_id) x condition -> mean of `passed` across repeats (0..1)."""

    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for row in rows:
        grouped[(row["case_id"], row["condition"])].append(1.0 if row.get("passed") else 0.0)
    matrix: Dict[str, Dict[str, float]] = defaultdict(dict)
    for (case_id, condition), values in grouped.items():
        matrix[case_id][condition] = round(statistics.fmean(values), 6)
    return dict(matrix)


def _binarize_matrix(matrix: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, int]]:
    """Majority-vote a fractional (repeats-averaged) matrix into 0/1 per task."""

    return {
        case_id: {condition: (1 if value >= 0.5 else 0) for condition, value in values.items()}
        for case_id, values in matrix.items()
    }


# -- phi coefficient ----------------------------------------------------------


def _phi_and_agreement(pairs: List[Tuple[int, int]]) -> Tuple[Optional[float], Optional[float]]:
    if not pairs:
        return None, None
    n11 = sum(1 for a, b in pairs if a == 1 and b == 1)
    n10 = sum(1 for a, b in pairs if a == 1 and b == 0)
    n01 = sum(1 for a, b in pairs if a == 0 and b == 1)
    n00 = sum(1 for a, b in pairs if a == 0 and b == 0)
    total = n11 + n10 + n01 + n00
    agreement = (n11 + n00) / total if total else None
    denom = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    phi = (n11 * n00 - n10 * n01) / denom if denom else None
    return phi, agreement


def _phi_bootstrap_ci(
    pairs: List[Tuple[int, int]],
    iterations: int = PHI_BOOTSTRAP_ITERATIONS,
    rng_seed: int = PHI_BOOTSTRAP_RNG_SEED,
) -> Tuple[Optional[float], Optional[float]]:
    """Deterministic bootstrap 95% CI for the phi coefficient. Resamples
    are drawn with a fresh, fixed-seed RNG so the result is reproducible
    for a given input regardless of call order."""

    if len(pairs) < 2:
        return None, None
    rng = random.Random(rng_seed)
    n = len(pairs)
    phis = []
    for _ in range(iterations):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        phi, _ = _phi_and_agreement(sample)
        if phi is not None:
            phis.append(phi)
    if not phis:
        return None, None
    phis.sort()
    lo = phis[int(0.025 * len(phis))]
    hi = phis[max(0, min(len(phis) - 1, int(0.975 * len(phis)) - 1))]
    return round(lo, 6), round(hi, 6)


def _pairwise_condition_correlation(
    binarized: Dict[str, Dict[str, int]], conditions: List[str]
) -> List[dict]:
    results = []
    for condition_a, condition_b in combinations(conditions, 2):
        pairs = [
            (values[condition_a], values[condition_b])
            for values in binarized.values()
            if condition_a in values and condition_b in values
        ]
        phi, agreement = _phi_and_agreement(pairs)
        ci = _phi_bootstrap_ci(pairs) if phi is not None else (None, None)
        results.append(
            {
                "condition_a": condition_a,
                "condition_b": condition_b,
                "n_tasks": len(pairs),
                "phi": round(phi, 6) if phi is not None else None,
                "phi_ci95": list(ci) if phi is not None else None,
                "agreement_rate": round(agreement, 6) if agreement is not None else None,
            }
        )
    return results


# -- worker-pair correlation ---------------------------------------------------


def _worker_correctness_matrix(
    condition_rows: List[dict],
) -> Tuple[Dict[str, Dict[str, float]], bool]:
    grouped: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    any_missing = False
    for row in condition_rows:
        for worker in row.get("worker_outputs") or []:
            if "passed" not in worker:
                any_missing = True
                continue
            grouped[(row["case_id"], worker["role"])].append(1.0 if worker["passed"] else 0.0)
    matrix: Dict[str, Dict[str, float]] = defaultdict(dict)
    for (case_id, role), values in grouped.items():
        matrix[case_id][role] = statistics.fmean(values)
    return dict(matrix), any_missing


def _worker_pair_correlation(
    rows: List[dict], warnings: List[str], *, context: str
) -> Dict[str, List[dict]]:
    by_condition = _group_by(rows, "condition")
    result: Dict[str, List[dict]] = {}
    for condition, condition_rows in sorted(by_condition.items()):
        role_matrix, any_missing = _worker_correctness_matrix(condition_rows)
        if any_missing:
            warnings.append(
                f"worker_outputs[].passed missing on some worker entries for "
                f"condition '{condition}' ({context}); those entries excluded "
                "from worker-pair correlation"
            )
        roles = sorted({role for values in role_matrix.values() for role in values})
        pairs_out = []
        for role_a, role_b in combinations(roles, 2):
            pairs = [
                (1 if values[role_a] >= 0.5 else 0, 1 if values[role_b] >= 0.5 else 0)
                for values in role_matrix.values()
                if role_a in values and role_b in values
            ]
            phi, agreement = _phi_and_agreement(pairs)
            ci = _phi_bootstrap_ci(pairs) if phi is not None else (None, None)
            pairs_out.append(
                {
                    "worker_a": role_a,
                    "worker_b": role_b,
                    "n_tasks": len(pairs),
                    "phi": round(phi, 6) if phi is not None else None,
                    "phi_ci95": list(ci) if phi is not None else None,
                    "agreement_rate": round(agreement, 6) if agreement is not None else None,
                }
            )
        if pairs_out:
            result[condition] = pairs_out
    return result


# -- oracle / damage / repair helpers -----------------------------------------


def _row_oracle_state(row: dict) -> Optional[bool]:
    """Whether at least one worker got this row right, or None if no
    worker_outputs entry on this row carries a usable `passed` field."""

    known = [w for w in (row.get("worker_outputs") or []) if "passed" in w]
    if not known:
        return None
    return any(worker["passed"] for worker in known)


def _oracle_task_matrix(condition_rows: List[dict]) -> Tuple[Dict[str, float], bool]:
    grouped: Dict[str, List[float]] = defaultdict(list)
    any_missing = False
    for row in condition_rows:
        state = _row_oracle_state(row)
        if state is None:
            any_missing = True
            continue
        grouped[row["case_id"]].append(1.0 if state else 0.0)
    matrix = {case_id: statistics.fmean(values) for case_id, values in grouped.items()}
    return matrix, any_missing


# -- cost / quality -------------------------------------------------------------


def _quality_per_1k_tokens(by_condition_rows: Dict[str, List[dict]]) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {}
    for condition, rows in by_condition_rows.items():
        correct = sum(1 for row in rows if row.get("passed"))
        total_tokens = sum((row.get("usage") or {}).get("total_tokens") or 0 for row in rows)
        result[condition] = round(correct / (total_tokens / 1000), 6) if total_tokens > 0 else None
    return result


def _cost_per_correct(by_condition_rows: Dict[str, List[dict]]) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    for condition, rows in by_condition_rows.items():
        correct = sum(1 for row in rows if row.get("passed"))
        total_wall_ms = sum(row.get("wall_ms") or 0.0 for row in rows)
        total_tokens = sum((row.get("usage") or {}).get("total_tokens") or 0 for row in rows)
        result[condition] = {
            "wall_ms": round(total_wall_ms / correct, 3) if correct else None,
            "tokens": round(total_tokens / correct, 3) if correct else None,
        }
    return result


# -- stage-level contribution (WP-7) -------------------------------------------


def _stage_contributions(rows: List[dict]) -> Tuple[dict, Optional[str]]:
    """Placeholder wired for WP-7's ablation harness output
    (condition_metadata.ablation_baseline + non-empty stage_results). WP-7
    is not implemented yet, so this always returns empty with a warning
    until it is."""

    has_ablation_metadata = any(
        (row.get("condition_metadata") or {}).get("ablation_baseline") for row in rows
    )
    has_stage_results = any(row.get("stage_results") for row in rows)
    if not has_ablation_metadata or not has_stage_results:
        return {}, (
            "stage-level contribution requires WP-7 ablation condition metadata "
            "(condition_metadata.ablation_baseline) and non-empty stage_results; "
            "none found in this input, so stage_contributions is empty"
        )
    return {}, None


# -- grouping helpers -----------------------------------------------------------


def _conditions_in_order(rows: List[dict]) -> List[str]:
    seen: List[str] = []
    for row in rows:
        label = row.get("condition")
        if label not in seen:
            seen.append(label)
    return seen


def _group_by(rows: List[dict], key: str) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for row in rows:
        grouped[row.get(key) or "unspecified"].append(row)
    return dict(grouped)


# -- rendering --------------------------------------------------------------


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _render_markdown(analysis: dict) -> str:
    lines = ["# Orchestration error-correlation & complementarity analysis", ""]
    lines.append(f"- rows analyzed: {analysis['n_rows']}")
    lines.append(f"- conditions: {', '.join(analysis['conditions']) or '(none)'}")
    lines.append("")

    lines.append("## Oracle upper bound / synthesizer damage & repair rate / cost")
    lines.append("")
    lines.append(
        "| condition | oracle upper bound | damage rate | repair rate | "
        "quality/1k tokens | cost/correct (ms) | cost/correct (tokens) |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for condition in analysis["conditions"]:
        cost = analysis["cost_per_correct"].get(condition, {})
        lines.append(
            f"| {condition} "
            f"| {_fmt(analysis['oracle_upper_bound'].get(condition))} "
            f"| {_fmt(analysis['synthesizer_damage_rate'].get(condition))} "
            f"| {_fmt(analysis['synthesizer_repair_rate'].get(condition))} "
            f"| {_fmt(analysis['quality_per_1k_tokens'].get(condition))} "
            f"| {_fmt(cost.get('wall_ms'))} "
            f"| {_fmt(cost.get('tokens'))} |"
        )
    lines.append("")

    lines.append("## Condition-pair error correlation (phi coefficient)")
    lines.append("")
    lines.append("| condition A | condition B | n tasks | phi | 95% CI | agreement rate |")
    lines.append("|---|---|---|---|---|---|")
    for pair in analysis["condition_pair_correlation"]:
        ci = pair.get("phi_ci95")
        ci_text = f"[{_fmt(ci[0])}, {_fmt(ci[1])}]" if ci else "n/a"
        lines.append(
            f"| {pair['condition_a']} | {pair['condition_b']} | {pair['n_tasks']} "
            f"| {_fmt(pair['phi'])} | {ci_text} | {_fmt(pair['agreement_rate'])} |"
        )
    lines.append("")

    if analysis["worker_pair_correlation"]:
        lines.append("## Worker-pair error correlation (phi coefficient, within condition)")
        lines.append("")
        for condition, pairs in analysis["worker_pair_correlation"].items():
            lines.append(f"### {condition}")
            lines.append("")
            lines.append("| worker A | worker B | n tasks | phi | 95% CI | agreement rate |")
            lines.append("|---|---|---|---|---|---|")
            for pair in pairs:
                ci = pair.get("phi_ci95")
                ci_text = f"[{_fmt(ci[0])}, {_fmt(ci[1])}]" if ci else "n/a"
                lines.append(
                    f"| {pair['worker_a']} | {pair['worker_b']} | {pair['n_tasks']} "
                    f"| {_fmt(pair['phi'])} | {ci_text} | {_fmt(pair['agreement_rate'])} |"
                )
            lines.append("")

    if analysis["by_domain"]:
        lines.append("## By-domain breakdown")
        lines.append("")
        for domain, metrics in sorted(analysis["by_domain"].items()):
            lines.append(f"### {domain}")
            lines.append("")
            lines.append("| condition | oracle upper bound | damage rate | repair rate |")
            lines.append("|---|---|---|---|")
            for condition in analysis["conditions"]:
                lines.append(
                    f"| {condition} "
                    f"| {_fmt(metrics['oracle_upper_bound'].get(condition))} "
                    f"| {_fmt(metrics['synthesizer_damage_rate'].get(condition))} "
                    f"| {_fmt(metrics['synthesizer_repair_rate'].get(condition))} |"
                )
            lines.append("")

    lines.append("## Stage-level contribution (WP-7 ablation)")
    lines.append("")
    if analysis["stage_contributions"]:
        lines.append("```json")
        lines.append(json.dumps(analysis["stage_contributions"], indent=2))
        lines.append("```")
    else:
        lines.append("_Not available: requires WP-7 ablation condition metadata._")
    lines.append("")

    if analysis["warnings"]:
        lines.append("## Warnings")
        lines.append("")
        for warning in analysis["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _print_summary(analysis: dict) -> None:
    print("Error-correlation analysis summary")
    print("-----------------------------------")
    print(f"rows: {analysis['n_rows']}  conditions: {', '.join(analysis['conditions'])}")
    for condition in analysis["conditions"]:
        oracle = _fmt(analysis["oracle_upper_bound"].get(condition))
        damage = _fmt(analysis["synthesizer_damage_rate"].get(condition))
        repair = _fmt(analysis["synthesizer_repair_rate"].get(condition))
        print(f"{condition}: oracle_upper_bound={oracle} damage_rate={damage} repair_rate={repair}")
    if analysis["warnings"]:
        print(f"warnings: {len(analysis['warnings'])} (see analysis.json/analysis.md)")


if __name__ == "__main__":
    raise SystemExit(main())
