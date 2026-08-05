import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import evaluate_orchestration as eval_script

from fugu_local.config import config_from_dict, load_config


class FakeWorkerResult:
    def __init__(self, role, model, content, *, ok=True, usage=None):
        self.role = role
        self.model = model
        self.content = content
        self.ok = ok
        self.usage = usage


class FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class FakeOrchestrator:
    def __init__(self, config):
        self.config = config
        self.calls = []

    def chat(self, messages, temperature=None, seed=None):
        self.calls.append(seed)
        prompt = messages[-1].content
        if "2 + 3" in prompt:
            content = "5"
        else:
            content = "Paris"
        result = type("Result", (), {})()
        result.content = content
        result.pattern = "direct"
        result.worker_results = [
            FakeWorkerResult(
                "solver",
                "mock",
                content,
                usage=FakeUsage(2, 3, 5),
            )
        ]
        result.selected_roles = ["solver"]
        result.usage = FakeUsage(2, 3, 5)
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
            self.assertEqual(manifest["schema_version"], 3)
            self.assertEqual(manifest["seed"], 42)
            self.assertEqual(manifest["seeds"], [42, 43])
            self.assertEqual(manifest["repeats"], 1)
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
            self.assertEqual(row["repeat_index"], 0)
            self.assertFalse(row["seed_sent"])  # condition's only model is backend=echo
            self.assertEqual(row["worker_outputs"][0]["role"], "solver")
            self.assertTrue(row["worker_outputs"][0]["passed"])
            self.assertEqual(row["stage_results"], [])
            summary = json.loads((output / "summary.json").read_text())
            self.assertEqual(summary["schema_version"], 3)
            self.assertEqual(summary["sample_unit"], "unique_task")
            self.assertEqual(summary["conditions"]["single"]["tokens_total"], 10)
            self.assertEqual(summary["conditions"]["single"]["seeds"], [42, 43])
            self.assertEqual(summary["conditions"]["single"]["task_scores"], {"capital": 1.0})

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

    def test_accuracy_uses_task_level_mean(self):
        # m1 has one pass and one fail across its two repeats -> task score 0.5.
        # q1 has a single repeat that passes -> task score 1.0. The condition's
        # accuracy is the mean of *task* scores (0.75), not the mean of the
        # three individual runs (2/3 = 0.6667) -- that run-level mean is what
        # WP-1 replaces, since it double-counts tasks with more repeats/seeds.
        rows = [
            {
                "condition": "A",
                "case_id": "m1",
                "domain": "math",
                "seed": 0,
                "repeat_index": 0,
                "passed": True,
                "wall_ms": 10,
                "error": "",
                "usage": {"total_tokens": 5},
            },
            {
                "condition": "A",
                "case_id": "m1",
                "domain": "math",
                "seed": 0,
                "repeat_index": 1,
                "passed": False,
                "wall_ms": 20,
                "error": "",
                "usage": {"total_tokens": 7},
            },
            {
                "condition": "A",
                "case_id": "q1",
                "domain": "qa",
                "seed": 0,
                "repeat_index": 0,
                "passed": True,
                "wall_ms": 30,
                "error": "",
                "usage": None,
            },
        ]

        summary = eval_script._summarize(rows, repeats=2)
        metrics = summary["conditions"]["A"]

        self.assertEqual(summary["schema_version"], 3)
        self.assertEqual(summary["sample_unit"], "unique_task")
        self.assertEqual(summary["n_tasks"], 2)
        self.assertEqual(summary["repeats"], 2)
        self.assertEqual(metrics["task_scores"], {"m1": 0.5, "q1": 1.0})
        self.assertEqual(metrics["accuracy"], 0.75)
        self.assertEqual(metrics["by_domain"], {"math": 0.5, "qa": 1.0})
        self.assertEqual(metrics["tokens_total"], 12)
        self.assertEqual(metrics["runs"], 3)
        self.assertEqual(metrics["unique_cases"], 2)
        self.assertEqual(metrics["seeds"], [0])
        self.assertGreater(metrics["accuracy_stderr"], 0.0)

    def test_repeats_are_aggregated_per_task(self):
        rows = [
            {
                "condition": "A",
                "case_id": "c1",
                "domain": "math",
                "seed": 0,
                "repeat_index": i,
                "passed": passed,
                "wall_ms": 1.0,
                "error": "",
                "usage": None,
            }
            for i, passed in enumerate([True, False, True, False])
        ]

        summary = eval_script._summarize(rows, repeats=4)

        self.assertEqual(summary["conditions"]["A"]["task_scores"]["c1"], 0.5)
        self.assertEqual(summary["repeats"], 4)

    def test_repeats_with_multiple_seeds_is_rejected(self):
        with self.assertRaises(SystemExit):
            eval_script.main(
                [
                    "--cases",
                    "unused.jsonl",
                    "--condition",
                    "A=unused.json",
                    "--repeats",
                    "2",
                    "--seeds",
                    "1,2",
                    "--csv",
                    "unused.csv",
                    "--summary",
                    "unused.json",
                ]
            )

    def test_paired_bootstrap_ci_is_deterministic(self):
        first = eval_script._paired_bootstrap_ci([0.1, -0.2, 0.3, 0.0])
        second = eval_script._paired_bootstrap_ci([0.1, -0.2, 0.3, 0.0])

        self.assertEqual(first, second)

    def test_paired_comparison_reports_baseline_vs_candidate(self):
        rows = [
            {
                "condition": "baseline",
                "case_id": "c1",
                "domain": "math",
                "seed": 0,
                "repeat_index": 0,
                "passed": False,
                "wall_ms": 1.0,
                "error": "",
                "usage": None,
            },
            {
                "condition": "candidate",
                "case_id": "c1",
                "domain": "math",
                "seed": 0,
                "repeat_index": 0,
                "passed": True,
                "wall_ms": 1.0,
                "error": "",
                "usage": None,
            },
        ]

        summary = eval_script._summarize(rows, repeats=1)

        self.assertEqual(len(summary["paired"]), 1)
        comparison = summary["paired"][0]
        self.assertEqual(comparison["baseline"], "baseline")
        self.assertEqual(comparison["candidate"], "candidate")
        self.assertEqual(comparison["n_tasks"], 1)
        self.assertEqual(comparison["n_excluded"], 0)
        self.assertEqual(comparison["mean_diff"], 1.0)
        self.assertEqual(comparison["method"], "paired_bootstrap")

    def test_seed_sent_flag_is_false_for_echo_backend(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "solver", "model": "m"}],
            }
        )
        orchestrator = FakeOrchestrator(config)
        case = eval_script.EvalCase(
            case_id="capital",
            prompt="capital?",
            grader={"type": "contains", "value": "Paris"},
        )
        condition = eval_script.Condition(label="A", config_path=Path("unused.json"))

        row = eval_script._run_case(
            condition, orchestrator, case, seed=0, repeat_index=0, repeat_seed=0
        )

        self.assertFalse(row["seed_sent"])

    def test_seed_sent_flag_is_true_for_ollama_backend(self):
        config = config_from_dict(
            {
                "models": [
                    {
                        "name": "m",
                        "backend": "ollama",
                        "model": "mock",
                        "base_url": "http://localhost:11434",
                    }
                ],
                "roles": [{"name": "solver", "model": "m"}],
            }
        )
        orchestrator = FakeOrchestrator(config)
        case = eval_script.EvalCase(
            case_id="capital",
            prompt="capital?",
            grader={"type": "contains", "value": "Paris"},
        )
        condition = eval_script.Condition(label="A", config_path=Path("unused.json"))

        row = eval_script._run_case(
            condition, orchestrator, case, seed=0, repeat_index=0, repeat_seed=0
        )

        self.assertTrue(row["seed_sent"])

    def test_worker_outputs_include_per_worker_passed(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [
                    {"name": "planner", "model": "m"},
                    {"name": "coder", "model": "m"},
                ],
            }
        )

        class MultiWorkerOrchestrator:
            def __init__(self, config):
                self.config = config

            def chat(self, messages, temperature=None, seed=None):
                result = type("Result", (), {})()
                result.content = "final"
                result.pattern = "role_split"
                result.worker_results = [
                    FakeWorkerResult("planner", "m", "final"),
                    FakeWorkerResult("coder", "m", "wrong"),
                ]
                result.selected_roles = ["planner", "coder"]
                result.usage = None
                return result

        orchestrator = MultiWorkerOrchestrator(config)
        case = eval_script.EvalCase(
            case_id="c1", prompt="p", grader={"type": "exact", "value": "final"}
        )
        condition = eval_script.Condition(label="A", config_path=Path("unused.json"))

        row = eval_script._run_case(
            condition, orchestrator, case, seed=0, repeat_index=0, repeat_seed=0
        )

        outputs = {output["role"]: output for output in row["worker_outputs"]}
        self.assertTrue(outputs["planner"]["passed"])
        self.assertFalse(outputs["coder"]["passed"])

    def test_legacy_manifest_schema_can_be_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "legacy"
            inputs_dir = output / "inputs"
            inputs_dir.mkdir(parents=True)
            cases_snapshot = inputs_dir / "cases.jsonl"
            cases_snapshot.write_text(
                json.dumps(
                    {
                        "id": "capital",
                        "prompt": "capital?",
                        "grader": {"type": "contains", "value": "Paris"},
                    }
                )
                + "\n"
            )
            config_snapshot = inputs_dir / "01-single.json"
            config_snapshot.write_text(
                json.dumps(
                    {
                        "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                        "roles": [{"name": "solver", "model": "m"}],
                    }
                )
            )
            manifest_path = output / "manifest.json"
            manifest = {
                "schema_version": 2,
                "seed": 7,
                "seeds": [7],
                "temperature_override": None,
                "cases": {
                    "source_path": str(root / "does-not-exist.jsonl"),
                    "path": "inputs/cases.jsonl",
                    "sha256": eval_script._sha256(cases_snapshot),
                    "count": 1,
                },
                "conditions": [
                    {
                        "label": "single",
                        "source_config_path": str(root / "does-not-exist.json"),
                        "config_path": "inputs/01-single.json",
                        "config_sha256": eval_script._sha256(config_snapshot),
                        "metadata": {},
                    }
                ],
                "hardware": {},
            }
            manifest_path.write_text(json.dumps(manifest))
            rerun_output = root / "legacy-rerun"

            with mock.patch.object(eval_script, "FuguLocalOrchestrator", FakeOrchestrator):
                code = eval_script.main(
                    ["--rerun-manifest", str(manifest_path), "--output-dir", str(rerun_output)]
                )

            self.assertEqual(code, 0)
            rerun_manifest = json.loads((rerun_output / "manifest.json").read_text())
            self.assertEqual(rerun_manifest["schema_version"], 3)
            self.assertEqual(rerun_manifest["repeats"], 1)
            self.assertEqual(rerun_manifest["seeds"], [7])

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
