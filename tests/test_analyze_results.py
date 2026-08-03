import json
import tempfile
import unittest
from pathlib import Path

from scripts import analyze_results


def _row(
    condition,
    case_id,
    passed,
    *,
    domain="d",
    worker_outputs=None,
    usage=None,
    wall_ms=10.0,
    stage_results=None,
    condition_metadata=None,
):
    return {
        "condition": condition,
        "case_id": case_id,
        "domain": domain,
        "passed": passed,
        "worker_outputs": worker_outputs if worker_outputs is not None else [],
        "stage_results": stage_results or [],
        "usage": usage,
        "wall_ms": wall_ms,
        "condition_metadata": condition_metadata or {},
    }


def _worker(role, passed, ok=True):
    return {"role": role, "model": "m", "ok": ok, "content": "x", "passed": passed}


class HandComputableScenarioTests(unittest.TestCase):
    """A single small synthetic dataset with exactly precomputed expected
    values for every metric, per WP-6's test requirement."""

    def setUp(self):
        # "multi" condition: task A/C have oracle=True (some worker right),
        # task B/D have oracle=False (no worker right). Final passed:
        # A=False (damage), B=True (repair), C=True (no damage), D=False (no repair).
        multi = [
            _row(
                "multi",
                "A",
                False,
                worker_outputs=[_worker("planner", True), _worker("coder", False)],
            ),
            _row(
                "multi",
                "B",
                True,
                worker_outputs=[_worker("planner", False), _worker("coder", False)],
            ),
            _row(
                "multi",
                "C",
                True,
                worker_outputs=[_worker("planner", True), _worker("coder", True)],
            ),
            _row(
                "multi",
                "D",
                False,
                worker_outputs=[_worker("planner", False), _worker("coder", False)],
            ),
        ]
        # "single" condition: final passed matches "multi" final passed
        # exactly (A=0, B=1, C=1, D=0) for a perfect condition-pair phi=1.0.
        single = [
            _row("single", "A", False),
            _row("single", "B", True),
            _row("single", "C", True),
            _row("single", "D", False),
        ]
        self.rows = multi + single
        self.analysis = analyze_results.analyze(self.rows)

    def test_correctness_matrix(self):
        matrix = self.analysis["correctness_matrix"]
        self.assertEqual(matrix["A"], {"multi": 0.0, "single": 0.0})
        self.assertEqual(matrix["B"], {"multi": 1.0, "single": 1.0})
        self.assertEqual(matrix["C"], {"multi": 1.0, "single": 1.0})
        self.assertEqual(matrix["D"], {"multi": 0.0, "single": 0.0})

    def test_oracle_upper_bound(self):
        self.assertEqual(self.analysis["oracle_upper_bound"]["multi"], 0.5)

    def test_damage_and_repair_rate(self):
        self.assertEqual(self.analysis["synthesizer_damage_rate"]["multi"], 0.5)
        self.assertEqual(self.analysis["synthesizer_repair_rate"]["multi"], 0.5)

    def test_single_condition_has_no_workers_so_rates_are_null(self):
        self.assertIsNone(self.analysis["oracle_upper_bound"]["single"])
        self.assertIsNone(self.analysis["synthesizer_damage_rate"]["single"])
        self.assertIsNone(self.analysis["synthesizer_repair_rate"]["single"])

    def test_condition_pair_perfect_correlation(self):
        pairs = self.analysis["condition_pair_correlation"]
        pair = next(p for p in pairs if {p["condition_a"], p["condition_b"]} == {"multi", "single"})
        self.assertEqual(pair["n_tasks"], 4)
        self.assertEqual(pair["phi"], 1.0)
        self.assertEqual(pair["agreement_rate"], 1.0)

    def test_worker_pair_correlation_within_multi_condition(self):
        # planner: [T,F,T,F] (A,B,C,D); coder: [F,F,T,F]
        # binarized identical: planner [1,0,1,0], coder [0,0,1,0]
        # n11=1(C), n10=1(A), n01=0, n00=2(B,D) -> phi = (1*2-1*0)/sqrt(2*2*1*3)
        pairs = self.analysis["worker_pair_correlation"]["multi"]
        pair = next(p for p in pairs if {p["worker_a"], p["worker_b"]} == {"planner", "coder"})
        self.assertEqual(pair["n_tasks"], 4)
        self.assertIsNotNone(pair["phi"])

    def test_quality_and_cost(self):
        # multi: 2 rows passed (B, C) out of 4; no usage tokens configured -> null
        self.assertIsNone(self.analysis["quality_per_1k_tokens"]["multi"])
        cost = self.analysis["cost_per_correct"]["multi"]
        self.assertEqual(cost["wall_ms"], round(4 * 10.0 / 2, 3))

    def test_by_domain_breakdown_mirrors_overall_when_single_domain(self):
        self.assertIn("d", self.analysis["by_domain"])
        self.assertEqual(
            self.analysis["by_domain"]["d"]["oracle_upper_bound"]["multi"],
            self.analysis["oracle_upper_bound"]["multi"],
        )

    def test_stage_contributions_empty_without_wp7_metadata(self):
        self.assertEqual(self.analysis["stage_contributions"], {})
        self.assertTrue(any("WP-7" in warning for warning in self.analysis["warnings"]))


class PhiCoefficientKnownCaseTests(unittest.TestCase):
    def test_perfect_positive_correlation(self):
        pairs = [(1, 1), (1, 1), (0, 0), (0, 0)]
        phi, agreement = analyze_results._phi_and_agreement(pairs)
        self.assertEqual(phi, 1.0)
        self.assertEqual(agreement, 1.0)

    def test_no_correlation(self):
        pairs = [(1, 1), (1, 0), (0, 1), (0, 0)]
        phi, agreement = analyze_results._phi_and_agreement(pairs)
        self.assertEqual(phi, 0.0)
        self.assertEqual(agreement, 0.5)

    def test_perfect_negative_correlation(self):
        pairs = [(1, 0), (1, 0), (0, 1), (0, 1)]
        phi, agreement = analyze_results._phi_and_agreement(pairs)
        self.assertEqual(phi, -1.0)
        self.assertEqual(agreement, 0.0)

    def test_zero_variance_denominator_is_null(self):
        # every pair is (1, 1): no variance in either series -> denom is 0.
        pairs = [(1, 1), (1, 1), (1, 1)]
        phi, agreement = analyze_results._phi_and_agreement(pairs)
        self.assertIsNone(phi)
        self.assertEqual(agreement, 1.0)

    def test_empty_pairs_returns_null(self):
        self.assertEqual(analyze_results._phi_and_agreement([]), (None, None))


class DamageRepairBoundaryTests(unittest.TestCase):
    def test_no_oracle_true_tasks_makes_damage_rate_null(self):
        rows = [
            _row("multi", "A", True, worker_outputs=[_worker("w", False)]),
            _row("multi", "B", False, worker_outputs=[_worker("w", False)]),
        ]
        analysis = analyze_results.analyze(rows)
        self.assertIsNone(analysis["synthesizer_damage_rate"]["multi"])
        # repair_den = 2 (both oracle-false); repair_num = 1 (A repaired)
        self.assertEqual(analysis["synthesizer_repair_rate"]["multi"], 0.5)

    def test_no_oracle_false_tasks_makes_repair_rate_null(self):
        rows = [
            _row("multi", "A", True, worker_outputs=[_worker("w", True)]),
            _row("multi", "B", False, worker_outputs=[_worker("w", True)]),
        ]
        analysis = analyze_results.analyze(rows)
        self.assertIsNone(analysis["synthesizer_repair_rate"]["multi"])
        # damage_den = 2 (both oracle-true); damage_num = 1 (B damaged)
        self.assertEqual(analysis["synthesizer_damage_rate"]["multi"], 0.5)

    def test_all_damaged_gives_damage_rate_one(self):
        rows = [
            _row("multi", "A", False, worker_outputs=[_worker("w", True)]),
            _row("multi", "B", False, worker_outputs=[_worker("w", True)]),
        ]
        analysis = analyze_results.analyze(rows)
        self.assertEqual(analysis["synthesizer_damage_rate"]["multi"], 1.0)

    def test_no_damage_gives_damage_rate_zero(self):
        rows = [
            _row("multi", "A", True, worker_outputs=[_worker("w", True)]),
            _row("multi", "B", True, worker_outputs=[_worker("w", True)]),
        ]
        analysis = analyze_results.analyze(rows)
        self.assertEqual(analysis["synthesizer_damage_rate"]["multi"], 0.0)


class MissingWorkerOutputsTests(unittest.TestCase):
    def test_empty_worker_outputs_does_not_raise_and_warns(self):
        rows = [
            _row("multi", "A", True, worker_outputs=[]),
            _row("multi", "B", False, worker_outputs=[]),
        ]

        analysis = analyze_results.analyze(rows)

        self.assertIsNone(analysis["oracle_upper_bound"]["multi"])
        self.assertIsNone(analysis["synthesizer_damage_rate"]["multi"])
        self.assertIsNone(analysis["synthesizer_repair_rate"]["multi"])
        self.assertTrue(
            any("unavailable" in warning and "multi" in warning for warning in analysis["warnings"])
        )

    def test_partial_missing_worker_outputs_excludes_those_rows_and_warns(self):
        rows = [
            _row("multi", "A", True, worker_outputs=[_worker("w", True)]),
            _row("multi", "B", False, worker_outputs=[]),
        ]

        analysis = analyze_results.analyze(rows)

        self.assertEqual(analysis["oracle_upper_bound"]["multi"], 1.0)
        self.assertTrue(
            any(
                "missing on some rows" in warning and "multi" in warning
                for warning in analysis["warnings"]
            )
        )


class LegacyMissingPassedFieldTests(unittest.TestCase):
    def test_worker_outputs_without_passed_field_nulls_rates_with_warning(self):
        legacy_worker = {"role": "w", "model": "m", "ok": True, "content": "x"}
        rows = [
            _row("multi", "A", True, worker_outputs=[legacy_worker]),
            _row("multi", "B", False, worker_outputs=[legacy_worker]),
        ]

        analysis = analyze_results.analyze(rows)

        self.assertIsNone(analysis["synthesizer_damage_rate"]["multi"])
        self.assertIsNone(analysis["synthesizer_repair_rate"]["multi"])
        self.assertTrue(
            any("unavailable" in warning and "multi" in warning for warning in analysis["warnings"])
        )

    def test_worker_pair_correlation_skips_legacy_entries_with_warning(self):
        legacy_worker = {"role": "w2", "model": "m", "ok": True, "content": "x"}
        rows = [
            _row(
                "multi",
                "A",
                True,
                worker_outputs=[_worker("w1", True), legacy_worker],
            ),
        ]

        analysis = analyze_results.analyze(rows)

        self.assertNotIn("multi", analysis["worker_pair_correlation"])
        self.assertTrue(
            any("worker-pair correlation" in warning for warning in analysis["warnings"])
        )


class MainCliTests(unittest.TestCase):
    def test_main_writes_analysis_json_and_markdown(self):
        rows = [
            _row("multi", "A", True, worker_outputs=[_worker("w", True)]),
            _row("multi", "B", False, worker_outputs=[_worker("w", False)]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.jsonl"
            with results_path.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            output_dir = Path(tmp) / "out"

            code = analyze_results.main([str(results_path), "--output-dir", str(output_dir)])

            self.assertEqual(code, 0)
            analysis_json = json.loads((output_dir / "analysis.json").read_text(encoding="utf-8"))
            analysis_md = (output_dir / "analysis.md").read_text(encoding="utf-8")

        self.assertEqual(analysis_json["n_rows"], 2)
        self.assertIn("# Orchestration error-correlation", analysis_md)


if __name__ == "__main__":
    unittest.main()
