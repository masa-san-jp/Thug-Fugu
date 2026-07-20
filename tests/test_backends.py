import io
import json
import unittest
import urllib.error
from unittest import mock

from fugu_local.backends import (
    BackendError,
    ChatMessage,
    ChatRequest,
    OllamaBackend,
    OpenAICompatibleBackend,
    probe_ollama,
)
from fugu_local.config import ModelConfig


class BackendRedactionTests(unittest.TestCase):
    def test_http_error_body_is_redacted(self):
        secret = "SECRET_SENTINEL_PROMPT_CONTENT"
        error = urllib.error.HTTPError(
            url="http://localhost:1234/v1/chat/completions?token=SECRET_QUERY",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=io.BytesIO(secret.encode("utf-8")),
        )
        backend = OpenAICompatibleBackend(
            ModelConfig(
                name="local",
                backend="openai-compatible",
                model="mock",
                base_url="http://localhost:1234",
            )
        )

        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(BackendError) as ctx:
                backend.chat(ChatRequest(model="mock", messages=[ChatMessage("user", "hello")]))

        message = str(ctx.exception)
        self.assertIn("HTTP 500", message)
        self.assertIn("http://localhost:1234/v1/chat/completions", message)
        self.assertIn("redacted", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("SECRET_QUERY", message)

    def test_non_json_response_body_is_redacted(self):
        secret = "SECRET_SENTINEL_COMPLETION_CONTENT"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return secret.encode("utf-8")

        backend = OpenAICompatibleBackend(
            ModelConfig(
                name="local",
                backend="openai-compatible",
                model="mock",
                base_url="http://localhost:1234",
            )
        )

        with mock.patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(BackendError) as ctx:
                backend.chat(ChatRequest(model="mock", messages=[ChatMessage("user", "hello")]))

        message = str(ctx.exception)
        self.assertIn("Non-JSON response", message)
        self.assertIn("redacted", message)
        self.assertNotIn(secret, message)


class OllamaProbeTests(unittest.TestCase):
    def test_probe_uses_tags_endpoint_and_timeout(self):
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response

        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            healthy = probe_ollama("http://localhost:11434/", timeout_seconds=2.5)

        self.assertTrue(healthy)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/tags")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2.5)

    def test_probe_returns_false_on_connection_error(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("down"),
        ):
            healthy = probe_ollama("http://localhost:11434", timeout_seconds=1)

        self.assertFalse(healthy)


class UsageParsingTests(unittest.TestCase):
    def test_openai_compatible_usage_is_preserved(self):
        backend = OpenAICompatibleBackend(
            ModelConfig(
                name="local",
                backend="openai-compatible",
                model="mock",
                base_url="http://localhost:1234",
            )
        )
        payload = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }

        with mock.patch("urllib.request.urlopen", return_value=JsonResponse(payload)):
            response = backend.chat(ChatRequest(model="mock", messages=[ChatMessage("user", "hi")]))

        self.assertEqual(response.content, "ok")
        self.assertIsNotNone(response.usage)
        self.assertEqual(response.usage.prompt_tokens, 3)
        self.assertEqual(response.usage.completion_tokens, 5)
        self.assertEqual(response.usage.total_tokens, 8)

    def test_ollama_usage_is_mapped_from_eval_counts(self):
        backend = OllamaBackend(
            ModelConfig(
                name="local",
                backend="ollama",
                model="mock",
                base_url="http://localhost:11434",
            )
        )
        payload = {
            "message": {"content": "ok"},
            "prompt_eval_count": 7,
            "eval_count": 11,
        }

        with mock.patch("urllib.request.urlopen", return_value=JsonResponse(payload)):
            response = backend.chat(ChatRequest(model="mock", messages=[ChatMessage("user", "hi")]))

        self.assertEqual(response.content, "ok")
        self.assertIsNotNone(response.usage)
        self.assertEqual(response.usage.prompt_tokens, 7)
        self.assertEqual(response.usage.completion_tokens, 11)
        self.assertEqual(response.usage.total_tokens, 18)


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
