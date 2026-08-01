import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import evaluate_orchestration as eval_script

from fugu_local.config import load_config


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
        self.assertEqual(eval_script._parse_seeds("1, 2,3", None), [1, 2, 3])

    def test_grader_normalizes_latex_markdown_and_unicode_digits(self):
        grader = {
            "type": "regex",
            "pattern": r"\bH2O\b",
            "normalize": True,
        }
        for answer in (
            "H2O",
            "H_2O",
            r"$\text{H}_2\text{O}$",
            "**H2O**",
            "H₂O",
        ):
            with self.subTest(answer=answer):
                self.assertTrue(eval_script._grade(answer, grader))

        with self.assertRaises(ValueError):
            eval_script._grade("H2O", {**grader, "normalize": "yes"})

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
                        "--seeds",
                        "42,43",
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
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(manifest["seed"], 42)
            self.assertEqual(manifest["seeds"], [42, 43])
            self.assertEqual(manifest["temperature_override"], 0.1)
            self.assertEqual(manifest["hardware"]["gpu"], "test-gpu")
            self.assertEqual(
                manifest["conditions"][0]["resolved"]["quantization"],
                "Q4_K_M",
            )
            snapshot = json.loads((output / "inputs/01-single.json").read_text())
            self.assertEqual(snapshot["models"][0]["api_key"], "<redacted>")

            result_lines = (output / "results.jsonl").read_text().splitlines()
            self.assertEqual(len(result_lines), 2)
            row = json.loads(result_lines[0])
            self.assertEqual(row["content"], "Paris")
            self.assertEqual(row["usage"]["total_tokens"], 5)
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["conditions"]["single"]["total_tokens"], 10)
            self.assertEqual(summary["conditions"]["single"]["seeds"], [42, 43])

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
            self.assertEqual(rerun_manifest["seeds"], [42, 43])
            self.assertEqual(
                rerun_manifest["source_manifest"],
                str((output / "manifest.json").resolve()),
            )
            rerun_row = json.loads((rerun_output / "results.jsonl").read_text().splitlines()[0])
            self.assertEqual(rerun_row["content"], "Paris")

    def test_summary_groups_domains_and_reports_uncertainty(self):
        rows = [
            {
                "condition": "A",
                "case_id": "m1",
                "domain": "math",
                "seed": 1,
                "passed": True,
                "wall_ms": 10,
                "error": "",
                "usage": {"total_tokens": 5},
            },
            {
                "condition": "A",
                "case_id": "m1",
                "domain": "math",
                "seed": 2,
                "passed": False,
                "wall_ms": 20,
                "error": "",
                "usage": {"total_tokens": 7},
            },
            {
                "condition": "A",
                "case_id": "q1",
                "domain": "qa",
                "seed": 1,
                "passed": True,
                "wall_ms": 30,
                "error": "",
                "usage": None,
            },
        ]

        metrics = eval_script._summarize(rows)["conditions"]["A"]

        self.assertEqual(metrics["runs"], 3)
        self.assertEqual(metrics["unique_cases"], 2)
        self.assertEqual(metrics["seeds"], [1, 2])
        self.assertEqual(metrics["accuracy"], 0.6667)
        self.assertEqual(metrics["total_tokens"], 12)
        self.assertEqual(set(metrics["domains"]), {"math", "qa"})
        self.assertEqual(metrics["domains"]["math"]["accuracy"], 0.5)
        self.assertLess(metrics["accuracy_ci95"][0], metrics["accuracy"])
        self.assertGreater(metrics["accuracy_ci95"][1], metrics["accuracy"])

    def test_phase1_fixtures_validate(self):
        root = Path("evals/phase1")
        cases = list(eval_script._load_cases(root / "tasks.jsonl"))
        self.assertGreaterEqual(len(cases), 10)
        self.assertEqual(
            {case.domain for case in cases},
            {"math", "reasoning", "qa", "coding"},
        )
        config_paths = sorted((root / "configs").glob("*.json"))
        self.assertEqual(len(config_paths), 7)
        for path in config_paths:
            with self.subTest(path=path):
                config = load_config(str(path))
                self.assertTrue(config.models)
                self.assertTrue(config.roles)


if __name__ == "__main__":
    unittest.main()
