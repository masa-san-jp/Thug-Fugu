"""Consult core: run one orchestration request and return a structured result.

This is the reusable building block for using Thug-Fugu as a consultant/sub-routine
from an outer agent (for example Claude Code via the MCP server). It keeps the
return value JSON-serializable so it can cross a tool boundary cleanly.

When the caller provides ``tool_calls`` and tool execution is enabled, allow-listed
local tools are executed, their outputs are injected as context, and the
orchestrator synthesizes a final answer over them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .backends import ChatMessage
from .config import FuguLocalConfig
from .orchestrator import FuguLocalOrchestrator
from .tools import execute_tool_calls, parse_tool_calls


def consult(
    config: FuguLocalConfig,
    prompt: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tool_calls: Optional[List[dict]] = None,
    orchestrator: Optional[FuguLocalOrchestrator] = None,
) -> Dict[str, Any]:
    """Run a single consult request and return a JSON-serializable result dict."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    engine = orchestrator or FuguLocalOrchestrator(config)
    messages = [ChatMessage(role="user", content=prompt)]

    tool_results_payload: List[Dict[str, Any]] = []
    if tool_calls:
        tool_config = config.tool_calling
        if not tool_config.enabled or not tool_config.execute:
            raise ValueError(
                "tool execution requires tool_calling.enabled=true and tool_calling.execute=true"
            )
        parsed = parse_tool_calls(tool_calls)
        results = execute_tool_calls(
            parsed,
            allowed_tools=tool_config.allowed_tools,
            timeout_seconds=tool_config.timeout_seconds,
            max_output_chars=tool_config.max_output_chars,
        )
        tool_results_payload = [
            {
                "tool_call_id": r.tool_call_id,
                "name": r.name,
                "content": r.content,
                "truncated": r.truncated,
                "error": r.error,
            }
            for r in results
        ]
        messages.append(ChatMessage(role="user", content=_format_tool_results(results)))

    result = engine.chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return {
        "answer": result.content,
        "pattern": result.pattern,
        "plan_reason": result.plan_reason,
        "plan_source": result.plan_source,
        "selected_roles": list(result.selected_roles),
        "synthesizer_role": result.synthesizer_role,
        "synthesis_error": result.synthesis_error,
        "run_id": result.run_id,
        "latency_ms": result.latency_ms,
        "tool_results": tool_results_payload,
        "workers": [
            {
                "role": worker.role,
                "model": worker.model,
                "ok": worker.ok,
                "latency_ms": worker.latency_ms,
                "timed_out": worker.timed_out,
                "error": worker.error,
            }
            for worker in result.worker_results
        ],
    }


def _format_tool_results(results) -> str:
    lines = ["Tool results (execute these were run locally; treat as evidence):"]
    for r in results:
        header = f"## {r.name} ({r.tool_call_id})"
        if r.error:
            lines.append(f"{header}\nERROR: {r.error}")
        else:
            suffix = " [truncated]" if r.truncated else ""
            lines.append(f"{header}{suffix}\n{r.content}")
    return "\n\n".join(lines)
