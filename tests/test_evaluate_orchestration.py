import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import evaluate_orchestration as eval_script


class FakeOrchestrator:
    def __init__(self, config):
        self.config = config

    def chat(self, messages):
        prompt = messages[-1].content
        if "2 + 3" in prompt:
            content = "5"
        else:
            content = "Paris"
        worker = type("Worker", (), {})()
        result = type("Result", (), {})()
        result.content = content
        result.pattern = "direct"
        result.worker_results = [worker]
        return result


class EvaluateOrchestrationTests(unittest.TestCase):
    def test_graders(self):
        self.assertTrue(eval_script._grade("hello Paris", {"type": "contains", "value": "paris"}))
        self.assertTrue(eval_script._grade("answer: 5", {"type": "regex", "pattern": r"\b5\b"}))
        self.assertTrue(eval_script._grade("  done ", {"type": "exact", "value": "done"}))
        with self.assertRaises(ValueError):
            eval_script._grade("x", {"type": "missing"})

    def test_main_writes_csv_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.jsonl"
            config = root / "config.json"
            csv_path = root / "out.csv"
            summary_path = root / "summary.json"
            cases.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "capital",
                                "prompt": "capital?",
                                "grader": {"type": "contains", "value": "Paris"},
                            }
                        ),
                        json.dumps(
                            {
                                "id": "math",
                                "prompt": "What is 2 + 3?",
                                "grader": {"type": "regex", "pattern": r"\b5\b"},
                            }
                        ),
                    ]
                )
                + "\n"
            )
            config.write_text("{}")

            with (
                mock.patch.object(eval_script, "load_config", return_value={}),
                mock.patch.object(eval_script, "FuguLocalOrchestrator", FakeOrchestrator),
            ):
                code = eval_script.main(
                    [
                        "--cases",
                        str(cases),
                        "--condition",
                        f"A={config}",
                        "--csv",
                        str(csv_path),
                        "--summary",
                        str(summary_path),
                    ]
                )

            self.assertEqual(code, 0)
            self.assertIn("capital", csv_path.read_text())
            summary = json.loads(summary_path.read_text())
            self.assertEqual(summary["conditions"]["A"]["passed"], 2)
            self.assertEqual(summary["conditions"]["A"]["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
