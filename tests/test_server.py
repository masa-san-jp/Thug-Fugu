import json
import threading
import unittest
import urllib.request

from fugu_local.config import config_from_dict
from fugu_local.orchestrator import FuguLocalOrchestrator
from fugu_local.server import FuguLocalHTTPServer, FuguLocalHandler


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

    def test_chat_completions(self):
        data = json.dumps(
            {
                "model": "fugu-local",
                "messages": [{"role": "user", "content": "hello"}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))

        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["message"]["role"], "assistant")
        self.assertIn("echo:m/mock", body["choices"][0]["message"]["content"])


if __name__ == "__main__":
    unittest.main()

