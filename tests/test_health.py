import threading
import unittest
from unittest import mock

from fugu_local.backends import ChatResponse
from fugu_local.config import HealthCheckConfig, ModelPoolConfig
from fugu_local.health import HealthMonitor
from fugu_local.routing import ModelRouter, RouterMember


class StubBackend:
    def chat(self, request):
        return ChatResponse(content="ok")


class HealthMonitorTests(unittest.TestCase):
    def _pool(self, *, interval_seconds=30.0):
        return ModelPoolConfig(
            name="fast",
            backend="ollama",
            model="gpt-oss:20b",
            endpoints=["http://one.test", "http://two.test"],
            health=HealthCheckConfig(
                enabled=True,
                interval_seconds=interval_seconds,
                timeout_seconds=1.0,
                failure_threshold=1,
                success_threshold=1,
            ),
        )

    def _router(self):
        return ModelRouter(
            "gpt-oss:20b",
            [
                RouterMember("http://one.test", StubBackend()),
                RouterMember("http://two.test", StubBackend()),
            ],
            active_health_enabled=True,
            health_failure_threshold=1,
            health_success_threshold=1,
        )

    def _openai_pool(self):
        return ModelPoolConfig(
            name="openai",
            backend="openai-compatible",
            model="local-model",
            endpoints=["http://openai.test"],
            api_key="secret",
            health=HealthCheckConfig(
                enabled=True,
                timeout_seconds=1.5,
                require_model=True,
            ),
        )

    def _openai_router(self):
        return ModelRouter(
            "local-model",
            [RouterMember("http://openai.test", StubBackend())],
            active_health_enabled=True,
        )

    def test_poll_once_updates_each_member(self):
        router = self._router()
        results = {"http://one.test": True, "http://two.test": False}
        monitor = HealthMonitor(
            {"fast": router},
            [self._pool()],
            probe=lambda target: results[target.member_key],
        )

        monitor.poll_once()

        health = {item["endpoint"]: item for item in router.health_snapshot()}
        self.assertEqual(health["http://one.test/"]["state"], "healthy")
        self.assertEqual(health["http://two.test/"]["state"], "unhealthy")
        self.assertIsNotNone(health["http://one.test/"]["last_probe_at"])

    def test_probe_exception_marks_member_unhealthy(self):
        router = self._router()

        def failing_probe(target):
            raise RuntimeError("probe failed")

        monitor = HealthMonitor({"fast": router}, [self._pool()], probe=failing_probe)

        monitor.poll_once()

        self.assertTrue(all(item["state"] == "unhealthy" for item in router.health_snapshot()))

    def test_background_monitor_starts_immediately_and_stops_cleanly(self):
        router = self._router()
        probed = threading.Event()

        def probe(target):
            probed.set()
            return True

        monitor = HealthMonitor(
            {"fast": router},
            [self._pool(interval_seconds=60.0)],
            probe=probe,
        )

        monitor.start()
        self.assertTrue(probed.wait(timeout=1))
        self.assertTrue(monitor.running)

        monitor.stop()

        self.assertFalse(monitor.running)

    def test_default_probe_uses_ollama_tags_probe(self):
        router = self._router()
        monitor = HealthMonitor({"fast": router}, [self._pool()])

        with mock.patch("fugu_local.health.probe_ollama", return_value=True) as probe:
            monitor.poll_once()

        self.assertEqual(probe.call_count, 2)
        probe.assert_any_call(
            "http://one.test",
            timeout_seconds=1.0,
            model="gpt-oss:20b",
            require_model=False,
        )

    def test_default_probe_supports_openai_compatible_backend(self):
        router = self._openai_router()
        monitor = HealthMonitor({"openai": router}, [self._openai_pool()])

        with mock.patch(
            "fugu_local.health.probe_openai_compatible",
            return_value=True,
        ) as probe:
            monitor.poll_once()

        probe.assert_called_once_with(
            "http://openai.test",
            timeout_seconds=1.5,
            api_key="secret",
            model="local-model",
            require_model=True,
        )
        self.assertEqual(router.health_snapshot()[0]["state"], "healthy")

    def test_disabled_pool_has_no_targets_or_thread(self):
        router = self._router()
        pool = ModelPoolConfig(
            name="fast",
            backend="ollama",
            model="gpt-oss:20b",
            endpoints=["http://one.test"],
        )
        monitor = HealthMonitor({"fast": router}, [pool])

        monitor.start()

        self.assertEqual(monitor.targets, [])
        self.assertFalse(monitor.running)


if __name__ == "__main__":
    unittest.main()
