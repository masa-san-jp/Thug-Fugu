import http.client
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from fugu_local.backends import ChatResponse, ChatStreamChunk, TokenUsage
from fugu_local.config import config_from_dict
from fugu_local.orchestrator import FuguLocalOrchestrator
from fugu_local.server import (
    MAX_REQUEST_BODY_BYTES,
    FuguLocalHandler,
    FuguLocalHTTPServer,
    is_safe_bind_host,
    validate_bind_host,
)


class BlockingBackend:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def chat(self, request):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("timed out waiting for release")
        return ChatResponse(content="released")


class UsageBackend:
    def chat(self, request):
        return ChatResponse(
            content="usage response",
            usage=TokenUsage(prompt_tokens=13, completion_tokens=17, total_tokens=30),
        )


class ControlledStreamingBackend:
    def __init__(self, *, fail_before=False, fail_after=False):
        self.fail_before = fail_before
        self.fail_after = fail_after
        self.first_yielded = threading.Event()
        self.release = threading.Event()
        self.completed = threading.Event()
        self.stream_calls = 0
        self.stream_requests = []
        self.chat_calls = 0

    def chat(self, request):
        self.chat_calls += 1
        return ChatResponse(content="buffered fallback")

    def stream_chat(self, request):
        self.stream_calls += 1
        self.stream_requests.append(request)
        if self.fail_before:
            raise RuntimeError("SECRET_PRE_HEADER_STREAM_ERROR")
        self.first_yielded.set()
        yield ChatStreamChunk(delta="first")
        if self.fail_after:
            raise RuntimeError("SECRET_POST_HEADER_STREAM_ERROR")
        if not self.release.wait(timeout=5):
            raise RuntimeError("stream release timeout")
        yield ChatStreamChunk(delta=" second")
        yield ChatStreamChunk(
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5),
        )
        self.completed.set()


class LifecycleMonitor:
    def __init__(self):
        self.started = threading.Event()
        self.stopped = threading.Event()

    def start(self):
        self.started.set()

    def stop(self):
        self.stopped.set()

    @property
    def running(self):
        return self.started.is_set() and not self.stopped.is_set()


class ServerTests(unittest.TestCase):
    def setUp(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        self.server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def _start_direct_stream_server(self, backend):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [
                    {
                        "name": "planner",
                        "model": "m",
                        "system_prompt": "answer directly",
                    }
                ],
                "coordinator": {"enabled": True, "default_pattern": "direct"},
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": backend}),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    def _start_role_split_stream_server(self, synthesizer_backend):
        config = config_from_dict(
            {
                "models": [
                    {"name": "worker-model", "backend": "echo", "model": "worker"},
                    {"name": "synth-model", "backend": "echo", "model": "synth"},
                ],
                "roles": [
                    {"name": "worker", "model": "worker-model", "always_include": True},
                    {
                        "name": "synthesizer",
                        "model": "synth-model",
                        "is_synthesizer": True,
                    },
                ],
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(
                config,
                backend_overrides={
                    "worker-model": UsageBackend(),
                    "synth-model": synthesizer_backend,
                },
            ),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    def test_health(self):
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["roles"], ["planner"])
        self.assertEqual(body["max_concurrent_requests"], 4)
        self.assertEqual(body["model_pools"], {})
        self.assertEqual(
            body["queue"],
            {
                "enabled": False,
                "size": 0,
                "max_size": 16,
                "timeout_seconds": 30.0,
            },
        )

    def test_health_reports_active_pool_state_without_credentials(self):
        endpoint = "http://user:secret@127.0.0.1:11434?token=secret"
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "model_pools": [
                    {
                        "name": "fast",
                        "backend": "ollama",
                        "model": "gpt-oss:20b",
                        "endpoints": [endpoint],
                        "health": {"enabled": True, "failure_threshold": 1},
                    }
                ],
                "roles": [{"name": "worker", "model": "fast"}],
            }
        )
        orchestrator = FuguLocalOrchestrator(
            config,
            backend_overrides={endpoint: UsageBackend()},
        )
        orchestrator._routers["fast"].record_probe_result(endpoint, False, timestamp=10.0)
        orchestrator._health_monitor = LifecycleMonitor()
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            orchestrator,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/health",
                timeout=5,
            ) as response:
                raw_body = response.read().decode("utf-8")
                body = json.loads(raw_body)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        member = body["model_pools"]["fast"][0]
        self.assertEqual(member["endpoint"], "http://127.0.0.1:11434/")
        self.assertEqual(member["state"], "unhealthy")
        self.assertEqual(member["last_probe_at"], 10.0)
        self.assertNotIn("secret", raw_body)

    def test_server_starts_and_stops_health_monitor(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        orchestrator = FuguLocalOrchestrator(config)
        monitor = LifecycleMonitor()
        orchestrator._health_monitor = monitor
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            orchestrator,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            self.assertTrue(monitor.started.wait(timeout=1))

            server.shutdown()
            thread.join(timeout=2)

            self.assertTrue(monitor.stopped.is_set())
        finally:
            server.server_close()

    def test_models_endpoint(self):
        with urllib.request.urlopen(f"{self.base_url}/v1/models", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body["object"], "list")
        ids = [model["id"] for model in body["data"]]
        self.assertIn("fugu-local", ids)
        self.assertIn("m", ids)
        self.assertEqual(body["data"][0]["object"], "model")

    def test_chat_completions(self):
        status, body = self._post_chat(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
            }
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertIn("echo:m/mock", body["choices"][0]["message"]["content"])
        self.assertEqual(
            body["usage"], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )

    def test_chat_completions_reports_backend_usage_when_known(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": UsageBackend()}),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post_chat_to(
                f"http://127.0.0.1:{server.server_port}",
                {
                    "model": "fugu-local",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(status, 200)
        self.assertEqual(
            body["usage"],
            {"prompt_tokens": 13, "completion_tokens": 17, "total_tokens": 30},
        )

    def test_streaming_chat_completions_returns_sse(self):
        status, headers, raw = self._post_chat_raw(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }
        )

        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        events = _parse_sse_events(raw.decode("utf-8"))
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(event) for event in events[:-1]]
        self.assertEqual(chunks[0]["object"], "chat.completion.chunk")
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertIn("echo:m/mock", chunks[1]["choices"][0]["delta"]["content"])
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("usage", chunks[-1])

    def test_streaming_chat_completions_can_include_usage_chunk(self):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": UsageBackend()}),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, headers, raw = self._post_chat_raw_to(
                f"http://127.0.0.1:{server.server_port}",
                {
                    "model": "fugu-local",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        events = _parse_sse_events(raw.decode("utf-8"))
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(event) for event in events[:-1]]
        usage_chunk = chunks[-1]
        self.assertEqual(usage_chunk["choices"], [])
        self.assertEqual(
            usage_chunk["usage"],
            {"prompt_tokens": 13, "completion_tokens": 17, "total_tokens": 30},
        )

    def test_direct_stream_emits_content_before_backend_completes(self):
        backend = ControlledStreamingBackend()
        server, thread, _ = self._start_direct_stream_server(backend)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        payload = json.dumps(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "stream_options": {"include_usage": True, "include_progress": True},
            }
        )
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=payload,
                headers={"content-type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("text/event-stream", response.getheader("content-type"))

            initial = ""
            while '"content": "first"' not in initial:
                line = response.readline().decode("utf-8")
                self.assertTrue(line)
                initial += line

            self.assertTrue(backend.first_yielded.is_set())
            self.assertFalse(backend.completed.is_set())
            self.assertEqual(backend.chat_calls, 0)

            backend.release.set()
            raw = initial + response.read().decode("utf-8")
        finally:
            backend.release.set()
            connection.close()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        events = _parse_sse_events(raw)
        self.assertEqual(events[-1], "[DONE]")
        chunks = [json.loads(event) for event in events[:-1]]
        content = "".join(
            choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"]
        )
        self.assertEqual(content, "first second")
        self.assertEqual(chunks[-1]["choices"], [])
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 5)
        self.assertTrue(backend.completed.is_set())
        self.assertNotIn("fugu_progress", raw)

    def test_direct_stream_failure_before_headers_returns_json(self):
        backend = ControlledStreamingBackend(fail_before=True)
        server, thread, base_url = self._start_direct_stream_server(backend)
        try:
            status, body = self._post_chat_to(
                base_url,
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(status, 502)
        self.assertIn("streaming backend failed", body["error"]["message"])
        self.assertNotIn("SECRET", body["error"]["message"])

    def test_direct_stream_failure_after_headers_emits_safe_terminal_error(self):
        backend = ControlledStreamingBackend(fail_after=True)
        server, thread, base_url = self._start_direct_stream_server(backend)
        try:
            status, headers, raw = self._post_chat_raw_to(
                base_url,
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        text = raw.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        self.assertIn('"content": "first"', text)
        self.assertIn("streaming backend error", text)
        self.assertIn("[DONE]", text)
        self.assertNotIn("SECRET_POST_HEADER_STREAM_ERROR", text)

    def test_role_split_streams_synthesizer_after_workers_complete(self):
        synthesizer = ControlledStreamingBackend()
        server, thread, _ = self._start_role_split_stream_server(synthesizer)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        payload = json.dumps(
            {
                "messages": [{"role": "user", "content": "complex task"}],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
        )
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=payload,
                headers={"content-type": "application/json"},
            )
            response = connection.getresponse()
            initial = ""
            while '"content": "first"' not in initial:
                line = response.readline().decode("utf-8")
                self.assertTrue(line)
                initial += line

            self.assertEqual(response.status, 200)
            self.assertFalse(synthesizer.completed.is_set())
            self.assertIn("usage response", synthesizer.stream_requests[0].messages[1].content)

            synthesizer.release.set()
            raw = initial + response.read().decode("utf-8")
        finally:
            synthesizer.release.set()
            connection.close()
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        events = _parse_sse_events(raw)
        chunks = [json.loads(event) for event in events[:-1]]
        content = "".join(
            choice["delta"].get("content", "") for chunk in chunks for choice in chunk["choices"]
        )
        self.assertEqual(content, "first second")
        self.assertEqual(chunks[-1]["usage"]["prompt_tokens"], 15)
        self.assertEqual(chunks[-1]["usage"]["completion_tokens"], 20)
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 35)
        self.assertNotIn("fugu_progress", raw)

    def test_role_split_progress_event_is_opt_in(self):
        synthesizer = ControlledStreamingBackend()
        synthesizer.release.set()
        server, thread, base_url = self._start_role_split_stream_server(synthesizer)
        try:
            status, headers, raw = self._post_chat_raw_to(
                base_url,
                {
                    "messages": [{"role": "user", "content": "complex task"}],
                    "stream": True,
                    "stream_options": {"include_progress": True},
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        text = raw.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        self.assertIn("event: fugu_progress", text)
        self.assertIn(
            'data: {"phase": "workers_done", "ok": 1, "failed": 0}',
            text,
        )
        self.assertLess(text.index("event: fugu_progress"), text.index('"content": "first"'))

    def test_role_split_synth_stream_failure_before_headers_uses_worker_fallback(self):
        synthesizer = ControlledStreamingBackend(fail_before=True)
        server, thread, base_url = self._start_role_split_stream_server(synthesizer)
        try:
            status, headers, raw = self._post_chat_raw_to(
                base_url,
                {
                    "messages": [{"role": "user", "content": "complex task"}],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        text = raw.decode("utf-8")
        events = _parse_sse_events(text)
        chunks = [json.loads(event) for event in events[:-1]]
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        self.assertIn("usage response", text)
        self.assertNotIn("SECRET_PRE_HEADER_STREAM_ERROR", text)
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 30)
        self.assertEqual(synthesizer.chat_calls, 0)

    def test_role_split_streaming_uses_buffered_fallback(self):
        backend = ControlledStreamingBackend()
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": backend}),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, headers, raw = self._post_chat_raw_to(
                f"http://127.0.0.1:{server.server_port}",
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers["content-type"])
        self.assertIn("buffered fallback", raw.decode("utf-8"))
        self.assertEqual(backend.chat_calls, 1)
        self.assertEqual(backend.stream_calls, 0)

    def test_rejects_non_boolean_stream(self):
        status, body = self._post_chat(
            {"messages": [{"role": "user", "content": "hello"}], "stream": "true"}
        )

        self.assertEqual(status, 400)
        self.assertIn("stream", body["error"]["message"])

    def test_rejects_invalid_stream_options_include_usage(self):
        status, body = self._post_chat(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "stream_options": {"include_usage": "true"},
            }
        )

        self.assertEqual(status, 400)
        self.assertIn("include_usage", body["error"]["message"])

    def test_rejects_invalid_stream_options_include_progress(self):
        status, body = self._post_chat(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "stream_options": {"include_progress": "true"},
            }
        )

        self.assertEqual(status, 400)
        self.assertIn("include_progress", body["error"]["message"])

    def test_rejects_tool_calling_requests(self):
        status, body = self._post_chat(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
                "tools": [],
            }
        )

        self.assertEqual(status, 400)
        self.assertIn("tool calling", body["error"]["message"])

    def test_invalid_messages_missing_returns_400(self):
        status, body = self._post_chat({"model": "fugu-local"})

        self.assertEqual(status, 400)
        self.assertIn("'messages' is required", body["error"]["message"])

    def test_invalid_messages_not_list_returns_400(self):
        status, body = self._post_chat({"messages": "hello"})

        self.assertEqual(status, 400)
        self.assertIn("'messages' must be a list", body["error"]["message"])

    def test_invalid_messages_empty_returns_400(self):
        status, body = self._post_chat({"messages": []})

        self.assertEqual(status, 400)
        self.assertIn("at least one message", body["error"]["message"])

    def test_invalid_message_item_returns_400(self):
        status, body = self._post_chat({"messages": ["hello"]})

        self.assertEqual(status, 400)
        self.assertIn("message at index 0 must be an object", body["error"]["message"])

    def test_invalid_message_role_returns_400(self):
        status, body = self._post_chat({"messages": [{"role": 123, "content": "hello"}]})

        self.assertEqual(status, 400)
        self.assertIn("string 'role'", body["error"]["message"])

    def test_invalid_message_content_returns_400(self):
        status, body = self._post_chat({"messages": [{"role": "user", "content": ["hello"]}]})

        self.assertEqual(status, 400)
        self.assertIn("string 'content'", body["error"]["message"])

    def test_backend_failure_after_valid_request_returns_502_with_redacted_body(self):
        secret = "SECRET_SENTINEL_BACKEND_BODY"
        config = config_from_dict(
            {
                "models": [
                    {
                        "name": "m",
                        "backend": "openai-compatible",
                        "model": "mock",
                        "base_url": "http://localhost:1234",
                    }
                ],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        self.server.orchestrator = FuguLocalOrchestrator(config)
        backend_error = urllib.error.HTTPError(
            url="http://localhost:1234/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(secret.encode("utf-8")),
        )
        payload = json.dumps({"messages": [{"role": "user", "content": "valid request"}]}).encode(
            "utf-8"
        )

        with mock.patch("urllib.request.urlopen", side_effect=backend_error):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            try:
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    body=payload,
                    headers={"content-type": "application/json"},
                )
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
            finally:
                conn.close()

        self.assertEqual(response.status, 502)
        message = body["error"]["message"]
        self.assertIn("HTTP 500", message)
        self.assertIn("redacted", message)
        self.assertNotIn(secret, message)

    def test_streaming_backend_failure_returns_json_error_before_sse_headers(self):
        secret = "SECRET_SENTINEL_STREAMING_BACKEND_BODY"
        config = config_from_dict(
            {
                "models": [
                    {
                        "name": "m",
                        "backend": "openai-compatible",
                        "model": "mock",
                        "base_url": "http://localhost:1234",
                    }
                ],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        self.server.orchestrator = FuguLocalOrchestrator(config)
        backend_error = urllib.error.HTTPError(
            url="http://localhost:1234/v1/chat/completions",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(secret.encode("utf-8")),
        )
        payload = json.dumps(
            {
                "stream": True,
                "messages": [{"role": "user", "content": "valid streaming request"}],
            }
        ).encode("utf-8")

        with mock.patch("urllib.request.urlopen", side_effect=backend_error):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            try:
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    body=payload,
                    headers={"content-type": "application/json"},
                )
                response = conn.getresponse()
                body = json.loads(response.read().decode("utf-8"))
            finally:
                conn.close()

        self.assertEqual(response.status, 502)
        self.assertIn("application/json", response.getheader("content-type"))
        self.assertNotIn(secret, body["error"]["message"])

    def test_rejects_invalid_temperature(self):
        status, body = self._post_chat(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": "0.2",
            }
        )

        self.assertEqual(status, 400)
        self.assertIn("temperature", body["error"]["message"])

    def test_rejects_invalid_max_tokens(self):
        status, body = self._post_chat(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 0,
            }
        )

        self.assertEqual(status, 400)
        self.assertIn("max_tokens", body["error"]["message"])

    def test_rejects_oversized_request_body(self):
        data = b"{}" + (b" " * MAX_REQUEST_BODY_BYTES)
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )

        status, body = self._open_json(request)

        self.assertEqual(status, 413)
        self.assertIn("too large", body["error"]["message"])

    def test_rejects_when_concurrency_limit_is_exhausted(self):
        backend = BlockingBackend()
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": backend}),
            max_concurrent_requests=1,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        first_result = {}

        def run_first_request():
            first_result["response"] = self._post_chat_to(base_url)

        first_thread = threading.Thread(target=run_first_request, daemon=True)
        first_thread.start()
        self.assertTrue(backend.started.wait(timeout=5))

        try:
            status, body = self._post_chat_to(base_url)
            self.assertEqual(status, 429)
            self.assertIn("too many", body["error"]["message"])
        finally:
            backend.release.set()
            first_thread.join(timeout=5)
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(first_result["response"][0], 200)

    def test_queue_waits_for_slot_is_bounded_and_reports_size(self):
        backend = BlockingBackend()
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
                "server": {
                    "queue": {
                        "enabled": True,
                        "max_size": 1,
                        "timeout_seconds": 2,
                    }
                },
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": backend}),
            max_concurrent_requests=1,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        first_result = {}
        second_result = {}

        first_thread = threading.Thread(
            target=lambda: first_result.setdefault("response", self._post_chat_to(base_url)),
            daemon=True,
        )
        first_thread.start()
        self.assertTrue(backend.started.wait(timeout=5))

        second_thread = threading.Thread(
            target=lambda: second_result.setdefault("response", self._post_chat_to(base_url)),
            daemon=True,
        )
        second_thread.start()

        try:
            deadline = time.monotonic() + 2
            queue_size = 0
            while time.monotonic() < deadline:
                with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
                    health = json.loads(response.read().decode("utf-8"))
                queue_size = health["queue"]["size"]
                if queue_size == 1:
                    break
                time.sleep(0.01)
            self.assertEqual(queue_size, 1)

            status, body = self._post_chat_to(base_url)
            self.assertEqual(status, 429)
            self.assertIn("too many", body["error"]["message"])

            backend.release.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)
        finally:
            backend.release.set()
            server.shutdown()
            server_thread.join(timeout=2)
            server.server_close()

        self.assertEqual(first_result["response"][0], 200)
        self.assertEqual(second_result["response"][0], 200)

    def test_queue_timeout_returns_429_and_removes_waiter(self):
        backend = BlockingBackend()
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
                "server": {
                    "queue": {
                        "enabled": True,
                        "max_size": 1,
                        "timeout_seconds": 0.05,
                    }
                },
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0),
            FuguLocalHandler,
            FuguLocalOrchestrator(config, backend_overrides={"m": backend}),
            max_concurrent_requests=1,
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        first_thread = threading.Thread(
            target=lambda: self._post_chat_to(base_url),
            daemon=True,
        )
        first_thread.start()
        self.assertTrue(backend.started.wait(timeout=5))

        try:
            status, body = self._post_chat_to(base_url)
            self.assertEqual(status, 429)
            self.assertIn("too many", body["error"]["message"])

            with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
                health = json.loads(response.read().decode("utf-8"))
            self.assertEqual(health["queue"]["size"], 0)
        finally:
            backend.release.set()
            first_thread.join(timeout=5)
            server.shutdown()
            server_thread.join(timeout=2)
            server.server_close()

    def _post_chat(self, payload):
        return self._post_chat_to(self.base_url, payload)

    def _post_chat_to(self, base_url, payload=None):
        if payload is None:
            payload = {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
            }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        return self._open_json(request)

    def _post_chat_raw(self, payload):
        return self._post_chat_raw_to(self.base_url, payload)

    def _post_chat_raw_to(self, base_url, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()

    def _open_json(self, request):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


class BindSafetyTests(unittest.TestCase):
    def test_loopback_hosts_are_safe(self):
        self.assertTrue(is_safe_bind_host("127.0.0.1"))
        self.assertTrue(is_safe_bind_host("localhost"))
        self.assertTrue(is_safe_bind_host("::1"))

    def test_non_loopback_hosts_require_explicit_opt_in(self):
        self.assertFalse(is_safe_bind_host("0.0.0.0"))
        self.assertFalse(is_safe_bind_host("::"))
        self.assertFalse(is_safe_bind_host("192.168.1.10"))
        self.assertFalse(is_safe_bind_host("example.internal"))

        with self.assertRaises(ValueError) as ctx:
            validate_bind_host("0.0.0.0")

        message = str(ctx.exception)
        self.assertIn("TLS", message)
        self.assertIn("authentication", message)
        self.assertIn("private-network", message)

    def test_allow_unsafe_bind_opt_in(self):
        validate_bind_host("0.0.0.0", allow_unsafe_bind=True)


def _parse_sse_events(raw):
    events = []
    for block in raw.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if line.startswith("data: ")]
        if lines:
            events.append("\n".join(line[len("data: ") :] for line in lines))
    return events


class ToolCallingServerTests(unittest.TestCase):
    def _server(self, tool_calling):
        config = config_from_dict(
            {
                "models": [{"name": "m", "backend": "echo", "model": "mock"}],
                "roles": [{"name": "planner", "model": "m"}],
                "tool_calling": tool_calling,
            }
        )
        server = FuguLocalHTTPServer(
            ("127.0.0.1", 0), FuguLocalHandler, FuguLocalOrchestrator(config)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=2))
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def _post(self, base_url, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_tools_rejected_when_disabled(self):
        base = self._server({"enabled": False, "mode": "disabled"})
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "lookup"}}],
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("tool calling is not enabled", body["error"]["message"])

    def test_valid_tools_accepted_when_enabled(self):
        base = self._server({"enabled": True, "mode": "synthesizer_only"})
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup_note",
                            "description": "look up a note",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": "auto",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")

    def test_tool_calls_execute_when_enabled(self):
        base = self._server(
            {
                "enabled": True,
                "mode": "synthesizer_only",
                "execute": True,
                "allowed_tools": ["echo"],
            }
        )
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "use evidence"}],
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "echo",
                            "arguments": {"text": "local evidence"},
                        },
                    }
                ],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["thug_fugu"]["tool_results"][0]["name"], "echo")
        self.assertEqual(body["thug_fugu"]["tool_results"][0]["content"], "local evidence")
        self.assertIn("local evidence", body["choices"][0]["message"]["content"])

    def test_tool_calls_rejected_when_execution_disabled(self):
        base = self._server({"enabled": True, "mode": "synthesizer_only"})
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "echo", "arguments": {"text": "x"}},
                    }
                ],
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("execute=true", body["error"]["message"])

    def test_tool_calls_reject_malformed_arguments(self):
        base = self._server(
            {
                "enabled": True,
                "mode": "synthesizer_only",
                "execute": True,
                "allowed_tools": ["echo"],
            }
        )
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{not-json"},
                    }
                ],
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("invalid JSON arguments", body["error"]["message"])

    def test_invalid_tool_name_returns_400(self):
        base = self._server({"enabled": True, "mode": "synthesizer_only"})
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "bad name!"}}],
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid function name", body["error"]["message"])

    def test_tool_choice_required_not_supported(self):
        base = self._server({"enabled": True, "mode": "synthesizer_only"})
        status, body = self._post(
            base,
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "lookup"}}],
                "tool_choice": "required",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("not supported yet", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
