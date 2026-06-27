import http.client
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from fugu_local.backends import ChatResponse
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

    def test_health(self):
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["roles"], ["planner"])
        self.assertEqual(body["max_concurrent_requests"], 4)

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

    def test_rejects_non_boolean_stream(self):
        status, body = self._post_chat(
            {"messages": [{"role": "user", "content": "hello"}], "stream": "true"}
        )

        self.assertEqual(status, 400)
        self.assertIn("stream", body["error"]["message"])

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
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
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


if __name__ == "__main__":
    unittest.main()
