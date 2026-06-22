import json
import threading
import unittest
import urllib.error
import urllib.request

from fugu_local.backends import ChatResponse
from fugu_local.config import config_from_dict
from fugu_local.orchestrator import FuguLocalOrchestrator
from fugu_local.server import FuguLocalHTTPServer, FuguLocalHandler, MAX_REQUEST_BODY_BYTES


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

    def test_rejects_streaming_requests(self):
        status, body = self._post_chat(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }
        )

        self.assertEqual(status, 400)
        self.assertIn("streaming", body["error"]["message"])

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

    def _open_json(self, request):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
