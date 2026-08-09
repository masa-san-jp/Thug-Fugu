import json
import tempfile
import unittest
from pathlib import Path

from scripts import make_budget_manifest


def _row(domain, total_tokens, wall_ms):
    return {"domain": domain, "usage": {"total_tokens": total_tokens}, "wall_ms": wall_ms}


class BuildManifestTests(unittest.TestCase):
    def test_family_median_is_computed_correctly(self):
        rows = [
            _row("math", 100, 10.0),
            _row("math", 200, 20.0),
            _row("math", 300, 30.0),
            _row("coding", 50, 5.0),
            _row("coding", 150, 15.0),
        ]

        manifest = make_budget_manifest.build_manifest(rows)

        self.assertEqual(manifest["by_family"]["math"]["token_budget"], 200)
        self.assertEqual(manifest["by_family"]["math"]["wall_clock_budget_ms"], 20.0)
        self.assertEqual(manifest["by_family"]["math"]["n_samples"], 3)
        # even-count median averages the two middle values: (50+150)/2 = 100
        self.assertEqual(manifest["by_family"]["coding"]["token_budget"], 100)
        self.assertEqual(manifest["by_family"]["coding"]["wall_clock_budget_ms"], 10.0)

    def test_coefficient_is_applied_and_recorded(self):
        rows = [_row("math", 100, 10.0), _row("math", 300, 30.0)]

        manifest = make_budget_manifest.build_manifest(rows, coefficient=1.5)

        self.assertEqual(manifest["coefficient"], 1.5)
        # median tokens = 200, x1.5 = 300
        self.assertEqual(manifest["by_family"]["math"]["token_budget"], 300)
        self.assertEqual(manifest["by_family"]["math"]["wall_clock_budget_ms"], 30.0)

    def test_coefficient_defaults_to_one(self):
        manifest = make_budget_manifest.build_manifest([_row("math", 100, 10.0)])
        self.assertEqual(manifest["coefficient"], 1.0)

    def test_source_conditions_and_paths_are_recorded(self):
        manifest = make_budget_manifest.build_manifest(
            [_row("math", 100, 10.0)],
            source_conditions=["01-best-small-single"],
            source_paths=["results/a.jsonl"],
        )
        self.assertEqual(manifest["source_conditions"], ["01-best-small-single"])
        self.assertEqual(manifest["source_paths"], ["results/a.jsonl"])

    def test_deterministic_output_for_same_input(self):
        rows = [_row("math", 100, 10.0), _row("logic", 300, 30.0), _row("math", 200, 20.0)]

        first = make_budget_manifest.build_manifest(rows)
        second = make_budget_manifest.build_manifest(rows)

        self.assertEqual(first, second)

    def test_deterministic_regardless_of_row_order(self):
        rows_a = [_row("math", 100, 10.0), _row("math", 200, 20.0), _row("math", 300, 30.0)]
        rows_b = list(reversed(rows_a))

        self.assertEqual(
            make_budget_manifest.build_manifest(rows_a),
            make_budget_manifest.build_manifest(rows_b),
        )

    def test_rows_missing_domain_or_usage_are_skipped_without_raising(self):
        rows = [
            {"domain": "math", "usage": {"total_tokens": 100}, "wall_ms": 10.0},
            {"usage": {"total_tokens": 999}, "wall_ms": 999.0},  # no domain
            {"domain": "math", "usage": None, "wall_ms": None},  # no usage/wall_ms
        ]

        manifest = make_budget_manifest.build_manifest(rows)

        self.assertEqual(manifest["by_family"]["math"]["n_samples"], 1)
        self.assertEqual(manifest["by_family"]["math"]["token_budget"], 100)


class MainCliTests(unittest.TestCase):
    def test_main_writes_manifest_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.jsonl"
            rows = [_row("math", 100, 10.0), _row("math", 200, 20.0)]
            with results_path.open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            output_path = Path(tmp) / "budget-manifest.json"

            code = make_budget_manifest.main(
                [str(results_path), "--output", str(output_path), "--coefficient", "1.0"]
            )

            self.assertEqual(code, 0)
            manifest = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["by_family"]["math"]["token_budget"], 150)

    def test_main_merges_multiple_input_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a.jsonl"
            path_b = Path(tmp) / "b.jsonl"
            path_a.write_text(json.dumps(_row("math", 100, 10.0)) + "\n", encoding="utf-8")
            path_b.write_text(json.dumps(_row("math", 300, 30.0)) + "\n", encoding="utf-8")
            output_path = Path(tmp) / "budget-manifest.json"

            code = make_budget_manifest.main(
                [str(path_a), str(path_b), "--output", str(output_path)]
            )

            self.assertEqual(code, 0)
            manifest = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["by_family"]["math"]["n_samples"], 2)
        self.assertEqual(manifest["by_family"]["math"]["token_budget"], 200)

    def test_main_rejects_non_positive_coefficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "results.jsonl"
            results_path.write_text(json.dumps(_row("math", 100, 10.0)) + "\n", encoding="utf-8")

            with self.assertRaises(SystemExit):
                make_budget_manifest.main(
                    [
                        str(results_path),
                        "--output",
                        str(Path(tmp) / "out.json"),
                        "--coefficient",
                        "0",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
