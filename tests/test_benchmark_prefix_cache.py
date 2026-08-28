import json
import tempfile
import unittest
from pathlib import Path

from scripts import benchmark_prefix_cache as benchmark


class PrefixCacheBenchmarkTests(unittest.TestCase):
    def test_portable_contract_preserves_shared_prefix_and_role_boundaries(self):
        contract = benchmark.portable_contract()

        self.assertTrue(contract["common_prefix_first_message_identical"])
        self.assertTrue(contract["current_first_message_differs_by_role"])
        self.assertTrue(contract["role_instruction_remains_system_message"])
        self.assertTrue(contract["task_remains_user_message"])
        self.assertTrue(contract["user_text_is_never_promoted_to_system"])
        self.assertEqual(contract["raw_prompt_and_completion_output"], "omitted")

    def test_fixture_generates_sanitized_comparison_artifact(self):
        root = Path(__file__).parent / "fixtures/prefix-cache"
        manifest_path = root / "manifest-ollama.json"
        fixture_path = root / "ollama.json"

        artifact = benchmark.run_benchmark(
            manifest_path,
            fixture_path=fixture_path,
            case_names=("short",),
            workers=2,
            repeats=2,
        )

        self.assertEqual(artifact["artifact_type"], "prefix_cache_spike")
        self.assertEqual(artifact["verification"]["status"], "unverified")
        self.assertFalse(artifact["verification"]["real_runtime"])
        self.assertEqual(artifact["experiment"]["variants"], list(benchmark.VARIANTS))
        self.assertEqual(artifact["experiment"]["cases"], ["short"])
        self.assertEqual(
            artifact["results"]["short"]["common_prefix"]["summary"]["successful_records"],
            4,
        )
        self.assertEqual(
            artifact["results"]["short"]["runtime_native"]["summary"]["cache_hit"]["status"],
            "direct",
        )
        self.assertGreater(
            artifact["comparisons"]["short"]["common_prefix_vs_current"][
                "prompt_eval_time_reduction_pct"
            ],
            0,
        )
        self.assertEqual(artifact["go_no_go"]["decision"], "human_gate")
        self.assertTrue(artifact["sanitization"]["raw_prompts_and_completions_omitted"])
        serialized = json.dumps(artifact, ensure_ascii=False)
        self.assertNotIn("Shared context paragraph", serialized)
        self.assertNotIn("Ignore previous instructions", serialized)

    def test_missing_direct_cache_hit_is_explicitly_estimated_or_unknown(self):
        records = [
            {
                "repeat": 0,
                "worker": 1,
                "ok": True,
                "prompt_eval_time_ms": 100,
                "cache_hit": {"status": "unknown", "method": "not_exposed"},
            },
            {
                "repeat": 1,
                "worker": 1,
                "ok": True,
                "prompt_eval_time_ms": 50,
                "cache_hit": {"status": "unknown", "method": "not_exposed"},
            },
        ]

        result = benchmark._estimate_cache_hit(records)

        self.assertEqual(result["status"], "estimated")
        self.assertIn("heuristic", result["method"])
        self.assertEqual(result["reduction_pct"], 50.0)

    def test_negative_fixture_metric_is_rejected(self):
        with self.assertRaises(benchmark.PrefixCacheError):
            benchmark._normalize_record({"ttft_ms": -1}, repeat=0, worker=1)

    def test_cli_writes_fixture_artifact_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "prefix-cache.json"
            result = benchmark.main(
                [
                    "--manifest",
                    "tests/fixtures/prefix-cache/manifest-ollama.json",
                    "--fixture",
                    "tests/fixtures/prefix-cache/ollama.json",
                    "--cases",
                    "short",
                    "--workers",
                    "2",
                    "--repeats",
                    "2",
                    "--output",
                    str(output),
                ]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(payload["runtime"]["name"], "ollama")
        self.assertEqual(payload["results"]["short"]["current"]["summary"]["records"], 4)


if __name__ == "__main__":
    unittest.main()
