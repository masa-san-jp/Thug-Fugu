"""Consult core: run one orchestration request and return a structured result.

This is the reusable building block for using Thug-Fugu as a consultant/sub-routine
from an outer agent (for example Claude Code via the MCP server). It keeps the
return value JSON-serializable so it can cross a tool boundary cleanly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .backends import ChatMessage
from .config import FuguLocalConfig
from .orchestrator import FuguLocalOrchestrator


def consult(
    config: FuguLocalConfig,
    prompt: str,
    *,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    orchestrator: Optional[FuguLocalOrchestrator] = None,
) -> Dict[str, Any]:
    """Run a single consult request and return a JSON-serializable result dict."""

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")

    engine = orchestrator or FuguLocalOrchestrator(config)
    result = engine.chat(
        [ChatMessage(role="user", content=prompt)],
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
