import io
import unittest
import urllib.error
from unittest import mock

from fugu_local.backends import BackendError, ChatMessage, ChatRequest, OpenAICompatibleBackend
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


if __name__ == "__main__":
    unittest.main()
