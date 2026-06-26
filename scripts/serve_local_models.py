#!/usr/bin/env python3
"""Print (or optionally launch) the local model servers a config needs.

On a single-GPU host (GX10, Apple Silicon MacBook Pro) you can run several model
servers in parallel terminals, all sharing the one GPU, or a single server with
parallel batching. This helper derives that set from one Thug-Fugu config so the
ports and model names are not maintained twice.

By default it only prints a plan; it does not start anything.
"""

from __future__ import annotations

import argparse
import json
from typing import Optional

from fugu_local.config import load_config
from fugu_local.serverplan import derive_server_plan, render_ollama_commands


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to a Thug-Fugu JSON config")
    parser.add_argument(
        "--num-parallel",
        type=int,
        default=None,
        help="Set OLLAMA_NUM_PARALLEL in printed serve commands (single-GPU concurrency lever)",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the derived plan as JSON instead of shell commands",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    endpoints = derive_server_plan(config)

    if not endpoints:
        print("No ollama endpoints found in config (nothing to start).")
        return 0

    if args.as_json:
        payload = [
            {
                "base_url": endpoint.base_url,
                "host": endpoint.host,
                "port": endpoint.port,
                "backend": endpoint.backend,
                "models": endpoint.models,
            }
            for endpoint in endpoints
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"# {len(endpoints)} local endpoint(s) derived from {args.config}")
    print("# Run each 'ollama serve' in its own terminal, then run the pull commands once.\n")
    for index, endpoint in enumerate(endpoints, start=1):
        print(f"# endpoint {index}: {endpoint.base_url} -> models: {', '.join(endpoint.models)}")
        for command in render_ollama_commands(endpoint, num_parallel=args.num_parallel):
            print(command)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
