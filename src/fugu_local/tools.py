"""Local allow-listed tool execution for Thug-Fugu.

The registry is deliberately tiny and side-effect free in this first execution
slice. It is process-local Python code, not arbitrary shell execution.
"""

from __future__ import annotations

import concurrent.futures
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List


class ToolExecutionError(RuntimeError):
    """Raised when a local tool cannot be executed safely."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    truncated: bool = False
    error: str = ""


def default_tool_registry() -> Dict[str, Callable[[Dict[str, Any]], str]]:
    return {
        "echo": _tool_echo,
        "lookup_static": _tool_lookup_static,
    }


def execute_tool_calls(
    calls: List[ToolCall],
    *,
    allowed_tools: List[str],
    timeout_seconds: float,
    max_output_chars: int,
    registry: Dict[str, Callable[[Dict[str, Any]], str]] | None = None,
) -> List[ToolResult]:
    registry = registry or default_tool_registry()
    results: List[ToolResult] = []
    for call in calls:
        results.append(
            _execute_one(
                call,
                allowed_tools=allowed_tools,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
                registry=registry,
            )
        )
    return results


def parse_tool_calls(raw_calls: Any) -> List[ToolCall]:
    if not isinstance(raw_calls, list):
        raise ToolExecutionError("tool_calls must be a list")
    calls: List[ToolCall] = []
    for index, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            raise ToolExecutionError(f"tool call at index {index} must be an object")
        call_id = raw.get("id") or f"call_local_{index}"
        function = raw.get("function")
        if raw.get("type") != "function" or not isinstance(function, dict):
            raise ToolExecutionError(f"tool call at index {index} must be a function call")
        name = function.get("name")
        arguments_raw = function.get("arguments", "{}")
        if not isinstance(name, str) or not name:
            raise ToolExecutionError(f"tool call at index {index} has invalid function name")
        if isinstance(arguments_raw, str):
            try:
                arguments = json.loads(arguments_raw or "{}")
            except json.JSONDecodeError as exc:
                raise ToolExecutionError(f"tool call {name} has invalid JSON arguments") from exc
        elif isinstance(arguments_raw, dict):
            arguments = arguments_raw
        else:
            raise ToolExecutionError(f"tool call {name} arguments must be JSON string or object")
        if not isinstance(arguments, dict):
            raise ToolExecutionError(f"tool call {name} arguments must decode to an object")
        calls.append(ToolCall(id=str(call_id), name=name, arguments=arguments))
    return calls


def _execute_one(
    call: ToolCall,
    *,
    allowed_tools: List[str],
    timeout_seconds: float,
    max_output_chars: int,
    registry: Dict[str, Callable[[Dict[str, Any]], str]],
) -> ToolResult:
    if call.name not in allowed_tools:
        return ToolResult(call.id, call.name, "", error=f"tool '{call.name}' is not allowed")
    func = registry.get(call.name)
    if func is None:
        return ToolResult(call.id, call.name, "", error=f"tool '{call.name}' is not registered")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, call.arguments)
        try:
            content = future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            future.cancel()
            return ToolResult(call.id, call.name, "", error="tool execution timed out")
        except Exception as exc:  # noqa: BLE001 - tool failures are captured as results.
            return ToolResult(call.id, call.name, "", error=str(exc))

    if not isinstance(content, str):
        content = str(content)
    truncated = len(content) > max_output_chars
    if truncated:
        content = content[:max_output_chars]
    return ToolResult(call.id, call.name, content, truncated=truncated)


def _tool_echo(args: Dict[str, Any]) -> str:
    text = args.get("text", "")
    if not isinstance(text, str):
        raise ValueError("echo.text must be a string")
    return text


def _tool_lookup_static(args: Dict[str, Any]) -> str:
    data = args.get("data", {})
    key = args.get("key")
    if not isinstance(data, dict):
        raise ValueError("lookup_static.data must be an object")
    if not isinstance(key, str):
        raise ValueError("lookup_static.key must be a string")
    value = data.get(key, "")
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
