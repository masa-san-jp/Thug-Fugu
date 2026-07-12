"""MCP server exposing Thug-Fugu as a `consult` tool for agent runtimes.

This lets an outer agent (for example Claude Code) delegate higher-quality
multi-role reasoning to Thug-Fugu while keeping its own tool execution and control
loop (README "pattern 2").

The MCP dependency is optional. Install it with:

    pip install 'thug-fugu-local[mcp]'

Run the server (stdio) with a config:

    fugu-local-mcp --config path/to/config.json

Or set FUGU_LOCAL_CONFIG in the environment.
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from .config import load_config
from .consult import consult
from .orchestrator import FuguLocalOrchestrator

_MISSING_MCP_MESSAGE = (
    "The 'mcp' package is required for the Thug-Fugu MCP server. "
    "Install it with: pip install 'thug-fugu-local[mcp]'"
)


def _resolve_config_path(explicit: Optional[str]) -> str:
    path = explicit or os.environ.get("FUGU_LOCAL_CONFIG")
    if not path:
        raise SystemExit("No config provided. Pass --config PATH or set FUGU_LOCAL_CONFIG.")
    return path


def build_server(config_path: str):  # pragma: no cover - requires optional mcp dependency
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(_MISSING_MCP_MESSAGE) from exc

    config = load_config(config_path)
    orchestrator = FuguLocalOrchestrator(config)
    server = FastMCP("thug-fugu")

    @server.tool()
    def consult_thug_fugu(
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tool_calls: Optional[list] = None,
    ) -> dict:
        """Delegate a reasoning task to Thug-Fugu's multi-role local LLM orchestration.

        Optionally pass OpenAI-style ``tool_calls`` to execute allow-listed local
        tools before synthesis (requires tool_calling.enabled and execute=true).
        Returns the synthesized answer plus metadata (pattern, roles, timings).
        """
        return consult(
            config,
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            tool_calls=tool_calls,
            orchestrator=orchestrator,
        )

    return server


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - stdio server entrypoint
    parser = argparse.ArgumentParser(prog="fugu-local-mcp", description=__doc__)
    parser.add_argument("--config", help="Path to a Thug-Fugu JSON config")
    args = parser.parse_args(argv)

    config_path = _resolve_config_path(args.config)
    server = build_server(config_path)
    server.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
