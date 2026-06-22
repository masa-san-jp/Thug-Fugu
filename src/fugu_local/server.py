"""Small OpenAI-compatible HTTP server for local orchestration."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .config import FuguLocalConfig
from .orchestrator import FuguLocalOrchestrator, OrchestrationError, messages_from_dicts


MAX_REQUEST_BODY_BYTES = 1_048_576
DEFAULT_MAX_CONCURRENT_REQUESTS = 4


class RequestTooLargeError(ValueError):
    """Raised when an HTTP request body exceeds the configured limit."""


class FuguLocalHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        RequestHandlerClass,
        orchestrator,
        *,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
    ):
        if max_concurrent_requests <= 0:
            raise ValueError("max_concurrent_requests must be positive")
        super().__init__(server_address, RequestHandlerClass)
        self.orchestrator = orchestrator
        self.max_concurrent_requests = max_concurrent_requests
        self._request_semaphore = threading.BoundedSemaphore(max_concurrent_requests)

    def try_acquire_request_slot(self) -> bool:
        return self._request_semaphore.acquire(blocking=False)

    def release_request_slot(self) -> None:
        self._request_semaphore.release()


class FuguLocalHandler(BaseHTTPRequestHandler):
    server: FuguLocalHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        if self.path == "/health":
            self._write_json(
                200,
                {
                    "status": "ok",
                    "service": "fugu-local",
                    "roles": [role.name for role in self.server.orchestrator.roles],
                    "max_concurrent_requests": self.server.max_concurrent_requests,
                },
            )
            return
        self._write_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        if self.path != "/v1/chat/completions":
            self._write_json(404, {"error": {"message": "not found"}})
            return

        if not self.server.try_acquire_request_slot():
            self._write_json(429, {"error": {"message": "too many concurrent requests"}})
            return

        try:
            body = self._read_json_body()
            _validate_chat_completion_request(body)
            messages = messages_from_dicts(body.get("messages", []))
            result = self.server.orchestrator.chat(
                messages,
                temperature=_optional_temperature(body.get("temperature")),
                max_tokens=_optional_max_tokens(body.get("max_tokens")),
            )
        except RequestTooLargeError as exc:
            self._write_json(413, {"error": {"message": str(exc)}})
            return
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": {"message": f"invalid JSON: {exc}"}})
            return
        except ValueError as exc:
            self._write_json(400, {"error": {"message": str(exc)}})
            return
        except OrchestrationError as exc:
            self._write_json(502, {"error": {"message": str(exc)}})
            return
        except Exception as exc:  # noqa: BLE001 - HTTP boundary.
            self._write_json(500, {"error": {"message": str(exc)}})
            return
        finally:
            self.server.release_request_slot()

        model = body.get("model") or "fugu-local"
        self._write_json(200, _chat_completion_response(model=model, content=result.content))

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the default server quiet when embedded in development tooling.
        return

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = self._content_length()
        if content_length > MAX_REQUEST_BODY_BYTES:
            raise RequestTooLargeError(
                f"request body too large: limit is {MAX_REQUEST_BODY_BYTES} bytes"
            )
        raw = self.rfile.read(content_length).decode("utf-8")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def _content_length(self) -> int:
        raw_value = self.headers.get("content-length")
        if raw_value is None:
            raise ValueError("content-length header is required")
        try:
            content_length = int(raw_value)
        except ValueError as exc:
            raise ValueError("content-length header must be an integer") from exc
        if content_length < 0:
            raise ValueError("content-length header must be non-negative")
        return content_length

    def _write_json(self, status: int, body: Dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _validate_chat_completion_request(body: Dict[str, Any]) -> None:
    if body.get("stream") not in (None, False):
        raise ValueError("streaming responses are not supported")
    if "tools" in body or "tool_choice" in body:
        raise ValueError("tool calling is not supported")


def _optional_temperature(value: Any) -> Optional[float]:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("temperature must be a number when provided")
    temperature = float(value)
    if not 0 <= temperature <= 2:
        raise ValueError("temperature must be between 0 and 2")
    return temperature


def _optional_max_tokens(value: Any) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("max_tokens must be a positive integer when provided")
    if value <= 0:
        raise ValueError("max_tokens must be a positive integer when provided")
    return value


def serve(
    config: FuguLocalConfig,
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
) -> None:
    orchestrator = FuguLocalOrchestrator(config)
    httpd = FuguLocalHTTPServer(
        (host, port),
        FuguLocalHandler,
        orchestrator,
        max_concurrent_requests=max_concurrent_requests,
    )
    print(
        f"fugu-local serving on http://{host}:{port} "
        f"(max_concurrent_requests={max_concurrent_requests})"
    )
    httpd.serve_forever()


def _chat_completion_response(model: str, content: str) -> Dict[str, Any]:
    created = int(time.time())
    return {
        "id": f"chatcmpl-local-{created}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
