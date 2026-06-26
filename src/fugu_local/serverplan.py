"""Derive the set of local model servers required by a single config.

A single Thug-Fugu config can reference several endpoints (different ports) on one
machine. On a single-GPU host (for example GX10 or an Apple Silicon MacBook Pro)
these endpoints all share the one GPU, but you may still want to run several model
servers in parallel terminals, or one server with parallel batching enabled.

This module turns the config into a deterministic "server plan" so the same single
definition can drive both orchestration and local server startup, avoiding
double-maintenance of ports and model names.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlsplit

from .config import FuguLocalConfig

DEFAULT_PLAN_BACKENDS = ("ollama",)


@dataclass(frozen=True)
class ServerEndpoint:
    base_url: str
    host: str
    port: Optional[int]
    backend: str
    models: List[str]

    @property
    def host_port(self) -> str:
        return f"{self.host}:{self.port}" if self.port is not None else self.host


def derive_server_plan(
    config: FuguLocalConfig,
    *,
    backends: tuple = DEFAULT_PLAN_BACKENDS,
) -> List[ServerEndpoint]:
    """Group config models by endpoint for the requested backends.

    Order is stable and follows first appearance in ``config.models``. Models with
    no ``base_url`` (for example the ``echo`` backend) are skipped.
    """

    grouped: dict = {}
    order: List[str] = []
    for model in config.models:
        if model.backend not in backends:
            continue
        if not model.base_url:
            continue
        key = model.base_url.rstrip("/")
        if key not in grouped:
            grouped[key] = {"backend": model.backend, "models": []}
            order.append(key)
        if model.model not in grouped[key]["models"]:
            grouped[key]["models"].append(model.model)

    endpoints: List[ServerEndpoint] = []
    for base_url in order:
        parts = urlsplit(base_url)
        endpoints.append(
            ServerEndpoint(
                base_url=base_url,
                host=parts.hostname or "127.0.0.1",
                port=parts.port,
                backend=grouped[base_url]["backend"],
                models=list(grouped[base_url]["models"]),
            )
        )
    return endpoints


def render_ollama_commands(
    endpoint: ServerEndpoint,
    *,
    num_parallel: Optional[int] = None,
) -> List[str]:
    """Render shell commands to start one Ollama endpoint and pull its models.

    ``num_parallel`` maps to ``OLLAMA_NUM_PARALLEL`` and is the single-GPU lever for
    concurrent requests against one server, which is the relevant knob on GX10/MBP
    where there is no second physical GPU to pin processes to.
    """

    if endpoint.backend != "ollama":
        raise ValueError(
            f"render_ollama_commands only supports ollama endpoints, got {endpoint.backend}"
        )

    if num_parallel is not None and num_parallel <= 0:
        raise ValueError("num_parallel must be positive when provided")

    host = shlex.quote(endpoint.host_port)
    env = f"OLLAMA_HOST={host}"
    if num_parallel is not None:
        env += f" OLLAMA_NUM_PARALLEL={num_parallel}"

    commands = [f"{env} ollama serve"]
    for model in endpoint.models:
        commands.append(f"OLLAMA_HOST={host} ollama pull {shlex.quote(model)}")
    return commands
