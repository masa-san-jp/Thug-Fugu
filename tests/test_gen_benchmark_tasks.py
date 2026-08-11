import importlib.util
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


def _load_script(name):
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_script("gen_benchmark_tasks")
validator = _load_script("validate_tasks")


class GeneratedBenchmarkTaskTests(unittest.TestCase):
    def test_generation_is_deterministic(self):
        first = gen.generate_tasks(per_family_per_difficulty=2, seed=42)
        second = gen.generate_tasks(per_family_per_difficulty=2, seed=42)

        self.assertEqual(first, second)

    def test_different_seed_changes_candidates(self):
        first = gen.generate_tasks(per_family_per_difficulty=1, seed=1)
        second = gen.generate_tasks(per_family_per_difficulty=1, seed=2)

        self.assertNotEqual(first, second)

    def test_every_generated_task_passes_wp2_schema_and_gold_consistency(self):
        tasks = gen.generate_tasks(per_family_per_difficulty=3, seed=7)

        errors = [error for task in tasks for error in validator._validate_task_schema(task)]

        self.assertEqual(errors, [])
        self.assertTrue(all(task["review_status"] == "pending" for task in tasks))
        self.assertTrue(all(task["gold_rationale"] for task in tasks))
        self.assertEqual({task["family"] for task in tasks}, set(gen.FAMILIES))

    def test_math_prompts_use_singular_unit_when_count_is_one(self):
        tasks = gen.generate_tasks(
            per_family_per_difficulty=20,
            seed=23,
            families=("math",),
        )

        for task in tasks:
            self.assertIsNone(re.search(r"\b1 (?:more )?units\b", task["prompt"]))
            self.assertIsNone(re.search(r"\b1 pallets\b", task["prompt"]))

    def test_coding_gold_is_from_executing_emitted_program(self):
        tasks = gen.generate_tasks(
            per_family_per_difficulty=3,
            seed=29,
            families=("coding",),
        )

        for task in tasks:
            program = (
                task["prompt"].split("\n\n", 1)[1].split("\nAnswer with the integer only.", 1)[0]
            )
            output = io.StringIO()
            with redirect_stdout(output):
                exec(program, {"__builtins__": {"print": print, "range": range}}, {})
            actual = output.getvalue().strip()
            self.assertEqual(task["gold"], actual)
            self.assertEqual(task["gold_rationale"], f"executed program output: {actual}")

    def test_logic_gold_has_exactly_one_exhaustive_solution(self):
        tasks = gen.generate_tasks(
            per_family_per_difficulty=2,
            seed=11,
            families=("logic",),
        )

        self.assertTrue(all("unique solution:" in task["gold_rationale"] for task in tasks))

    def test_long_context_candidates_exceed_minimum_context_length(self):
        tasks = gen.generate_tasks(
            per_family_per_difficulty=1,
            seed=13,
            families=("long_context",),
        )

        self.assertTrue(all(len(task["prompt"]) >= 2000 for task in tasks))

    def test_planning_gold_is_exhaustively_counted(self):
        tasks = gen.generate_tasks(
            per_family_per_difficulty=1,
            seed=17,
            families=("planning",),
        )

        self.assertTrue(all("exhaustive enumeration" in task["gold_rationale"] for task in tasks))

    def test_main_writes_jsonl_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "candidates.jsonl"
            code = gen.main(
                [
                    "--output",
                    str(output),
                    "--per-family-per-difficulty",
                    "1",
                    "--seed",
                    "5",
                ]
            )
            rows = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(code, 0)
        self.assertEqual(len(rows), len(gen.FAMILIES) * len(gen.DIFFICULTIES))
        self.assertTrue(all(row["review_status"] == "pending" for row in rows))

    def test_draft_splits_pass_full_structural_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = gen.main(["--output-dir", tmp, "--seed", "20260810"])
            paths = [
                Path(tmp) / "tasks-v2-calibration.jsonl",
                Path(tmp) / "tasks-v2-dev.jsonl",
                Path(tmp) / "tasks-v2-test.jsonl",
            ]
            errors = validator.validate_files(paths)
            rows = [json.loads(line) for path in paths for line in path.read_text().splitlines()]

        self.assertEqual(code, 0)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 150)
        self.assertEqual(len({row["id"] for row in rows}), 150)
        self.assertEqual(len({row["prompt"] for row in rows}), 150)
        self.assertTrue(all(row["review_status"] == "pending" for row in rows))


if __name__ == "__main__":
    unittest.main()
