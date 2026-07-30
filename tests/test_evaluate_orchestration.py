import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import evaluate_orchestration as eval_script


class FakeOrchestrator:
    def __init__(self, config):
        self.config = config

    def chat(self, messages, temperature=None):
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
        result.selected_roles = ["solver"]
        result.usage = type(
            "Usage",
            (),
            {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        )()
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

    def test_experiment_bundle_and_manifest_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = root / "cases.jsonl"
            config = root / "config.json"
            metadata = root / "metadata.json"
            hardware = root / "hardware.json"
            output = root / "experiment"
            rerun_output = root / "experiment-rerun"
            cases.write_text(
                json.dumps(
                    {
                        "id": "capital",
                        "prompt": "capital?",
                        "grader": {"type": "contains", "value": "Paris"},
                    }
                )
                + "\n"
            )
            config.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "name": "m",
                                "backend": "echo",
                                "model": "mock-q4",
                                "api_key": "literal-secret",
                            }
                        ],
                        "roles": [{"name": "solver", "model": "m"}],
                    }
                )
            )
            metadata.write_text(json.dumps({"quantization": "Q4_K_M"}))
            hardware.write_text(json.dumps({"gpu": "test-gpu", "ram_gb": 16}))

            with mock.patch.object(eval_script, "FuguLocalOrchestrator", FakeOrchestrator):
                code = eval_script.main(
                    [
                        "--cases",
                        str(cases),
                        "--condition",
                        f"single={config}",
                        "--condition-meta",
                        f"single={metadata}",
                        "--seed",
                        "42",
                        "--temperature",
                        "0.1",
                        "--hardware-json",
                        str(hardware),
                        "--output-dir",
                        str(output),
                    ]
                )

            self.assertEqual(code, 0)
            for name in (
                "manifest.json",
                "results.jsonl",
                "results.csv",
                "summary.json",
                "rerun.sh",
                "inputs/cases.jsonl",
                "inputs/01-single.json",
            ):
                self.assertTrue((output / name).exists(), name)

            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["seed"], 42)
            self.assertEqual(manifest["temperature_override"], 0.1)
            self.assertEqual(manifest["hardware"]["gpu"], "test-gpu")
            self.assertEqual(
                manifest["conditions"][0]["resolved"]["quantization"],
                "Q4_K_M",
            )
            snapshot = json.loads((output / "inputs/01-single.json").read_text())
            self.assertEqual(snapshot["models"][0]["api_key"], "<redacted>")

            row = json.loads((output / "results.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["content"], "Paris")
            self.assertEqual(row["usage"]["total_tokens"], 5)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["conditions"]["single"]["total_tokens"], 5)

            with mock.patch.object(eval_script, "FuguLocalOrchestrator", FakeOrchestrator):
                rerun_code = eval_script.main(
                    [
                        "--rerun-manifest",
                        str(output / "manifest.json"),
                        "--output-dir",
                        str(rerun_output),
                    ]
                )

            self.assertEqual(rerun_code, 0)
            rerun_manifest = json.loads((rerun_output / "manifest.json").read_text())
            self.assertEqual(rerun_manifest["seed"], 42)
            self.assertEqual(
                rerun_manifest["source_manifest"],
                str((output / "manifest.json").resolve()),
            )
            rerun_row = json.loads((rerun_output / "results.jsonl").read_text().splitlines()[0])
            self.assertEqual(rerun_row["content"], "Paris")


if __name__ == "__main__":
    unittest.main()
