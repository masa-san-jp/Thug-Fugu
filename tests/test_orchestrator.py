import unittest

from fugu_local.backends import ChatMessage, ChatResponse, ChatStreamChunk, TokenUsage
from fugu_local.config import config_from_dict
from fugu_local.orchestrator import FuguLocalOrchestrator, OrchestrationError, derive_seed


class StaticBackend:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage
        self.calls = []

    def chat(self, request):
        self.calls.append(request)
        return ChatResponse(content=self.content, usage=self.usage)


class StreamingStaticBackend(StaticBackend):
    def __init__(self, content, usage=None):
        super().__init__(content, usage=usage)
        self.stream_calls = []

    def stream_chat(self, request):
        self.stream_calls.append(request)
        yield ChatStreamChunk(delta=self.content[:3])
        yield ChatStreamChunk(delta=self.content[3:])
        yield ChatStreamChunk(finish_reason="stop", usage=self.usage)


class FailingBackend:
    def chat(self, request):
        raise RuntimeError("boom")


class SequenceBackend:
    def __init__(self, contents, usages=None):
        self.contents = list(contents)
        self.usages = list(usages or [])
        self.calls = []

    def chat(self, request):
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        usage = self.usages[index] if index < len(self.usages) else None
        return ChatResponse(content=self.contents[index], usage=usage)


class ResponseSequenceBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, request):
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


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


def make_backend_tool_config(*, execute):
    return config_from_dict(
        {
            "models": [
                {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": [
                {
                    "name": "planner",
                    "model": "planner-model",
                    "always_include": True,
                },
                {
                    "name": "synthesizer",
                    "model": "synth-model",
                    "is_synthesizer": True,
                },
            ],
            "tool_calling": {
                "enabled": True,
                "mode": "synthesizer_only",
                "execute": execute,
                "allowed_tools": ["echo"] if execute else [],
            },
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

    def test_usage_aggregates_workers_and_synthesizer(self):
        planner = StaticBackend(
            "planner output",
            usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )
        coder = StaticBackend(
            "coder output",
            usage=TokenUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9),
        )
        synth = StaticBackend(
            "final output",
            usage=TokenUsage(prompt_tokens=6, completion_tokens=7, total_tokens=13),
        )
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": coder,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.prompt_tokens, 12)
        self.assertEqual(result.usage.completion_tokens, 15)
        self.assertEqual(result.usage.total_tokens, 27)


if __name__ == "__main__":
    unittest.main()


class BackendToolLoopTests(unittest.TestCase):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "echo evidence",
                "parameters": {"type": "object"},
            },
        }
    ]

    def test_proposal_mode_preserves_backend_tool_calls(self):
        proposal = ChatResponse(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": '{"text":"evidence"}',
                    },
                }
            ],
            finish_reason="tool_calls",
        )
        synth = ResponseSequenceBackend([proposal])
        orchestrator = FuguLocalOrchestrator(
            make_backend_tool_config(execute=False),
            backend_overrides={
                "planner-model": StaticBackend("worker output"),
                "synth-model": synth,
            },
        )

        result = orchestrator.chat_with_backend_tools(
            [ChatMessage(role="user", content="use a tool")],
            tools=self.tools,
            tool_choice="required",
        )

        self.assertEqual(result.content, "")
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(result.tool_calls, proposal.tool_calls)
        self.assertEqual(result.tool_results, [])
        self.assertEqual(synth.calls[0].tools, self.tools)
        self.assertEqual(synth.calls[0].tool_choice, "required")

    def test_execute_mode_runs_one_allowed_tool_round(self):
        proposal = ChatResponse(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": '{"text":"local evidence"}',
                    },
                }
            ],
            finish_reason="tool_calls",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )
        final = ChatResponse(
            content="final answer",
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9),
        )
        synth = ResponseSequenceBackend([proposal, final])
        worker = StaticBackend(
            "worker output",
            usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )
        orchestrator = FuguLocalOrchestrator(
            make_backend_tool_config(execute=True),
            backend_overrides={
                "planner-model": worker,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat_with_backend_tools(
            [ChatMessage(role="user", content="use a tool")],
            tools=self.tools,
        )

        self.assertEqual(result.content, "final answer")
        self.assertEqual(result.finish_reason, "stop")
        self.assertIsNone(result.tool_calls)
        self.assertEqual(result.tool_results[0].content, "local evidence")
        self.assertEqual(len(synth.calls), 2)
        self.assertIsNone(synth.calls[1].tools)
        self.assertIn("local evidence", synth.calls[1].messages[-1].content)
        self.assertEqual(result.usage.total_tokens, 17)

    def test_malformed_backend_arguments_fail_without_execution(self):
        synth = ResponseSequenceBackend(
            [
                ChatResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": "{not-json",
                            },
                        }
                    ],
                )
            ]
        )
        orchestrator = FuguLocalOrchestrator(
            make_backend_tool_config(execute=True),
            backend_overrides={
                "planner-model": StaticBackend("worker output"),
                "synth-model": synth,
            },
        )

        with self.assertRaises(OrchestrationError):
            orchestrator.chat_with_backend_tools(
                [ChatMessage(role="user", content="use a tool")],
                tools=self.tools,
            )

    def test_disallowed_generated_tool_is_captured_as_evidence(self):
        proposal = ChatResponse(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "lookup_static",
                        "arguments": '{"key":"x","data":{}}',
                    },
                }
            ],
        )
        synth = ResponseSequenceBackend([proposal, ChatResponse(content="safe final")])
        orchestrator = FuguLocalOrchestrator(
            make_backend_tool_config(execute=True),
            backend_overrides={
                "planner-model": StaticBackend("worker output"),
                "synth-model": synth,
            },
        )

        result = orchestrator.chat_with_backend_tools(
            [ChatMessage(role="user", content="use a tool")],
            tools=self.tools,
        )

        self.assertEqual(result.content, "safe final")
        self.assertIn("not allowed", result.tool_results[0].error)
        self.assertIn("not allowed", synth.calls[1].messages[-1].content)

    def test_second_generated_tool_round_is_rejected(self):
        proposal = ChatResponse(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": '{"text":"evidence"}',
                    },
                }
            ],
        )
        synth = ResponseSequenceBackend([proposal, proposal])
        orchestrator = FuguLocalOrchestrator(
            make_backend_tool_config(execute=True),
            backend_overrides={
                "planner-model": StaticBackend("worker output"),
                "synth-model": synth,
            },
        )

        with self.assertRaises(OrchestrationError) as ctx:
            orchestrator.chat_with_backend_tools(
                [ChatMessage(role="user", content="use a tool")],
                tools=self.tools,
            )

        self.assertIn("maximum is one", str(ctx.exception))


def make_verifier_config(max_retries=1, enabled=True, explicit_role=False):
    verify = {"enabled": enabled, "max_retries": max_retries}
    if explicit_role:
        verify["role"] = "verifier"
    return config_from_dict(
        {
            "models": [
                {"name": "worker-model", "backend": "echo", "model": "mock-worker"},
                {"name": "verifier-model", "backend": "echo", "model": "mock-verifier"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": [
                {"name": "worker", "model": "worker-model", "system_prompt": "work"},
                {
                    "name": "verifier",
                    "model": "verifier-model",
                    "system_prompt": "verify",
                    "is_verifier": not explicit_role,
                },
                {
                    "name": "synthesizer",
                    "model": "synth-model",
                    "system_prompt": "synth",
                    "is_synthesizer": True,
                },
            ],
            "orchestrator": {"selection_policy": "all"},
            "coordinator": {"verify": verify},
        }
    )


class VerifierRetryTests(unittest.TestCase):
    def test_verify_disabled_preserves_existing_flow(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": false, "critique": "should not run"}')
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(enabled=False),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.content, "final output")
        self.assertEqual(result.selected_roles, ["worker"])
        self.assertEqual(result.verification_attempts, [])
        self.assertIsNone(result.verification_passed)
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(len(verifier.calls), 0)

    def test_verifier_pass_short_circuits_without_retry(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": true, "critique": ""}')
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=2),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.content, "final output")
        self.assertTrue(result.verification_passed)
        self.assertEqual(len(result.verification_attempts), 1)
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(len(synth.calls), 1)

    def test_verifier_fail_then_pass_retries_workers_once(self):
        worker = StaticBackend("worker output")
        verifier = SequenceBackend(
            [
                '{"pass": false, "critique": "add risks"}',
                '{"pass": true, "critique": ""}',
            ]
        )
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=1),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.content, "final output")
        self.assertTrue(result.verification_passed)
        self.assertEqual(len(result.verification_attempts), 2)
        self.assertEqual(len(worker.calls), 2)
        self.assertEqual(len(verifier.calls), 2)
        retry_prompt = worker.calls[1].messages[-1].content
        self.assertIn("Verifier critique", retry_prompt)
        self.assertIn("add risks", retry_prompt)

    def test_usage_includes_retried_workers_verifier_attempts_and_synthesizer(self):
        worker = StaticBackend(
            "worker output",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )
        verifier = SequenceBackend(
            [
                '{"pass": false, "critique": "add risks"}',
                '{"pass": true, "critique": ""}',
            ],
            usages=[
                TokenUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7),
                TokenUsage(prompt_tokens=5, completion_tokens=6, total_tokens=11),
            ],
        )
        synth = StaticBackend(
            "final output",
            usage=TokenUsage(prompt_tokens=7, completion_tokens=8, total_tokens=15),
        )
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=1),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertTrue(result.verification_passed)
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.prompt_tokens, 17)
        self.assertEqual(result.usage.completion_tokens, 22)
        self.assertEqual(result.usage.total_tokens, 39)

    def test_verifier_budget_exhaustion_returns_best_available_with_warning(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend("FAIL missing evidence")
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=1),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertFalse(result.verification_passed)
        self.assertEqual(len(result.verification_attempts), 2)
        self.assertEqual(len(worker.calls), 2)
        self.assertEqual(len(verifier.calls), 2)
        self.assertIsNotNone(result.verification_warning)
        self.assertTrue(result.content.startswith("Warning: verification did not pass"))
        self.assertIn("final output", result.content)

    def test_verifier_retry_budget_is_never_exceeded(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": false, "critique": "still wrong"}')
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=2),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": StaticBackend("final output"),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertFalse(result.verification_passed)
        self.assertEqual(len(result.verification_attempts), 3)
        self.assertEqual(len(worker.calls), 3)
        self.assertEqual(len(verifier.calls), 3)

    def test_explicit_verify_role_is_excluded_from_workers(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": true}')
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=1, explicit_role=True),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": StaticBackend("final output"),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.selected_roles, ["worker"])
        self.assertEqual(len(worker.calls), 1)
        self.assertEqual(len(verifier.calls), 1)


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

    def test_direct_pattern_exposes_backend_stream_when_supported(self):
        config = make_coordinator_config({"enabled": True, "default_pattern": "direct"})
        planner = StreamingStaticBackend(
            "streamed output",
            usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={
                "planner-model": planner,
                "synth-model": StaticBackend("unused"),
            },
        )

        stream = orchestrator.stream_direct_if_available(
            [ChatMessage(role="user", content="short question")]
        )

        self.assertIsNotNone(stream)
        chunks = list(stream)
        self.assertEqual("".join(chunk.delta for chunk in chunks), "streamed output")
        self.assertEqual(chunks[-1].usage.total_tokens, 5)
        self.assertEqual(planner.stream_calls[0].messages[0].role, "system")

    def test_role_split_does_not_expose_direct_stream(self):
        planner = StreamingStaticBackend("streamed output")
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": StaticBackend("coder"),
                "synth-model": StaticBackend("synth"),
            },
        )

        stream = orchestrator.stream_direct_if_available(
            [ChatMessage(role="user", content="short question")]
        )

        self.assertIsNone(stream)
        self.assertEqual(planner.stream_calls, [])

    def test_role_split_prepares_synthesizer_stream_after_workers(self):
        planner = StaticBackend(
            "planner output",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )
        coder = StaticBackend(
            "coder output",
            usage=TokenUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9),
        )
        synth = StreamingStaticBackend(
            "streamed synthesis",
            usage=TokenUsage(prompt_tokens=6, completion_tokens=7, total_tokens=13),
        )
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": coder,
                "synth-model": synth,
            },
        )

        prepared = orchestrator.prepare_streaming_response(
            [ChatMessage(role="user", content="design and implement")]
        )

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.pattern, "role_split")
        self.assertEqual(
            prepared.progress,
            {"phase": "workers_done", "ok": 2, "failed": 0},
        )
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(len(coder.calls), 1)
        self.assertEqual(synth.stream_calls, [])
        self.assertIn("planner output", prepared.fallback_content)

        chunks = list(prepared.chunks)
        self.assertEqual("".join(chunk.delta for chunk in chunks), "streamed synthesis")
        self.assertEqual(chunks[-1].usage.prompt_tokens, 11)
        self.assertEqual(chunks[-1].usage.completion_tokens, 14)
        self.assertEqual(chunks[-1].usage.total_tokens, 25)
        self.assertIn("planner output", synth.stream_calls[0].messages[1].content)
        self.assertIn("coder output", synth.stream_calls[0].messages[1].content)

    def test_role_split_does_not_run_workers_when_synthesizer_cannot_stream(self):
        planner = StaticBackend("planner output")
        coder = StaticBackend("coder output")
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": coder,
                "synth-model": StaticBackend("buffered synthesis"),
            },
        )

        prepared = orchestrator.prepare_streaming_response(
            [ChatMessage(role="user", content="design and implement")]
        )

        self.assertIsNone(prepared)
        self.assertEqual(planner.calls, [])
        self.assertEqual(coder.calls, [])

    def test_role_split_stream_preserves_worker_usage_when_synth_usage_is_missing(self):
        planner = StaticBackend(
            "planner output",
            usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )
        coder = StaticBackend(
            "coder output",
            usage=TokenUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9),
        )
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": planner,
                "coder-model": coder,
                "synth-model": StreamingStaticBackend("streamed synthesis"),
            },
        )

        prepared = orchestrator.prepare_streaming_response(
            [ChatMessage(role="user", content="design and implement")]
        )
        chunks = list(prepared.chunks)

        self.assertEqual(chunks[-1].usage.prompt_tokens, 5)
        self.assertEqual(chunks[-1].usage.completion_tokens, 7)
        self.assertEqual(chunks[-1].usage.total_tokens, 12)

    def test_role_split_streaming_raises_when_all_workers_fail(self):
        orchestrator = FuguLocalOrchestrator(
            make_config(selection_policy="all"),
            backend_overrides={
                "planner-model": FailingBackend(),
                "coder-model": FailingBackend(),
                "synth-model": StreamingStaticBackend("unused"),
            },
        )

        with self.assertRaises(OrchestrationError):
            orchestrator.prepare_streaming_response(
                [ChatMessage(role="user", content="design and implement")]
            )

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


class CoordinatorObservabilityTests(unittest.TestCase):
    def test_meta_reason_is_not_logged_even_if_it_echoes_prompt(self):
        secret = "TOP_SECRET_PROMPT"
        config = make_coordinator_config(
            {
                "enabled": True,
                "meta_model": "planner-model",
                "default_pattern": "role_split",
            }
        )
        meta_backend = StaticMetaBackend(
            '{"pattern":"direct","reason":"because user said TOP_SECRET_PROMPT"}'
        )
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={
                "planner-model": meta_backend,
                "synth-model": StaticBackend("synth"),
            },
        )

        with self.assertLogs("fugu_local.orchestrator", level="INFO") as cm:
            result = orchestrator.chat([ChatMessage(role="user", content=secret + ("x" * 90))])

        self.assertEqual(result.pattern, "direct")
        self.assertEqual(result.plan_source, "meta")
        self.assertEqual(result.plan_reason, "meta-call selected direct")
        log_text = "\n".join(cm.output)
        self.assertNotIn(secret, log_text)
        self.assertNotIn("because user said", log_text)


class SleepBackend:
    def __init__(self, content, delay_seconds):
        self.content = content
        self.delay_seconds = delay_seconds
        self.calls = []

    def chat(self, request):
        import time

        self.calls.append(request)
        time.sleep(self.delay_seconds)
        return ChatResponse(content=self.content)


def make_deadline_config(request_timeout_seconds=None):
    orchestrator = {"selection_policy": "all", "max_parallel_workers": 2}
    if request_timeout_seconds is not None:
        orchestrator["request_timeout_seconds"] = request_timeout_seconds
    return config_from_dict(
        {
            "models": [
                {"name": "fast-model", "backend": "echo", "model": "mock-fast"},
                {"name": "slow-model", "backend": "echo", "model": "mock-slow"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": [
                {"name": "fast", "model": "fast-model"},
                {"name": "slow", "model": "slow-model"},
                {
                    "name": "synthesizer",
                    "model": "synth-model",
                    "is_synthesizer": True,
                },
            ],
            "orchestrator": orchestrator,
        }
    )


class RequestDeadlineTests(unittest.TestCase):
    def test_default_no_deadline_waits_for_all_workers(self):
        orchestrator = FuguLocalOrchestrator(
            make_deadline_config(),
            backend_overrides={
                "fast-model": StaticBackend("fast output"),
                "slow-model": SleepBackend("slow output", 0.03),
                "synth-model": StaticBackend("final output"),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(result.content, "final output")
        self.assertTrue(all(not worker.timed_out for worker in result.worker_results))
        self.assertIn("slow output", [worker.content for worker in result.worker_results])

    def test_deadline_returns_partial_result_when_one_worker_succeeds(self):
        orchestrator = FuguLocalOrchestrator(
            make_deadline_config(request_timeout_seconds=0.02),
            backend_overrides={
                "fast-model": StaticBackend("fast output"),
                "slow-model": SleepBackend("slow output", 0.08),
                "synth-model": StaticBackend("final output"),
            },
        )

        result = orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertIn("fast output", result.content)
        self.assertIsNone(result.synthesizer_role)
        self.assertEqual(len(result.worker_results), 2)
        timed_out = [worker for worker in result.worker_results if worker.timed_out]
        self.assertEqual([worker.role for worker in timed_out], ["slow"])
        self.assertIn("deadline", timed_out[0].error)

    def test_deadline_raises_when_all_workers_timeout(self):
        orchestrator = FuguLocalOrchestrator(
            make_deadline_config(request_timeout_seconds=0.01),
            backend_overrides={
                "fast-model": SleepBackend("fast output", 0.08),
                "slow-model": SleepBackend("slow output", 0.08),
                "synth-model": StaticBackend("final output"),
            },
        )

        with self.assertRaises(OrchestrationError) as ctx:
            orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertIn("deadline", str(ctx.exception))


class DeriveSeedTests(unittest.TestCase):
    def test_derive_seed_is_deterministic(self):
        first = derive_seed(42, "worker:planner")
        second = derive_seed(42, "worker:planner")

        self.assertEqual(first, second)
        self.assertIsNone(derive_seed(None, "worker:planner"))

    def test_derive_seed_differs_per_role(self):
        planner_seed = derive_seed(42, "worker:planner")
        coder_seed = derive_seed(42, "worker:coder")

        self.assertNotEqual(planner_seed, coder_seed)

    def test_derive_seed_stable_when_role_order_changes(self):
        def build(role_order):
            return config_from_dict(
                {
                    "models": [
                        {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                        {"name": "coder-model", "backend": "echo", "model": "mock-coder"},
                    ],
                    "roles": [
                        role
                        for name in role_order
                        for role in (
                            [
                                {
                                    "name": "planner",
                                    "model": "planner-model",
                                    "always_include": True,
                                }
                            ]
                            if name == "planner"
                            else [
                                {
                                    "name": "coder",
                                    "model": "coder-model",
                                    "always_include": True,
                                }
                            ]
                        )
                    ],
                }
            )

        planner_first = StaticBackend("planner output")
        coder_first = StaticBackend("coder output")
        orchestrator_planner_first = FuguLocalOrchestrator(
            build(["planner", "coder"]),
            backend_overrides={"planner-model": planner_first, "coder-model": coder_first},
        )
        planner_second = StaticBackend("planner output")
        coder_second = StaticBackend("coder output")
        orchestrator_coder_first = FuguLocalOrchestrator(
            build(["coder", "planner"]),
            backend_overrides={"planner-model": planner_second, "coder-model": coder_second},
        )

        orchestrator_planner_first.chat([ChatMessage(role="user", content="hello")], seed=42)
        orchestrator_coder_first.chat([ChatMessage(role="user", content="hello")], seed=42)

        self.assertEqual(planner_first.calls[-1].seed, planner_second.calls[-1].seed)
        self.assertEqual(coder_first.calls[-1].seed, coder_second.calls[-1].seed)


class SeedPropagationTests(unittest.TestCase):
    def test_seed_is_propagated_to_all_worker_requests(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": true, "critique": ""}')
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=2),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        orchestrator.chat([ChatMessage(role="user", content="hello")], seed=42)

        self.assertEqual(worker.calls[-1].seed, derive_seed(42, "worker:worker"))
        self.assertEqual(verifier.calls[-1].seed, derive_seed(42, "verifier:attempt1"))
        self.assertEqual(synth.calls[-1].seed, derive_seed(42, "synthesizer"))

    def test_seed_none_leaves_requests_unseeded(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": true, "critique": ""}')
        synth = StaticBackend("final output")
        orchestrator = FuguLocalOrchestrator(
            make_verifier_config(max_retries=2),
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertIsNone(worker.calls[-1].seed)
        self.assertIsNone(verifier.calls[-1].seed)
        self.assertIsNone(synth.calls[-1].seed)

    def test_orchestrator_config_seed_is_used_when_chat_seed_omitted(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": true, "critique": ""}')
        synth = StaticBackend("final output")
        config_with_seed = config_from_dict(
            {
                "models": [
                    {"name": "worker-model", "backend": "echo", "model": "mock-worker"},
                    {"name": "verifier-model", "backend": "echo", "model": "mock-verifier"},
                    {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
                ],
                "roles": [
                    {"name": "worker", "model": "worker-model", "system_prompt": "work"},
                    {
                        "name": "verifier",
                        "model": "verifier-model",
                        "system_prompt": "verify",
                        "is_verifier": True,
                    },
                    {
                        "name": "synthesizer",
                        "model": "synth-model",
                        "system_prompt": "synth",
                        "is_synthesizer": True,
                    },
                ],
                "orchestrator": {"selection_policy": "all", "seed": 20260802},
                "coordinator": {"verify": {"enabled": True, "max_retries": 2}},
            }
        )
        orchestrator = FuguLocalOrchestrator(
            config_with_seed,
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        orchestrator.chat([ChatMessage(role="user", content="hello")])

        self.assertEqual(worker.calls[-1].seed, derive_seed(20260802, "worker:worker"))

    def test_explicit_chat_seed_overrides_orchestrator_config_seed(self):
        worker = StaticBackend("worker output")
        verifier = StaticBackend('{"pass": true, "critique": ""}')
        synth = StaticBackend("final output")
        config_with_seed = config_from_dict(
            {
                "models": [
                    {"name": "worker-model", "backend": "echo", "model": "mock-worker"},
                    {"name": "verifier-model", "backend": "echo", "model": "mock-verifier"},
                    {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
                ],
                "roles": [
                    {"name": "worker", "model": "worker-model", "system_prompt": "work"},
                    {
                        "name": "verifier",
                        "model": "verifier-model",
                        "system_prompt": "verify",
                        "is_verifier": True,
                    },
                    {
                        "name": "synthesizer",
                        "model": "synth-model",
                        "system_prompt": "synth",
                        "is_synthesizer": True,
                    },
                ],
                "orchestrator": {"selection_policy": "all", "seed": 1},
                "coordinator": {"verify": {"enabled": True, "max_retries": 2}},
            }
        )
        orchestrator = FuguLocalOrchestrator(
            config_with_seed,
            backend_overrides={
                "worker-model": worker,
                "verifier-model": verifier,
                "synth-model": synth,
            },
        )

        orchestrator.chat([ChatMessage(role="user", content="hello")], seed=99)

        self.assertEqual(worker.calls[-1].seed, derive_seed(99, "worker:worker"))

    def test_seed_differs_across_parallel_ensemble_members(self):
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

        orchestrator.chat([ChatMessage(role="user", content="2案を比較して")], seed=42)

        # Workers run concurrently, so only compare the set of seeds actually
        # used, not the order in which they were recorded.
        seeds = {call.seed for call in planner.calls}
        expected = {derive_seed(42, f"worker:planner#{index}") for index in range(1, 4)}
        self.assertEqual(len(planner.calls), 3)
        self.assertEqual(seeds, expected)


def make_judge_tiebreak_config(n=2, max_parallel_workers=1):
    return config_from_dict(
        {
            "models": [
                {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
                {"name": "judge-model", "backend": "echo", "model": "mock-judge"},
                {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
            ],
            "roles": [
                {"name": "planner", "model": "planner-model", "system_prompt": "plan"},
                {
                    "name": "judge",
                    "model": "judge-model",
                    "system_prompt": "judge",
                    "is_verifier": True,
                },
                {
                    "name": "synthesizer",
                    "model": "synth-model",
                    "system_prompt": "synth",
                    "is_synthesizer": True,
                },
            ],
            "orchestrator": {
                "selection_policy": "all",
                "max_parallel_workers": max_parallel_workers,
            },
            "coordinator": {
                "enabled": True,
                "rules": [{"match": ["比較"], "pattern": "parallel_ensemble"}],
                "ensemble": {"n": n, "vote": "judge_tiebreak"},
            },
        }
    )


class EnsembleVoteTests(unittest.TestCase):
    def test_majority_vote_normalizes_equivalent_answers(self):
        config = make_coordinator_config(
            {
                "enabled": True,
                "rules": [{"match": ["比較"], "pattern": "parallel_ensemble"}],
                "ensemble": {"n": 3, "vote": "majority"},
            }
        )
        # "42" and "**42**" normalize to the same answer; "43" does not.
        planner = SequenceBackend(["42", "**42**", "43"])
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={"planner-model": planner, "synth-model": StaticBackend("x")},
        )

        result = orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertIn(result.content, {"42", "**42**"})
        self.assertEqual(result.vote_summary.clusters, 2)
        self.assertEqual(result.vote_summary.winning_votes, 2)
        self.assertTrue(result.vote_summary.normalized)
        self.assertFalse(result.vote_summary.judge_called)

    def test_majority_vote_exact_mode_when_normalize_false(self):
        config = make_coordinator_config(
            {
                "enabled": True,
                "rules": [{"match": ["比較"], "pattern": "parallel_ensemble"}],
                "ensemble": {"n": 3, "vote": "majority", "normalize": False},
            }
        )
        # Under exact matching, "42" and "**42**" are distinct strings, so the
        # two "**42**" answers form the majority instead of all three tying.
        planner = SequenceBackend(["42", "**42**", "**42**"])
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={"planner-model": planner, "synth-model": StaticBackend("x")},
        )

        result = orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertEqual(result.content, "**42**")
        self.assertEqual(result.vote_summary.clusters, 2)
        self.assertEqual(result.vote_summary.winning_votes, 2)
        self.assertFalse(result.vote_summary.normalized)

    def test_judge_tiebreak_called_only_on_tie(self):
        tie_judge = StaticBackend('{"choice": 1}')
        tied_planner = SequenceBackend(["42", "43"])
        tied_orchestrator = FuguLocalOrchestrator(
            make_judge_tiebreak_config(n=2),
            backend_overrides={
                "planner-model": tied_planner,
                "judge-model": tie_judge,
                "synth-model": StaticBackend("x"),
            },
        )

        tied_result = tied_orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertEqual(len(tie_judge.calls), 1)
        self.assertTrue(tied_result.vote_summary.judge_called)
        self.assertEqual(tied_result.content, "43")  # judge chose index 1

        clear_judge = StaticBackend('{"choice": 0}')
        clear_planner = SequenceBackend(["42", "42", "43"])
        clear_orchestrator = FuguLocalOrchestrator(
            make_judge_tiebreak_config(n=3),
            backend_overrides={
                "planner-model": clear_planner,
                "judge-model": clear_judge,
                "synth-model": StaticBackend("x"),
            },
        )

        clear_result = clear_orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertEqual(len(clear_judge.calls), 0)
        self.assertFalse(clear_result.vote_summary.judge_called)
        self.assertEqual(clear_result.content, "42")

    def test_judge_tiebreak_propagates_seed_and_counts_usage(self):
        judge = StaticBackend(
            '{"choice": 1}',
            usage=TokenUsage(prompt_tokens=6, completion_tokens=7, total_tokens=13),
        )
        planner = SequenceBackend(
            ["42", "43"],
            usages=[
                TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                TokenUsage(prompt_tokens=4, completion_tokens=5, total_tokens=9),
            ],
        )
        orchestrator = FuguLocalOrchestrator(
            make_judge_tiebreak_config(n=2),
            backend_overrides={
                "planner-model": planner,
                "judge-model": judge,
                "synth-model": StaticBackend("x"),
            },
        )

        result = orchestrator.chat(
            [ChatMessage(role="user", content="2案を比較して")],
            seed=42,
        )

        self.assertEqual(judge.calls[0].seed, derive_seed(42, "ensemble-judge:judge"))
        self.assertEqual(result.content, "43")
        self.assertEqual(result.usage.prompt_tokens, 11)
        self.assertEqual(result.usage.completion_tokens, 14)
        self.assertEqual(result.usage.total_tokens, 25)

    def test_judge_tiebreak_invalid_choice_counts_usage_and_warns(self):
        judge = StaticBackend(
            '{"choice": 99}',
            usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )
        planner = SequenceBackend(["43", "42"])
        orchestrator = FuguLocalOrchestrator(
            make_judge_tiebreak_config(n=2),
            backend_overrides={
                "planner-model": planner,
                "judge-model": judge,
                "synth-model": StaticBackend("x"),
            },
        )

        with self.assertLogs("fugu_local.orchestrator", level="WARNING") as captured:
            result = orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertEqual(result.content, "43")
        self.assertEqual(result.usage.total_tokens, 5)
        self.assertTrue(any("invalid choice" in message for message in captured.output))

    def test_judge_tiebreak_falls_back_when_judge_fails(self):
        failing_judge = FailingBackend()
        tied_planner = SequenceBackend(["43", "42"])
        orchestrator = FuguLocalOrchestrator(
            make_judge_tiebreak_config(n=2),
            backend_overrides={
                "planner-model": tied_planner,
                "judge-model": failing_judge,
                "synth-model": StaticBackend("x"),
            },
        )

        with self.assertLogs("fugu_local.orchestrator", level="WARNING") as captured:
            result = orchestrator.chat([ChatMessage(role="user", content="2案を比較して")])

        self.assertTrue(result.vote_summary.judge_called)
        # Falls back to the normalized-majority tie-break: the earliest
        # tied cluster (member #1's answer) wins.
        self.assertEqual(result.content, "43")
        self.assertTrue(any("failed" in message for message in captured.output))


def make_sequential_dag_config(stages=None, request_timeout_seconds=None, tool_calling=None):
    orchestrator = {"selection_policy": "all"}
    if request_timeout_seconds is not None:
        orchestrator["request_timeout_seconds"] = request_timeout_seconds
    raw = {
        "models": [
            {"name": "planner-model", "backend": "echo", "model": "mock-planner"},
            {"name": "solver-model", "backend": "echo", "model": "mock-solver"},
            {"name": "judge-model", "backend": "echo", "model": "mock-judge"},
            {"name": "critic-model", "backend": "echo", "model": "mock-critic"},
            {"name": "synth-model", "backend": "echo", "model": "mock-synth"},
        ],
        "roles": [
            {"name": "planner", "model": "planner-model"},
            {"name": "solver", "model": "solver-model"},
            {"name": "judge", "model": "judge-model", "is_verifier": True},
            {"name": "critic", "model": "critic-model"},
            {"name": "synthesizer", "model": "synth-model", "is_synthesizer": True},
        ],
        "orchestrator": orchestrator,
        "coordinator": {
            "enabled": True,
            "default_pattern": "sequential_dag",
            # Force sequential_dag regardless of message length/keywords, so
            # tests aren't sensitive to the coordinator's built-in heuristics
            # (e.g. "short text -> direct").
            "rules": [{"match": ["USE_DAG"], "pattern": "sequential_dag"}],
            "dag": {
                "stages": stages
                if stages is not None
                else [
                    {"name": "planner", "role": "planner"},
                    {"name": "solver", "role": "solver", "fanout": 2},
                    {"name": "verifier", "role": "judge"},
                    {"name": "critic", "role": "critic"},
                    {"name": "reviser", "role": "solver"},
                    {"name": "claim_judge", "role": "judge"},
                    {"name": "writer", "role": "synthesizer"},
                ]
            },
        },
    }
    if tool_calling is not None:
        raw["tool_calling"] = tool_calling
    return config_from_dict(raw)


def make_sequential_dag_backends():
    return {
        "planner-model": StaticBackend('{"answer": "plan", "subproblems": ["sp1", "sp2"]}'),
        "solver-model": StaticBackend('{"answer": "solved"}'),
        "judge-model": StaticBackend('{"claims": []}'),
        "critic-model": StaticBackend('{"claims": []}'),
        "synth-model": StaticBackend('{"answer": "final answer"}'),
    }


class SequentialDagTests(unittest.TestCase):
    def test_end_to_end_run_produces_content_and_stage_results(self):
        config = make_sequential_dag_config()
        orchestrator = FuguLocalOrchestrator(
            config, backend_overrides=make_sequential_dag_backends()
        )

        result = orchestrator.chat([ChatMessage(role="user", content="USE_DAG do the task")])

        self.assertEqual(result.pattern, "sequential_dag")
        self.assertEqual(result.content, "final answer")
        # planner + 2x solver (fanout) + verifier + critic + reviser +
        # claim_judge + writer = 8 stage calls.
        self.assertEqual(len(result.stage_results), 8)
        self.assertEqual(result.warnings, [])
        self.assertIsNone(result.synthesizer_role)

    def test_seed_is_propagated_to_dag_stages(self):
        config = make_sequential_dag_config()
        backends = make_sequential_dag_backends()
        orchestrator = FuguLocalOrchestrator(config, backend_overrides=backends)

        orchestrator.chat([ChatMessage(role="user", content="USE_DAG task")], seed=42)

        self.assertIsNotNone(backends["planner-model"].calls[-1].seed)

    def test_prepare_streaming_response_returns_none_for_sequential_dag(self):
        config = make_sequential_dag_config()
        orchestrator = FuguLocalOrchestrator(
            config, backend_overrides=make_sequential_dag_backends()
        )

        prepared = orchestrator.prepare_streaming_response(
            [ChatMessage(role="user", content="USE_DAG task")]
        )

        self.assertIsNone(prepared)

    def test_deadline_exceeded_returns_partial_result_with_warning(self):
        config = make_sequential_dag_config(request_timeout_seconds=0.05)
        backends = make_sequential_dag_backends()
        backends["planner-model"] = SleepBackend('{"answer": "plan", "subproblems": ["x"]}', 0.2)
        orchestrator = FuguLocalOrchestrator(config, backend_overrides=backends)

        result = orchestrator.chat([ChatMessage(role="user", content="USE_DAG task")])

        self.assertTrue(any("deadline" in warning for warning in result.warnings))

    def test_empty_dag_stages_raises(self):
        config = make_sequential_dag_config(stages=[])
        orchestrator = FuguLocalOrchestrator(
            config, backend_overrides=make_sequential_dag_backends()
        )

        with self.assertRaises(OrchestrationError):
            orchestrator.chat([ChatMessage(role="user", content="USE_DAG task")])

    def test_all_stage_calls_failing_raises_orchestration_error(self):
        config = make_sequential_dag_config()
        backends = {name: FailingBackend() for name in make_sequential_dag_backends()}
        orchestrator = FuguLocalOrchestrator(config, backend_overrides=backends)

        with self.assertRaises(OrchestrationError):
            orchestrator.chat([ChatMessage(role="user", content="USE_DAG task")])
