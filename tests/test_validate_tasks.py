import json
import tempfile
import unittest
from pathlib import Path

from scripts import validate_tasks


def _make_task(task_id, family, *, difficulty="medium", review_status="pending"):
    return {
        "id": task_id,
        "family": family,
        "difficulty": difficulty,
        "answer_type": "single",
        "prompt": f"prompt for {task_id}",
        "grader": {"type": "exact", "value": task_id},
        "source": "authored",
        "gold": task_id,
        "gold_rationale": "trivial",
        "review_status": review_status,
    }


def _valid_fixture_files(tmp_dir: Path):
    """A minimal, exactly-at-the-boundary fixture that satisfies every
    structural rule in validate_tasks.py: 150 tasks total (30 calibration +
    60 dev + 60 test), >=20 tasks per family total, >=10 per family in the
    test file, 0% easy (well under the 20% cap)."""

    counts = {"calibration": 5, "dev": 10, "test": 10}
    families = sorted(validate_tasks.ALLOWED_FAMILIES)
    paths = []
    for role, count_per_family in counts.items():
        tasks = [
            _make_task(f"{family}-{role}-{i}", family)
            for family in families
            for i in range(count_per_family)
        ]
        path = tmp_dir / f"tasks-v2-{role}.jsonl"
        path.write_text("\n".join(json.dumps(task) for task in tasks) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


class ValidFixtureTests(unittest.TestCase):
    def test_valid_fixture_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _valid_fixture_files(Path(tmp))
            errors = validate_tasks.validate_files(paths)

        self.assertEqual(errors, [])

    def test_main_returns_zero_for_valid_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _valid_fixture_files(Path(tmp))
            code = validate_tasks.main([str(p) for p in paths])

        self.assertEqual(code, 0)


class SchemaViolationTests(unittest.TestCase):
    def _fixture_with_extra_task(self, tmp, role, task):
        paths = _valid_fixture_files(Path(tmp))
        path = next(p for p in paths if role in p.name)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(task) + "\n")
        return paths

    def test_cross_file_duplicate_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _valid_fixture_files(Path(tmp))
            calibration_path = next(p for p in paths if "calibration" in p.name)
            dev_path = next(p for p in paths if "dev" in p.name)
            first_line = calibration_path.read_text(encoding="utf-8").splitlines()[0]
            with dev_path.open("a", encoding="utf-8") as fh:
                fh.write(first_line + "\n")

            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("duplicate id" in e for e in errors))

    def test_unknown_family_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-family-1", "unknown-family")
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("'family'" in e for e in errors))

    def test_exec_grader_type_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-grader-1", "math")
            bad_task["grader"] = {"type": "exec", "code": "print(1)"}
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("grader.type" in e for e in errors))

    def test_freeform_answer_type_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-answer-type-1", "math")
            bad_task["answer_type"] = "freeform"
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("answer_type" in e for e in errors))

    def test_gold_self_consistency_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-gold-1", "math")
            bad_task["gold"] = "totally-different-from-grader-value"
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("self-consistency" in e for e in errors))

    def test_invalid_grader_definition_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-grader-def-1", "math")
            bad_task["grader"] = {"type": "exact"}  # missing required 'value'
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("grader definition is invalid" in e for e in errors))

    def test_invalid_regex_pattern_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-regex-1", "math")
            bad_task["grader"] = {"type": "regex", "pattern": "["}
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("regex grader pattern is invalid" in e for e in errors))

    def test_empty_gold_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_task = _make_task("bad-gold-empty-1", "math")
            bad_task["gold"] = ""
            paths = self._fixture_with_extra_task(tmp, "test", bad_task)
            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("'gold' must be a non-empty string" in e for e in errors))

    def test_invalid_json_line_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _valid_fixture_files(Path(tmp))
            test_path = next(p for p in paths if "test" in p.name)
            with test_path.open("a", encoding="utf-8") as fh:
                fh.write("{not valid json\n")

            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("invalid JSON" in e for e in errors))


class CountViolationTests(unittest.TestCase):
    def test_insufficient_total_count_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks-v2-calibration.jsonl"
            tasks = [_make_task(f"math-{i}", "math") for i in range(5)]
            path.write_text("\n".join(json.dumps(t) for t in tasks) + "\n", encoding="utf-8")

            errors = validate_tasks.validate_files([path])

        self.assertTrue(any("below the required minimum" in e for e in errors))

    def test_easy_ratio_above_cap_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _valid_fixture_files(Path(tmp))
            calibration_path = next(p for p in paths if "calibration" in p.name)
            tasks = [
                json.loads(line)
                for line in calibration_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for task in tasks:
                task["difficulty"] = "easy"
            calibration_path.write_text(
                "\n".join(json.dumps(t) for t in tasks) + "\n", encoding="utf-8"
            )

            errors = validate_tasks.validate_files(paths)

        self.assertTrue(any("easy tasks" in e for e in errors))

    def test_unrecognized_filename_role_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks-v2-mystery.jsonl"
            tasks = [_make_task(f"math-{i}", "math") for i in range(5)]
            path.write_text("\n".join(json.dumps(t) for t in tasks) + "\n", encoding="utf-8")

            errors = validate_tasks.validate_files([path])

        self.assertTrue(any("cannot infer role" in e for e in errors))


class MainCliTests(unittest.TestCase):
    def test_main_returns_nonzero_and_prints_errors_for_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks-v2-calibration.jsonl"
            path.write_text("{}\n", encoding="utf-8")

            code = validate_tasks.main([str(path)])

        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
