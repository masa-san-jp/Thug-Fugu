"""Small OpenAI-compatible HTTP server for local orchestration."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .backends import ChatMessage, TokenUsage
from .config import FuguLocalConfig
from .orchestrator import FuguLocalOrchestrator, OrchestrationError, messages_from_dicts
from .tools import ToolExecutionError, ToolResult, execute_tool_calls, parse_tool_calls

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

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self.orchestrator.start_health_monitor()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self.orchestrator.stop_health_monitor()

    def server_close(self) -> None:
        self.orchestrator.stop_health_monitor()
        super().server_close()

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
                    "model_pools": self.server.orchestrator.model_pool_health(),
                },
            )
            return
        if self.path == "/v1/models":
            self._write_json(200, _models_response(self.server.orchestrator.config))
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
            _validate_chat_completion_request(body, self.server.orchestrator.config.tool_calling)
            messages = messages_from_dicts(body.get("messages", []))
            tool_results = _execute_http_tool_calls(
                body.get("tool_calls"),
                self.server.orchestrator.config.tool_calling,
            )
            if tool_results:
                messages.append(
                    ChatMessage(role="user", content=_format_tool_results(tool_results))
                )
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
        except Exception:  # noqa: BLE001 - HTTP boundary; do not leak internals.
            self._write_json(500, {"error": {"message": "internal server error"}})
            return
        finally:
            self.server.release_request_slot()

        model = body.get("model") or "fugu-local"
        if body.get("stream") is True:
            self._write_chat_completion_stream(
                model=model,
                content=result.content,
                usage=result.usage,
                include_usage=_stream_include_usage(body),
            )
        else:
            self._write_json(
                200,
                _chat_completion_response(
                    model=model,
                    content=result.content,
                    usage=result.usage,
                    thug_fugu=_thug_fugu_metadata(tool_results),
                ),
            )

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the default server quiet when embedded in development tooling.
        return

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = self._content_length()
        if content_length > MAX_REQUEST_BODY_BYTES:
            # Drain a bounded amount of the request body before responding so clients
            # sending a just-over-limit body can receive the JSON 413 response instead
            # of seeing a connection reset while still preserving the memory cap.
            self.rfile.read(MAX_REQUEST_BODY_BYTES + 1)
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

    def _write_chat_completion_stream(
        self,
        *,
        model: str,
        content: str,
        usage: Optional[TokenUsage] = None,
        include_usage: bool = False,
    ) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        for event in _chat_completion_stream_events(
            model=model,
            content=content,
            usage=usage,
            include_usage=include_usage,
        ):
            self.wfile.write(event)
        self.wfile.flush()


_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _validate_chat_completion_request(body: Dict[str, Any], tool_calling=None) -> None:
    if "stream" in body and not isinstance(body.get("stream"), bool):
        raise ValueError("stream must be a boolean when provided")
    _validate_stream_options(body)
    if "tool_calls" in body:
        _validate_tool_calls_request(body["tool_calls"])
    if "tools" in body or "tool_choice" in body:
        _validate_tool_calling_request(body, tool_calling)
    _validate_messages_schema(body)


def _validate_tool_calls_request(raw_tool_calls: Any) -> None:
    if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
        raise ValueError("tool_calls must be a non-empty list when provided")
    for index, call in enumerate(raw_tool_calls):
        if not isinstance(call, dict):
            raise ValueError(f"tool_call at index {index} must be an object")
        if call.get("type") != "function":
            raise ValueError(f"tool_call at index {index} must have type 'function'")
        function = call.get("function")
        if not isinstance(function, dict):
            raise ValueError(f"tool_call at index {index} must have a function object")
        name = function.get("name")
        if not isinstance(name, str) or not _TOOL_NAME_PATTERN.match(name):
            raise ValueError(
                f"tool_call at index {index} has an invalid function name; "
                "must match ^[A-Za-z0-9_-]{1,64}$"
            )
        arguments = function.get("arguments", {})
        if not isinstance(arguments, (str, dict)):
            raise ValueError(
                f"tool_call at index {index} function.arguments must be a JSON string or object"
            )


def _execute_http_tool_calls(
    raw_tool_calls: Any,
    tool_calling,
) -> list[ToolResult]:
    if not raw_tool_calls:
        return []
    if tool_calling is None or not tool_calling.enabled or not tool_calling.execute:
        raise ValueError(
            "tool_calls execution requires tool_calling.enabled=true and tool_calling.execute=true"
        )
    try:
        parsed = parse_tool_calls(raw_tool_calls)
    except ToolExecutionError as exc:
        raise ValueError(str(exc)) from exc
    return execute_tool_calls(
        parsed,
        allowed_tools=tool_calling.allowed_tools,
        timeout_seconds=tool_calling.timeout_seconds,
        max_output_chars=tool_calling.max_output_chars,
    )


def _format_tool_results(results: list[ToolResult]) -> str:
    lines = ["Tool results (executed locally by Thug-Fugu HTTP server; treat as evidence):"]
    for result in results:
        header = f"## {result.name} ({result.tool_call_id})"
        if result.error:
            lines.append(f"{header}\nERROR: {result.error}")
        else:
            suffix = " [truncated]" if result.truncated else ""
            lines.append(f"{header}{suffix}\n{result.content}")
    return "\n\n".join(lines)


def _validate_stream_options(body: Dict[str, Any]) -> None:
    if "stream_options" not in body:
        return
    options = body["stream_options"]
    if not isinstance(options, dict):
        raise ValueError("stream_options must be an object when provided")
    if "include_usage" in options and not isinstance(options["include_usage"], bool):
        raise ValueError("stream_options.include_usage must be a boolean when provided")


def _stream_include_usage(body: Dict[str, Any]) -> bool:
    options = body.get("stream_options")
    return isinstance(options, dict) and options.get("include_usage") is True


def _validate_tool_calling_request(body: Dict[str, Any], tool_calling) -> None:
    if tool_calling is None or not getattr(tool_calling, "enabled", False):
        raise ValueError("tool calling is not enabled")

    if "tools" in body:
        tools = body["tools"]
        if not isinstance(tools, list) or not tools:
            raise ValueError("'tools' must be a non-empty list when provided")
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                raise ValueError(f"tool at index {index} must be an object")
            if tool.get("type") != "function":
                raise ValueError(f"tool at index {index} must have type 'function'")
            function = tool.get("function")
            if not isinstance(function, dict):
                raise ValueError(f"tool at index {index} must have a 'function' object")
            name = function.get("name")
            if not isinstance(name, str) or not _TOOL_NAME_PATTERN.match(name):
                raise ValueError(
                    f"tool at index {index} has an invalid function name; "
                    "must match ^[A-Za-z0-9_-]{1,64}$"
                )
            if "description" in function and not isinstance(function["description"], str):
                raise ValueError(f"tool at index {index} description must be a string")
            if "parameters" in function and not isinstance(function["parameters"], dict):
                raise ValueError(f"tool at index {index} parameters must be an object")

    tool_choice = body.get("tool_choice", "auto")
    if isinstance(tool_choice, str):
        if tool_choice not in ("none", "auto", "required"):
            raise ValueError("tool_choice string must be one of: none, auto, required")
        if tool_choice == "required":
            raise ValueError(
                "tool_choice='required' is not supported yet; this build accepts tool schemas "
                "in shape-only mode but does not force or execute tool calls"
            )
    elif isinstance(tool_choice, dict):
        if tool_choice.get("type") != "function" or not isinstance(
            tool_choice.get("function"), dict
        ):
            raise ValueError("named tool_choice must be {'type':'function','function':{...}}")
        raise ValueError(
            "named tool_choice is not supported yet; this build accepts tool schemas in "
            "shape-only mode but does not force or execute tool calls"
        )
    else:
        raise ValueError("tool_choice must be a string or an object when provided")


def _validate_messages_schema(body: Dict[str, Any]) -> None:
    """Validate the Chat Completions message array shape, raising ValueError (HTTP 400)."""

    if "messages" not in body:
        raise ValueError("'messages' is required")
    raw_messages = body["messages"]
    if not isinstance(raw_messages, list):
        raise ValueError("'messages' must be a list")
    if not raw_messages:
        raise ValueError("'messages' must contain at least one message")
    for index, message in enumerate(raw_messages):
        if not isinstance(message, dict):
            raise ValueError(f"message at index {index} must be an object")
        if not isinstance(message.get("role"), str):
            raise ValueError(f"message at index {index} must contain a string 'role'")
        if not isinstance(message.get("content"), str):
            raise ValueError(f"message at index {index} must contain a string 'content'")


def is_safe_bind_host(host: str) -> bool:
    """Return True when host is limited to the local loopback interface."""

    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # DNS names and LAN hostnames can resolve externally or privately; require opt-in.
        return False


def validate_bind_host(host: str, *, allow_unsafe_bind: bool = False) -> None:
    if allow_unsafe_bind or is_safe_bind_host(host):
        return
    raise ValueError(
        "refusing to bind the unauthenticated development server to a non-loopback "
        f"address ({host!r}). The built-in server has no TLS or authentication by "
        "default; use --allow-unsafe-bind only for deliberate private-network or "
        "reverse-proxy deployments with appropriate controls."
    )


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
    allow_unsafe_bind: bool = False,
) -> None:
    validate_bind_host(host, allow_unsafe_bind=allow_unsafe_bind)
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


def _chat_completion_stream_events(
    model: str,
    content: str,
    usage: Optional[TokenUsage] = None,
    include_usage: bool = False,
) -> list:
    created = int(time.time())
    completion_id = f"chatcmpl-local-{created}"
    chunks = [
        _chat_completion_chunk(
            completion_id=completion_id,
            created=created,
            model=model,
            delta={"role": "assistant"},
            finish_reason=None,
        )
    ]
    if content:
        chunks.append(
            _chat_completion_chunk(
                completion_id=completion_id,
                created=created,
                model=model,
                delta={"content": content},
                finish_reason=None,
            )
        )
    chunks.append(
        _chat_completion_chunk(
            completion_id=completion_id,
            created=created,
            model=model,
            delta={},
            finish_reason="stop",
        )
    )
    if include_usage:
        chunks.append(
            _chat_completion_chunk(
                completion_id=completion_id,
                created=created,
                model=model,
                delta={},
                finish_reason=None,
                usage=usage,
                choices=[],
            )
        )
    events = [
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8") for chunk in chunks
    ]
    events.append(b"data: [DONE]\n\n")
    return events


def _chat_completion_chunk(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: Dict[str, str],
    finish_reason: Optional[str],
    usage: Optional[TokenUsage] = None,
    choices: Optional[list] = None,
) -> Dict[str, Any]:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": choices
        if choices is not None
        else [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = _usage_to_openai_dict(usage)
    elif choices == []:
        payload["usage"] = _usage_to_openai_dict(None)
    return payload


def _chat_completion_response(
    model: str,
    content: str,
    usage: Optional[TokenUsage] = None,
    thug_fugu: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    created = int(time.time())
    payload = {
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
        "usage": _usage_to_openai_dict(usage),
    }
    if thug_fugu:
        payload["thug_fugu"] = thug_fugu
    return payload


def _thug_fugu_metadata(tool_results: list[ToolResult]) -> Optional[Dict[str, Any]]:
    if not tool_results:
        return None
    return {
        "tool_results": [
            {
                "tool_call_id": result.tool_call_id,
                "name": result.name,
                "content": result.content,
                "truncated": result.truncated,
                "error": result.error,
            }
            for result in tool_results
        ]
    }


def _usage_to_openai_dict(usage: Optional[TokenUsage]) -> Dict[str, int]:
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = usage.prompt_tokens or 0
    completion = usage.completion_tokens or 0
    total = usage.total_tokens if usage.total_tokens is not None else prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _models_response(config: FuguLocalConfig) -> Dict[str, Any]:
    ids = ["fugu-local"]
    ids.extend(model.name for model in config.models)
    ids.extend(pool.name for pool in config.model_pools)
    seen = set()
    data = []
    for model_id in ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        data.append(
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "thug-fugu",
            }
        )
    return {"object": "list", "data": data}
