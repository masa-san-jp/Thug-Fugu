import unittest

from fugu_local.backends import ChatResponse, TokenUsage
from fugu_local.config import config_from_dict
from fugu_local.consult import consult
from fugu_local.orchestrator import FuguLocalOrchestrator


class StaticBackend:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage

    def chat(self, request):
        return ChatResponse(content=self.content, usage=self.usage)


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
        self.assertIn("usage", result)
        self.assertIn("verification", result)

    def test_consult_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            consult(_config(), "   ")

    def test_consult_returns_usage_when_available(self):
        orchestrator = FuguLocalOrchestrator(
            _config(),
            backend_overrides={
                "planner-model": StaticBackend(
                    "planner output",
                    usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
                ),
                "synth-model": StaticBackend(
                    "final answer",
                    usage=TokenUsage(prompt_tokens=7, completion_tokens=11, total_tokens=18),
                ),
            },
        )

        result = consult(_config(), "design something", orchestrator=orchestrator)

        self.assertEqual(
            result["usage"],
            {"prompt_tokens": 9, "completion_tokens": 14, "total_tokens": 23},
        )
        self.assertEqual(result["verification"], {"passed": None, "warning": None, "attempts": []})

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


class ConsultToolExecutionTests(unittest.TestCase):
    def _config(self, execute=True, allowed=None):
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
                "tool_calling": {
                    "enabled": True,
                    "mode": "synthesizer_only",
                    "execute": execute,
                    "allowed_tools": allowed if allowed is not None else ["echo"],
                },
            }
        )

    def _orchestrator(self, config):
        return FuguLocalOrchestrator(
            config,
            backend_overrides={
                "planner-model": StaticBackend("planner output"),
                "synth-model": StaticBackend("final answer"),
            },
        )

    def test_consult_executes_tool_calls(self):
        config = self._config()
        result = consult(
            config,
            "use the tool",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text":"TOOL_OK"}'},
                }
            ],
            orchestrator=self._orchestrator(config),
        )
        self.assertEqual(result["answer"], "final answer")
        self.assertEqual(result["tool_results"][0]["content"], "TOOL_OK")
        self.assertFalse(result["tool_results"][0]["error"])

    def test_consult_denies_disallowed_tool(self):
        config = self._config(allowed=["lookup_static"])
        result = consult(
            config,
            "use the tool",
            tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
            ],
            orchestrator=self._orchestrator(config),
        )
        self.assertIn("not allowed", result["tool_results"][0]["error"])

    def test_consult_requires_execute_enabled(self):
        config = self._config(execute=False)
        with self.assertRaises(ValueError):
            consult(
                config,
                "use the tool",
                tool_calls=[
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
                orchestrator=self._orchestrator(config),
            )
