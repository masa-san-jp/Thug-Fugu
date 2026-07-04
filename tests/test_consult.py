import unittest

from fugu_local.backends import ChatResponse
from fugu_local.config import config_from_dict
from fugu_local.consult import consult
from fugu_local.orchestrator import FuguLocalOrchestrator


class StaticBackend:
    def __init__(self, content):
        self.content = content

    def chat(self, request):
        return ChatResponse(content=self.content)


def _config():
    return config_from_dict(
        {
            "models": [
                {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": [
                {"name": "planner", "model": "planner-model", "always_include": True},
                {"name": "synthesizer", "model": "synth-model", "is_synthesizer": True},
            ],
            "orchestrator": {"selection_policy": "all"},
        }
    )


class ConsultTests(unittest.TestCase):
    def test_consult_returns_structured_result(self):
        orchestrator = FuguLocalOrchestrator(
            _config(),
            backend_overrides={
                "planner-model": StaticBackend("planner output"),
                "synth-model": StaticBackend("final answer"),
            },
        )

        result = consult(_config(), "design something", orchestrator=orchestrator)

        self.assertEqual(result["answer"], "final answer")
        self.assertEqual(result["selected_roles"], ["planner"])
        self.assertEqual(result["synthesizer_role"], "synthesizer")
        self.assertTrue(result["run_id"])
        self.assertEqual(result["workers"][0]["role"], "planner")
        self.assertTrue(result["workers"][0]["ok"])

    def test_consult_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            consult(_config(), "   ")

    def test_consult_result_is_json_serializable(self):
        import json

        orchestrator = FuguLocalOrchestrator(
            _config(),
            backend_overrides={
                "planner-model": StaticBackend("p"),
                "synth-model": StaticBackend("s"),
            },
        )
        result = consult(_config(), "hi", orchestrator=orchestrator)
        json.dumps(result)  # must not raise


if __name__ == "__main__":
    unittest.main()
