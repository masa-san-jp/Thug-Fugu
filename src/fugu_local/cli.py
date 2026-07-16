"""Command-line interface for fugu-local."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .backends import ChatMessage
from .config import ConfigError, load_config
from .consult import consult
from .orchestrator import FuguLocalOrchestrator, OrchestrationError
from .server import DEFAULT_MAX_CONCURRENT_REQUESTS, serve, validate_bind_host


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fugu-local",
        description="Run local LLM orchestration inspired by Thug AI Fugu.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a single orchestration request")
    run_parser.add_argument("prompt", help="User prompt")
    run_parser.add_argument("--config", required=True, help="Path to JSON config")
    run_parser.add_argument("--temperature", type=float, default=None)
    run_parser.add_argument("--max-tokens", type=int, default=None)
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON object with answer plus usage/verification/worker metadata",
    )

    serve_parser = subparsers.add_parser("serve", help="Serve an OpenAI-compatible API")
    serve_parser.add_argument("--config", required=True, help="Path to JSON config")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument(
        "--max-concurrent-requests",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_REQUESTS,
        help="Maximum concurrent chat completion requests before returning HTTP 429",
    )
    serve_parser.add_argument(
        "--allow-unsafe-bind",
        action="store_true",
        help=(
            "Allow binding to non-loopback addresses. Use only for deliberate "
            "private-network or reverse-proxy deployments with TLS/auth controls."
        ),
    )

    validate_parser = subparsers.add_parser("validate-config", help="Validate config and exit")
    validate_parser.add_argument("--config", required=True, help="Path to JSON config")

    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate-config":
        print(
            f"OK: {len(config.models)} model(s), {len(config.roles)} role(s), "
            f"selection_policy={config.orchestrator.selection_policy}"
        )
        return 0

    if args.command == "serve":
        if args.max_concurrent_requests <= 0:
            print("--max-concurrent-requests must be positive", file=sys.stderr)
            return 2
        try:
            validate_bind_host(args.host, allow_unsafe_bind=args.allow_unsafe_bind)
            serve(
                config,
                host=args.host,
                port=args.port,
                max_concurrent_requests=args.max_concurrent_requests,
                allow_unsafe_bind=args.allow_unsafe_bind,
            )
        except ValueError as exc:
            print(f"Unsafe bind refused: {exc}", file=sys.stderr)
            return 2
        return 0

    if args.command == "run":
        orchestrator = FuguLocalOrchestrator(config)
        if args.json:
            try:
                payload = consult(
                    config,
                    args.prompt,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    orchestrator=orchestrator,
                )
            except OrchestrationError as exc:
                print(f"Orchestration error: {exc}", file=sys.stderr)
                return 1
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        try:
            result = orchestrator.chat(
                [ChatMessage(role="user", content=args.prompt)],
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
        except OrchestrationError as exc:
            print(f"Orchestration error: {exc}", file=sys.stderr)
            return 1
        print(result.content)
        return 0

    parser.print_help()
    return 2
