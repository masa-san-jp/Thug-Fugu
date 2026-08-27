import copy
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import benchmark_parallelism


class PercentileTests(unittest.TestCase):
    def test_nearest_rank_and_empty_input(self):
        self.assertEqual(benchmark_parallelism.percentile([50, 10, 30, 40, 20], 50), 30.0)
        self.assertEqual(benchmark_parallelism.percentile([10, 20, 30], 99), 30.0)
        self.assertIsNone(benchmark_parallelism.percentile([], 95))


class ArtifactTests(unittest.TestCase):
    def test_artifact_has_all_required_scenarios_and_stable_shape(self):
        first = benchmark_parallelism.build_artifact(
            runs=1, seed=123, latency_ms=2.0, cold_start_ms=1.0, commit="abc1234"
        )
        second = benchmark_parallelism.build_artifact(
            runs=1, seed=123, latency_ms=2.0, cold_start_ms=1.0, commit="abc1234"
        )

        self.assertEqual(
            {scenario["id"] for scenario in first["scenarios"]},
            benchmark_parallelism.REQUIRED_SCENARIOS,
        )
        self.assertEqual(first["config_hash"], second["config_hash"])
        self.assertEqual(_shape(first), _shape(second))
        self.assertEqual(benchmark_parallelism.validate_artifact(first), [])
        self.assertEqual(len(first["comparisons"]), 2)
        self.assertTrue(all("worker_speedup" in comparison for comparison in first["comparisons"]))

    def test_failure_and_timeout_are_recorded_as_samples(self):
        artifact = benchmark_parallelism.build_artifact(
            runs=1, seed=1, latency_ms=3.0, cold_start_ms=0.0, commit="abc1234"
        )
        scenarios = {scenario["id"]: scenario for scenario in artifact["scenarios"]}

        failure = scenarios["worker_failure"]["samples"][0]
        self.assertEqual(failure["failures"], 1)
        self.assertEqual(
            sum(worker["error_kind"] == "backend_failure" for worker in failure["worker_results"]),
            1,
        )

        timeout = scenarios["deadline_expiration"]["samples"][0]
        self.assertGreater(timeout["timeouts"], 0)
        self.assertEqual(timeout["failures"], timeout["timeouts"])

    def test_json_and_jsonl_round_trip_through_validator(self):
        artifact = benchmark_parallelism.build_artifact(
            runs=1, seed=2, latency_ms=0.5, cold_start_ms=0.0, commit="abc1234"
        )
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            json_path = directory_path / "artifact.json"
            jsonl_path = directory_path / "artifact.jsonl"
            benchmark_parallelism._write_artifact(json_path, artifact, "json")
            benchmark_parallelism._write_artifact(jsonl_path, artifact, "jsonl")

            self.assertEqual(benchmark_parallelism.validate_artifact_file(json_path), [])
            self.assertEqual(benchmark_parallelism.validate_artifact_file(jsonl_path), [])
            self.assertEqual(
                benchmark_parallelism._read_artifact(jsonl_path)["config_hash"],
                artifact["config_hash"],
            )

    def test_benchmark_does_not_open_sockets(self):
        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network call attempted")
        ):
            artifact = benchmark_parallelism.build_artifact(
                runs=1, seed=4, latency_ms=0.5, cold_start_ms=0.0, commit="abc1234"
            )
        self.assertEqual(benchmark_parallelism.validate_artifact(artifact), [])

    def test_validator_reports_malformed_jsonl_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.jsonl"
            path.write_text(
                '{"record_type":"manifest"}\n{"record_type":"scenario"}\n',
                encoding="utf-8",
            )
            errors = benchmark_parallelism.validate_artifact_file(path)
        self.assertTrue(any("cannot read artifact" in error for error in errors))

    def test_validator_rejects_missing_fields_and_secrets(self):
        artifact = benchmark_parallelism.build_artifact(
            runs=1, seed=3, latency_ms=0.5, cold_start_ms=0.0, commit="abc1234"
        )
        missing = copy.deepcopy(artifact)
        del missing["config_hash"]
        self.assertTrue(
            any(
                "config_hash" in error
                for error in benchmark_parallelism.validate_artifact(missing)
            )
        )

        secret = copy.deepcopy(artifact)
        secret["backend"]["api_key"] = "sk-test-secret-value"
        errors = benchmark_parallelism.validate_artifact(secret)
        self.assertTrue(any("sensitive" in error for error in errors))


def _shape(value):
    if isinstance(value, dict):
        return {key: _shape(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(child) for child in value]
    return type(value).__name__


if __name__ == "__main__":
    unittest.main()
