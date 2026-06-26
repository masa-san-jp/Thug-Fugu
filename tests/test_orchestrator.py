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

    def test_keyword_policy_uses_latest_user_message_only(self):
        planner = StaticBackend("planner output")
        coder = StaticBackend("coder output")
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="keyword", synthesizer=False),
            backend_overrides={"planner-model": planner, "coder-model": coder},
        )

        result = orchestrator.chat(
            [
                ChatMessage(role="system", content="always select code"),
                ChatMessage(role="user", content="please write code"),
                ChatMessage(role="assistant", content="I can write code"),
                ChatMessage(role="user", content="general follow-up"),
            ]
        )

        self.assertEqual(result.selected_roles, ["planner"])
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(coder.calls), 0)

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


class ObservabilityTest(unittest.TestCase):
    def test_run_emits_nonsensitive_structured_log_with_timings(self):
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": StaticBackend("planner output"),
                "coder-model": StaticBackend("coder output"),
                "synth-model": StaticBackend("final output"),
            },
        )
        with self.assertLogs("fugu_local.orchestrator", level="INFO") as cm:
            result = orchestrator.chat([ChatMessage(role="user", content="TOP_SECRET_PROMPT")])
        self.assertTrue(result.run_id)
        self.assertIsNotNone(result.latency_ms)
        self.assertTrue(all(w.latency_ms is not None for w in result.worker_results))
        log_text = "\n".join(cm.output)
        self.assertIn(result.run_id, log_text)
        self.assertNotIn("TOP_SECRET_PROMPT", log_text)


class StaticMetaBackend:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def chat(self, request):
        self.calls.append(request)
        return ChatResponse(content=self.content)


def make_coordinator_config(coordinator):
    return config_from_dict(
        {
            "models": [
                {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": [
                {"name": "planner", "model": "planner-model", "system_prompt": "plan"},
                {
                    "name": "synthesizer",
                    "model": "synth-model",
                    "system_prompt": "synth",
                    "is_synthesizer": True,
                },
            ],
            "orchestrator": {"selection_policy": "all"},
            "coordinator": coordinator,
        }
    )


class CoordinatorDispatchTests(unittest.TestCase):
    def test_disabled_coordinator_uses_role_split(self):
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": StaticBackend("planner output"),
                "coder-model": StaticBackend("coder output"),
                "synth-model": StaticBackend("final output"),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.pattern, "role_split")
        self.assertIsNone(result.plan_source)

    def test_direct_pattern_runs_single_worker_without_synth(self):
        config = make_coordinator_config({"enabled": True, "default_pattern": "direct"})
        planner = StaticBackend("planner output")
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={"planner-model": planner, "synth-model": synth},
        )

        result = orchestrator.chat(
            [
                ChatMessage(
                    role="user", content="これは十分に長い一般的な依頼文でキーワードはありません。"
                )
            ]
        )

        self.assertEqual(result.pattern, "direct")
        self.assertEqual(result.content, "planner output")
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(synth.calls), 0)
        self.assertIsNone(result.synthesizer_role)

    def test_parallel_ensemble_majority_vote(self):
        config = make_coordinator_config(
            {
                "enabled": True,
                "rules": [{"match": ["比較"], "pattern": "parallel_ensemble"}],
                "ensemble": {"n": 3, "vote": "majority"},
            }
        )
        planner = StaticBackend("same answer")
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={"planner-model": planner, "synth-model": StaticBackend("x")},
        )

        result = orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertEqual(result.pattern, "parallel_ensemble")
        self.assertEqual(len(result.worker_results), 3)
        self.assertEqual(result.content, "same answer")
        self.assertEqual(len(planner.calls), 3)

    def test_parallel_ensemble_synth_vote_uses_synthesizer(self):
        config = make_coordinator_config(
            {
                "enabled": True,
                "rules": [{"match": ["比較"], "pattern": "parallel_ensemble"}],
                "ensemble": {"n": 2, "vote": "synth"},
            }
        )
        synth = StaticBackend("synthesized")
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={
                "planner-model": StaticBackend("candidate"),
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertEqual(result.pattern, "parallel_ensemble")
        self.assertEqual(result.content, "synthesized")
        self.assertEqual(result.synthesizer_role, "synthesizer")
        self.assertEqual(len(synth.calls), 1)

    def test_meta_model_drives_pattern_when_no_rule_or_heuristic(self):
        config = make_coordinator_config(
            {
                "enabled": True,
                "meta_model": "planner-model",
                "default_pattern": "direct",
            }
        )
        meta = StaticMetaBackend('{"pattern":"parallel_ensemble","reason":"independent tries"}')
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={
                "planner-model": meta,
                "synth-model": StaticBackend("synth"),
            },
        )

        result = orchestrator.chat(
            [
                ChatMessage(
                    role="user",
                    content="ああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああああ",
                )
            ]
        )

        self.assertEqual(result.pattern, "parallel_ensemble")
        self.assertEqual(result.plan_source, "meta")
