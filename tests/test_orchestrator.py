import unittest

from fugu_local.backends import ChatMessage, ChatResponse
from fugu_local.config import config_from_dict
from fugu_local.orchestrator import FuguLocalOrchestrator, OrchestrationError


class StaticBackend:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, request):
        self.calls.append(request)
        return ChatResponse(content=self.content)


class FailingBackend:
    def chat(self, request):
        raise RuntimeError("boom")


def make_config(selection_policy="all", synthesizer=True):
    roles = [
        {
            "name": "planner",
            "model": "planner-model",
            "system_prompt": "plan",
            "keywords": ["plan"],
            "always_include": True,
        },
        {
            "name": "coder",
            "model": "coder-model",
            "system_prompt": "code",
            "keywords": ["code"],
        },
    ]
    if synthesizer:
        roles.append(
            {
                "name": "synthesizer",
                "model": "synth-model",
                "system_prompt": "synth",
                "is_synthesizer": True,
            }
        )
    return config_from_dict(
        {
            "models": [
                {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                {"name": "coder-model", "backend": "echo", "model": "mock-coder"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": roles,
            "orchestrator": {"selection_policy": selection_policy},
        }
    )


class OrchestratorTests(unittest.TestCase):
    def test_all_policy_runs_all_workers_and_synthesizer(self):
        planner = StaticBackend("planner output")
        coder = StaticBackend("coder output")
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": coder,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.content, "final output")
        self.assertEqual(result.selected_roles, ["planner", "coder"])
        self.assertEqual(result.synthesizer_role, "synthesizer")
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(coder.calls), 1)
        self.assertEqual(len(synth.calls), 1)

    def test_keyword_policy_selects_matching_and_always_include_roles(self):
        planner = StaticBackend("planner output")
        coder = StaticBackend("coder output")
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="keyword", synthesizer=False),
            backend_overrides={"planner-model": planner, "coder-model": coder},
        )

        result = orchestrator.chat([ChatMessage(role="user", content="please write code")])

        self.assertEqual(result.selected_roles, ["planner", "coder"])
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(coder.calls), 1)

    def test_keyword_policy_falls_back_to_first_worker(self):
        config = config_from_dict(
            {
                "models": [
                    {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                    {"name": "coder-model", "backend": "echo", "model": "mock-coder"},
                ],
                "roles": [
                    {
                        "name": "planner",
                        "model": "planner-model",
                        "system_prompt": "plan",
                        "keywords": ["plan"],
                    },
                    {
                        "name": "coder",
                        "model": "coder-model",
                        "system_prompt": "code",
                        "keywords": ["code"],
                    },
                ],
                "orchestrator": {"selection_policy": "keyword"},
            }
        )
        planner = StaticBackend("planner output")
        coder = StaticBackend("coder output")
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={"planner-model": planner, "coder-model": coder},
        )

        result = orchestrator.chat([ChatMessage(role="user", content="general question")])

        self.assertEqual(result.selected_roles, ["planner"])
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(coder.calls), 0)

    def test_synthesis_failure_falls_back_to_deterministic_merge(self):
        planner = StaticBackend("planner output")
        coder = StaticBackend("coder output")
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": coder,
                "synth-model": FailingBackend(),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertIn("planner output", result.content)
        self.assertIn("coder output", result.content)
        self.assertEqual(result.synthesizer_role, "synthesizer")
        self.assertIsNotNone(result.synthesis_error)

    def test_all_workers_failed_raises(self):
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all", synthesizer=False),
            backend_overrides={
                "planner-model": FailingBackend(),
                "coder-model": FailingBackend(),
            },
        )

        with self.assertRaises(OrchestrationError):
            orchestrator.chat([ChatMessage(role="user", content="hello")])


if __name__ == "__main__":
    unittest.main()

