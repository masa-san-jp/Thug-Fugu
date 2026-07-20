"""Model routing: turn a model or model pool into a failover-capable backend.

A ``ModelRouter`` implements the ``LLMBackend`` chat interface but may dispatch to
several underlying endpoints (pool members). It supports two routing policies:

- ``round_robin``: rotate the starting member on each call.
- ``least_busy``: prefer the member with the fewest in-flight calls.

On a member error it fails over to the next member in the attempt order. If every
member fails, the last error is raised.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import List, Optional

from .backends import ChatRequest, ChatResponse, LLMBackend


class RoutingError(RuntimeError):
    """Raised when a router has no members to dispatch to."""


def _safe_endpoint_label(endpoint: str) -> str:
    """Drop URL credentials, query parameters, and fragments from health output."""

    parsed = urllib.parse.urlsplit(endpoint)
    if not parsed.scheme or not parsed.hostname:
        return parsed.path or endpoint

    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"

    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


@dataclass
class RouterMember:
    key: str
    backend: LLMBackend
    busy: int = 0
    failures: int = 0
    cooldown_until: float = 0.0


class ModelRouter:
    """Dispatch a chat request to one or more backends with policy and failover."""

    def __init__(
        self,
        model_string: str,
        members: List[RouterMember],
        *,
        policy: str = "round_robin",
        cooldown_seconds: float = 0.0,
        unhealthy_threshold: int = 1,
    ):
        if not members:
            raise RoutingError("ModelRouter requires at least one member")
        self.model_string = model_string
        self._members = members
        self._policy = policy
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._unhealthy_threshold = max(1, unhealthy_threshold)
        self._lock = threading.Lock()
        self._round_robin_index = 0

    @property
    def members(self) -> List[RouterMember]:
        return list(self._members)

    def health_snapshot(self) -> List[dict]:
        """Return non-sensitive passive health state for observability."""

        now = time.monotonic()
        with self._lock:
            snapshot = []
            for member in self._members:
                cooldown_remaining = max(0.0, member.cooldown_until - now)
                state = "degraded" if cooldown_remaining > 0 else "healthy"
                snapshot.append(
                    {
                        "endpoint": _safe_endpoint_label(member.key),
                        "state": state,
                        "busy": member.busy,
                        "failures": member.failures,
                        "cooldown_remaining_seconds": round(cooldown_remaining, 3),
                    }
                )
            return snapshot

    def chat(self, request: ChatRequest) -> ChatResponse:
        order = self._attempt_order()
        last_exc: Optional[Exception] = None
        for member in order:
            self._acquire(member)
            try:
                response = member.backend.chat(request)
                self._record_success(member)
                return response
            except Exception as exc:  # noqa: BLE001 - try next member on any failure.
                last_exc = exc
                self._record_failure(member)
            finally:
                self._release(member)
        assert last_exc is not None  # order is non-empty, so a failure must have occurred.
        raise last_exc

    def _attempt_order(self) -> List[RouterMember]:
        with self._lock:
            now = time.monotonic()
            if self._policy == "least_busy":
                # Stable sort keeps config order among equally-busy members. Members in
                # cooldown are deprioritized but never removed, so a fully-degraded pool
                # still attempts every endpoint.
                base = sorted(self._members, key=lambda member: member.busy)
            else:
                start = self._round_robin_index
                self._round_robin_index = (self._round_robin_index + 1) % len(self._members)
                base = self._members[start:] + self._members[:start]
            return sorted(base, key=lambda member: member.cooldown_until > now)

    def _record_success(self, member: RouterMember) -> None:
        with self._lock:
            member.failures = 0
            member.cooldown_until = 0.0

    def _record_failure(self, member: RouterMember) -> None:
        if self._cooldown_seconds <= 0:
            return
        with self._lock:
            member.failures += 1
            if member.failures >= self._unhealthy_threshold:
                member.cooldown_until = time.monotonic() + self._cooldown_seconds

    def _acquire(self, member: RouterMember) -> None:
        with self._lock:
            member.busy += 1

    def _release(self, member: RouterMember) -> None:
        with self._lock:
            member.busy -= 1
