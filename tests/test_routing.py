import unittest

from fugu_local.backends import ChatMessage, ChatRequest, ChatResponse
from fugu_local.config import config_from_dict
from fugu_local.orchestrator import FuguLocalOrchestrator
from fugu_local.routing import ModelRouter, RouterMember


class RecordingBackend:
    def __init__(self, content, *, fail=False):
        self.content = content
        self.fail = fail
        self.calls = 0

    def chat(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"backend {self.content} down")
        return ChatResponse(content=self.content)


def _request():
    return ChatRequest(model="m", messages=[ChatMessage(role="user", content="hi")])


class ModelRouterTests(unittest.TestCase):
    def test_round_robin_rotates_members(self):
        a = RecordingBackend("a")
        b = RecordingBackend("b")
        router = ModelRouter(
            "m",
            [RouterMember("a", a), RouterMember("b", b)],
            policy="round_robin",
        )

        first = router.chat(_request()).content
        second = router.chat(_request()).content
        third = router.chat(_request()).content

        self.assertEqual([first, second, third], ["a", "b", "a"])

    def test_failover_to_next_member(self):
        down = RecordingBackend("down", fail=True)
        up = RecordingBackend("up")
        router = ModelRouter(
            "m",
            [RouterMember("down", down), RouterMember("up", up)],
            policy="round_robin",
        )

        result = router.chat(_request())

        self.assertEqual(result.content, "up")
        self.assertEqual(down.calls, 1)
        self.assertEqual(up.calls, 1)

    def test_raises_when_all_members_fail(self):
        router = ModelRouter(
            "m",
            [
                RouterMember("a", RecordingBackend("a", fail=True)),
                RouterMember("b", RecordingBackend("b", fail=True)),
            ],
            policy="round_robin",
        )

        with self.assertRaises(RuntimeError):
            router.chat(_request())


class PoolOrchestrationTests(unittest.TestCase):
    def _pool_config(self, policy="round_robin"):
        return config_from_dict(
            {
                "models": [
                    {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
                ],
                "model_pools": [
                    {
                        "name": "fast",
                        "backend": "ollama",
                        "model": "gpt-oss:20b",
                        "endpoints": [
                            "http://127.0.0.1:11434",
                            "http://127.0.0.1:11435",
                        ],
                        "policy": policy,
                    }
                ],
                "roles": [
                    {"name": "worker", "model": "fast", "always_include": True},
                    {
                        "name": "synthesizer",
                        "model": "synth-model",
                        "is_synthesizer": True,
                    },
                ],
                "orchestrator": {"selection_policy": "all"},
            }
        )

    def test_role_can_reference_a_pool_and_fail_over(self):
        down = RecordingBackend("down", fail=True)
        up = RecordingBackend("from-11435")
        orchestrator = FuguLocalOrchestrator(
            self._pool_config(),
            backend_overrides={
                "http://127.0.0.1:11434": down,
                "http://127.0.0.1:11435": up,
                "synth-model": RecordingBackend("final"),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        worker = next(w for w in result.worker_results if w.role == "worker")
        self.assertTrue(worker.ok)
        self.assertEqual(worker.content, "from-11435")
        self.assertEqual(down.calls, 1)
        self.assertEqual(up.calls, 1)

    def test_pool_worker_fails_only_when_all_endpoints_fail(self):
        orchestrator = FuguLocalOrchestrator(
            self._pool_config(),
            backend_overrides={
                "http://127.0.0.1:11434": RecordingBackend("a", fail=True),
                "http://127.0.0.1:11435": RecordingBackend("b", fail=True),
                "synth-model": RecordingBackend("final"),
            },
        )

        with self.assertRaises(Exception):
            orchestrator.chat([ChatMessage(role="user", content="hello")])


if __name__ == "__main__":
    unittest.main()
