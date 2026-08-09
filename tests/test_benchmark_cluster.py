import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import benchmark_cluster

from fugu_local.backends import ChatMessage, ChatRequest, ChatResponse
from fugu_local.config import HealthCheckConfig, ModelPoolConfig
from fugu_local.routing import ModelRouter, RouterMember


class StubBackend:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        if self.fail:
            raise RuntimeError("stub backend failure")
        return ChatResponse(content="ok")


def _single_member_router(backend, key="member-1"):
    return ModelRouter("mock-model", [RouterMember(key=key, backend=backend)])


def _ping_request(router: ModelRouter) -> ChatRequest:
    return ChatRequest(
        model=router.model_string, messages=[ChatMessage(role="user", content="ping")]
    )


class PercentileTests(unittest.TestCase):
    def test_empty_input_returns_none(self):
        self.assertIsNone(benchmark_cluster._percentile_ms([], 50))

    def test_nearest_rank_matches_known_values(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(benchmark_cluster._percentile_ms(values, 50), 30.0)
        self.assertEqual(benchmark_cluster._percentile_ms(values, 100), 50.0)
        self.assertEqual(benchmark_cluster._percentile_ms(values, 1), 10.0)

    def test_unsorted_input_is_sorted_first(self):
        values = [50.0, 10.0, 30.0, 40.0, 20.0]
        self.assertEqual(benchmark_cluster._percentile_ms(values, 50), 30.0)


class SplitEvenlyTests(unittest.TestCase):
    def test_splits_remainder_across_first_parts(self):
        self.assertEqual(benchmark_cluster._split_evenly(10, 3), [4, 3, 3])
        self.assertEqual(benchmark_cluster._split_evenly(9, 3), [3, 3, 3])
        self.assertEqual(benchmark_cluster._split_evenly(1, 4), [1, 0, 0, 0])


class RunBenchmarkLevelTests(unittest.TestCase):
    def test_all_successes_report_full_success_rate_and_latency_ordering(self):
        router = _single_member_router(StubBackend())

        result = benchmark_cluster.run_benchmark_level(
            lambda: router.chat(_ping_request(router)),
            concurrency=4,
            total_requests=20,
        )

        self.assertEqual(result.total_requests, 20)
        self.assertEqual(result.successes, 20)
        self.assertEqual(result.failures, 0)
        self.assertEqual(result.success_rate_pct, 100.0)
        self.assertGreater(result.throughput_rps, 0)
        self.assertIsNotNone(result.latency_p50_ms)
        self.assertLessEqual(result.latency_p50_ms, result.latency_p95_ms)
        self.assertLessEqual(result.latency_p95_ms, result.latency_p99_ms)

    def test_all_failures_report_zero_success_rate_and_no_latency(self):
        router = _single_member_router(StubBackend(fail=True))

        result = benchmark_cluster.run_benchmark_level(
            lambda: router.chat(_ping_request(router)),
            concurrency=2,
            total_requests=10,
        )

        self.assertEqual(result.failures, 10)
        self.assertEqual(result.successes, 0)
        self.assertEqual(result.success_rate_pct, 0.0)
        self.assertIsNone(result.latency_p50_ms)
        self.assertIsNone(result.latency_p95_ms)
        self.assertIsNone(result.latency_p99_ms)

    def test_rejects_non_positive_concurrency(self):
        with self.assertRaises(ValueError):
            benchmark_cluster.run_benchmark_level(lambda: None, concurrency=0, total_requests=5)

    def test_rejects_non_positive_total_requests(self):
        with self.assertRaises(ValueError):
            benchmark_cluster.run_benchmark_level(lambda: None, concurrency=1, total_requests=0)

    def test_does_not_use_real_sockets(self):
        router = _single_member_router(StubBackend())
        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network call attempted")
        ):
            result = benchmark_cluster.run_benchmark_level(
                lambda: router.chat(_ping_request(router)),
                concurrency=2,
                total_requests=6,
            )
        self.assertEqual(result.successes, 6)


class ConcurrencySweepTests(unittest.TestCase):
    def test_sweep_runs_one_level_result_per_concurrency_value(self):
        router = _single_member_router(StubBackend())

        results = benchmark_cluster.run_concurrency_sweep(
            lambda: router.chat(_ping_request(router)),
            concurrency_levels=[1, 2, 4],
            requests_per_level=6,
        )

        self.assertEqual([r.concurrency for r in results], [1, 2, 4])
        self.assertTrue(all(r.total_requests == 6 for r in results))


class MemberOutageInjectionTests(unittest.TestCase):
    def test_outage_with_no_failover_partner_drops_success_rate_to_zero(self):
        backend = StubBackend()
        router = _single_member_router(backend, key="only-member")
        baseline = benchmark_cluster.run_benchmark_level(
            lambda: router.chat(_ping_request(router)), concurrency=1, total_requests=5
        )
        self.assertEqual(baseline.success_rate_pct, 100.0)

        benchmark_cluster.inject_member_outage(router, "only-member")
        degraded = benchmark_cluster.run_benchmark_level(
            lambda: router.chat(_ping_request(router)), concurrency=1, total_requests=5
        )

        self.assertEqual(degraded.success_rate_pct, 0.0)

    def test_outage_with_healthy_failover_partner_keeps_high_success_rate(self):
        router = ModelRouter(
            "mock-model",
            [
                RouterMember(key="a", backend=StubBackend()),
                RouterMember(key="b", backend=StubBackend()),
            ],
        )

        benchmark_cluster.inject_member_outage(router, "a")
        degraded = benchmark_cluster.run_benchmark_level(
            lambda: router.chat(_ping_request(router)), concurrency=2, total_requests=10
        )

        self.assertEqual(degraded.success_rate_pct, 100.0)

    def test_unknown_member_key_raises(self):
        router = _single_member_router(StubBackend())
        with self.assertRaises(ValueError):
            benchmark_cluster.inject_member_outage(router, "does-not-exist")


class BuildPoolRouterTests(unittest.TestCase):
    def test_builds_a_router_with_one_member_per_endpoint(self):
        pool = ModelPoolConfig(
            name="test-pool",
            backend="echo",
            model="mock",
            endpoints=["fake://host-a", "fake://host-b"],
            health=HealthCheckConfig(),
        )

        router = benchmark_cluster._build_pool_router(pool)

        self.assertEqual(len(router.members), 2)
        response = router.chat(_ping_request(router))
        self.assertIn("echo", response.content)


class SafeEndpointTests(unittest.TestCase):
    def test_strips_credentials_and_query(self):
        self.assertEqual(
            benchmark_cluster._safe_endpoint("http://user:secret@host:1234/path?token=abc"),
            "http://host:1234/path",
        )


class MainEndToEndTests(unittest.TestCase):
    def _pool_config(self, endpoints):
        return type(
            "FakeConfig",
            (),
            {
                "model_pools": [
                    ModelPoolConfig(
                        name="pool",
                        backend="echo",
                        model="mock",
                        endpoints=endpoints,
                        health=HealthCheckConfig(),
                    )
                ]
            },
        )()

    def test_writes_baseline_only_json_output(self):
        config = self._pool_config(["fake://a"])
        with mock.patch.object(benchmark_cluster, "load_config", return_value=config):
            with tempfile.TemporaryDirectory() as tmp:
                output_path = Path(tmp) / "cluster-benchmark.json"
                code = benchmark_cluster.main(
                    [
                        "--config",
                        "unused.json",
                        "--pool",
                        "pool",
                        "--concurrency-levels",
                        "1,2",
                        "--requests-per-level",
                        "4",
                        "--output",
                        str(output_path),
                    ]
                )

                self.assertEqual(code, 0)
                payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("baseline", payload["conditions"])
        self.assertNotIn("degraded", payload["conditions"])
        self.assertEqual(len(payload["conditions"]["baseline"]), 2)
        self.assertIn("NOT a quality/accuracy measurement", payload["note"])

    def test_writes_baseline_and_degraded_json_output_with_outage_member(self):
        config = self._pool_config(["fake://a", "fake://b"])
        with mock.patch.object(benchmark_cluster, "load_config", return_value=config):
            with tempfile.TemporaryDirectory() as tmp:
                output_path = Path(tmp) / "cluster-benchmark.json"
                code = benchmark_cluster.main(
                    [
                        "--config",
                        "unused.json",
                        "--pool",
                        "pool",
                        "--concurrency-levels",
                        "2",
                        "--requests-per-level",
                        "4",
                        "--outage-member",
                        "fake://a",
                        "--output",
                        str(output_path),
                    ]
                )

                self.assertEqual(code, 0)
                payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertIn("degraded", payload["conditions"])
        self.assertEqual(payload["outage_member"], "fake://a/")

    def test_unknown_pool_name_errors(self):
        config = self._pool_config(["fake://a"])
        with mock.patch.object(benchmark_cluster, "load_config", return_value=config):
            with self.assertRaises(SystemExit):
                benchmark_cluster.main(
                    [
                        "--config",
                        "unused.json",
                        "--pool",
                        "does-not-exist",
                        "--output",
                        "out.json",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
