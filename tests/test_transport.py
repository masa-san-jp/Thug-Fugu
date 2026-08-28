import json
import threading
import unittest
from unittest import mock

from fugu_local.backends import (
    BackendError,
    ChatMessage,
    ChatRequest,
    OpenAICompatibleBackend,
)
from fugu_local.config import ModelConfig
from fugu_local.transport import PersistentHTTPTransport, TransportTiming


class FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)


class FakeHTTPResponse:
    will_close = False

    def __init__(self, body=b"{}", headers=None, status=200):
        self.body = body
        self.status = status
        self.headers = headers or [("Content-Length", str(len(body))), ("Connection", "keep-alive")]
        self.closed = False

    def getheaders(self):
        return self.headers

    def read(self, amount=-1):
        if amount < 0:
            body, self.body = self.body, b""
            return body
        body, self.body = self.body[:amount], self.body[amount:]
        return body

    def __iter__(self):
        body, self.body = self.body, b""
        for line in body.splitlines(keepends=True):
            yield line

    def close(self):
        self.closed = True


class FakeHTTPConnection:
    def __init__(self, responses, fail_request=False):
        self.responses = list(responses)
        self.fail_request = fail_request
        self.sock = None
        self.timeout = None
        self.connect_calls = 0
        self.close_calls = 0
        self.requests = []

    def connect(self):
        self.connect_calls += 1
        self.sock = FakeSocket()

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))
        if self.fail_request:
            raise OSError("stale connection")

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        self.close_calls += 1
        self.sock = None


class TransportTests(unittest.TestCase):
    def test_reuses_connection_per_thread_and_emits_timing(self):
        connection = FakeHTTPConnection(
            [FakeHTTPResponse(b"one"), FakeHTTPResponse(b"two")]
        )
        timings = []
        transport = PersistentHTTPTransport()
        with mock.patch(
            "fugu_local.transport._make_connection", return_value=connection
        ) as factory:
            with transport.request(
                "GET",
                "http://localhost:1234/api/tags?secret=query",
                body=None,
                headers={"accept": "application/json"},
                timeout=1.0,
                timing_hook=timings.append,
            ) as response:
                self.assertEqual(response.read(), b"one")
            with transport.request(
                "GET",
                "http://localhost:1234/api/tags",
                body=None,
                headers={},
                timeout=1.0,
                timing_hook=timings.append,
            ) as response:
                self.assertEqual(response.read(), b"two")
            transport.close()

        self.assertEqual(factory.call_count, 1)
        self.assertEqual(connection.connect_calls, 1)
        self.assertEqual(len(connection.requests), 2)
        self.assertEqual([timing.reused_connection for timing in timings], [False, True])
        self.assertTrue(all(timing.total_ms >= 0 for timing in timings))
        self.assertTrue(all(timing.read_ms is not None for timing in timings))
        self.assertEqual(connection.close_calls, 1)

    def test_different_threads_get_different_connection_objects(self):
        connections = [FakeHTTPConnection([FakeHTTPResponse(b"one")]) for _ in range(2)]
        transport = PersistentHTTPTransport()
        results = []

        def request():
            with transport.request(
                "GET",
                "http://localhost:1234/health",
                body=None,
                headers={},
                timeout=1.0,
            ) as response:
                results.append(response.read())

        with mock.patch("fugu_local.transport._make_connection", side_effect=connections):
            threads = [threading.Thread(target=request) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=1.0)
        transport.close()

        self.assertEqual(sorted(results), [b"one", b"one"])
        self.assertEqual([connection.connect_calls for connection in connections], [1, 1])
        self.assertEqual([connection.close_calls for connection in connections], [1, 1])

    def test_connection_close_header_forces_reconnect(self):
        first = FakeHTTPConnection(
            [
                FakeHTTPResponse(
                    b"one",
                    headers=[("Content-Length", "3"), ("Connection", "close")],
                )
            ]
        )
        second = FakeHTTPConnection([FakeHTTPResponse(b"two")])
        transport = PersistentHTTPTransport()
        with mock.patch("fugu_local.transport._make_connection", side_effect=[first, second]):
            with transport.request(
                "GET", "http://localhost:1234/health", body=None, headers={}, timeout=1.0
            ) as response:
                self.assertEqual(response.read(), b"one")
            with transport.request(
                "GET", "http://localhost:1234/health", body=None, headers={}, timeout=1.0
            ) as response:
                self.assertEqual(response.read(), b"two")
        transport.close()

        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.connect_calls, 1)

    def test_broken_connection_is_replaced_on_next_request(self):
        first = FakeHTTPConnection([], fail_request=True)
        second = FakeHTTPConnection([FakeHTTPResponse(b"reconnected")])
        transport = PersistentHTTPTransport()
        with mock.patch("fugu_local.transport._make_connection", side_effect=[first, second]):
            with self.assertRaises(OSError):
                transport.request(
                    "GET", "http://localhost:1234/health", body=None, headers={}, timeout=1.0
                )
            with transport.request(
                "GET", "http://localhost:1234/health", body=None, headers={}, timeout=1.0
            ) as response:
                self.assertEqual(response.read(), b"reconnected")
        transport.close()

        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.connect_calls, 1)


class InjectedTransportTests(unittest.TestCase):
    def test_backend_uses_injected_transport_for_json_and_closes_response(self):
        response = ClosableResponse(
            json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        )
        transport = RecordingTransport(response)
        timings = []
        backend = OpenAICompatibleBackend(
            ModelConfig(
                name="local",
                backend="openai-compatible",
                model="mock",
                base_url="http://localhost:1234",
                api_key="secret",
            ),
            transport=transport,
            timing_hook=timings.append,
        )

        result = backend.chat(ChatRequest(model="mock", messages=[ChatMessage("user", "hi")]))

        self.assertEqual(result.content, "ok")
        self.assertTrue(response.closed)
        self.assertEqual(
            transport.calls[0][0:2], ("POST", "http://localhost:1234/v1/chat/completions")
        )
        self.assertEqual(transport.calls[0][3]["authorization"], "Bearer secret")
        self.assertEqual(len(timings), 1)

    def test_malformed_stream_closes_injected_response(self):
        response = ClosableResponse(b"data: {not-json}\n")
        transport = RecordingTransport(response)
        backend = OpenAICompatibleBackend(
            ModelConfig(
                name="local",
                backend="openai-compatible",
                model="mock",
                base_url="http://localhost:1234",
            ),
            transport=transport,
        )

        with self.assertRaises(BackendError):
            list(
                backend.stream_chat(
                    ChatRequest(model="mock", messages=[ChatMessage("user", "hi")])
                )
            )

        self.assertTrue(response.closed)

    def test_http_status_error_closes_response_and_redacts_body(self):
        secret = b"secret backend response"
        response = ClosableResponse(secret, status=500)
        transport = RecordingTransport(response)
        backend = OpenAICompatibleBackend(
            ModelConfig(
                name="local",
                backend="openai-compatible",
                model="mock",
                base_url="http://localhost:1234",
            ),
            transport=transport,
        )

        with self.assertRaises(BackendError) as context:
            backend.chat(ChatRequest(model="mock", messages=[ChatMessage("user", "hi")]))

        self.assertIn("HTTP 500", str(context.exception))
        self.assertIn("redacted", str(context.exception))
        self.assertNotIn(secret.decode("utf-8"), str(context.exception))
        self.assertTrue(response.closed)


class RecordingTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, *, body, headers, timeout, timing_hook=None):
        self.calls.append((method, url, body, headers, timeout, timing_hook))
        if timing_hook is not None:
            timing_hook(
                TransportTiming(
                    method=method,
                    endpoint=url,
                    connect_ms=0.0,
                    ttfb_ms=0.0,
                    read_ms=0.0,
                    total_ms=0.0,
                    reused_connection=True,
                )
            )
        return self.response

    def close(self):
        return


class ClosableResponse:
    headers = {"connection": "keep-alive"}

    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.closed = False

    def read(self, amount=-1):
        if amount < 0:
            body, self.body = self.body, b""
            return body
        body, self.body = self.body[:amount], self.body[amount:]
        return body

    def iter_lines(self):
        body, self.body = self.body, b""
        yield from body.splitlines(keepends=True)

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


if __name__ == "__main__":
    unittest.main()
