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
from typing import Iterator, List, Optional

from .backends import ChatRequest, ChatResponse, ChatStreamChunk, LLMBackend


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
    health_state: str = "healthy"
    last_probe_at: Optional[float] = None
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    consecutive_successes: int = 0


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
        active_health_enabled: bool = False,
        health_failure_threshold: int = 2,
        health_success_threshold: int = 1,
    ):
        if not members:
            raise RoutingError("ModelRouter requires at least one member")
        self.model_string = model_string
        self._members = members
        self._policy = policy
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._unhealthy_threshold = max(1, unhealthy_threshold)
        self._active_health_enabled = active_health_enabled
        self._health_failure_threshold = max(1, health_failure_threshold)
        self._health_success_threshold = max(1, health_success_threshold)
        self._lock = threading.Lock()
        self._round_robin_index = 0
        if active_health_enabled:
            for member in self._members:
                member.health_state = "unknown"

    @property
    def members(self) -> List[RouterMember]:
        return list(self._members)

    @property
    def supports_streaming(self) -> bool:
        return all(
            callable(getattr(member.backend, "stream_chat", None)) for member in self._members
        )

    def health_snapshot(self) -> List[dict]:
        """Return non-sensitive passive and active health state for observability."""

        monotonic_now = time.monotonic()
        with self._lock:
            snapshot = []
            for member in self._members:
                cooldown_remaining = max(0.0, member.cooldown_until - monotonic_now)
                snapshot.append(
                    {
                        "endpoint": _safe_endpoint_label(member.key),
                        "state": self._member_state_locked(member, monotonic_now),
                        "busy": member.busy,
                        "failures": member.failures,
                        "cooldown_remaining_seconds": round(cooldown_remaining, 3),
                        "last_probe_at": member.last_probe_at,
                        "last_success_at": member.last_success_at,
                        "last_failure_at": member.last_failure_at,
                    }
                )
            return snapshot

    def record_probe_result(
        self,
        member_key: str,
        healthy: bool,
        *,
        timestamp: Optional[float] = None,
    ) -> None:
        """Update one member from an active health probe."""

        observed_at = time.time() if timestamp is None else timestamp
        with self._lock:
            member = next(
                (candidate for candidate in self._members if candidate.key == member_key),
                None,
            )
            if member is None:
                raise KeyError(f"unknown router member: {member_key}")

            member.last_probe_at = observed_at
            if healthy:
                member.last_success_at = observed_at
                member.consecutive_successes += 1
                member.failures = 0
                if member.consecutive_successes >= self._health_success_threshold:
                    member.health_state = "healthy"
                    member.cooldown_until = 0.0
                else:
                    member.health_state = "degraded"
            else:
                member.last_failure_at = observed_at
                member.consecutive_successes = 0
                member.failures += 1
                if member.failures >= self._health_failure_threshold:
                    member.health_state = "unhealthy"
                else:
                    member.health_state = "degraded"

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

    def stream_chat(self, request: ChatRequest) -> Iterator[ChatStreamChunk]:
        """Stream from one member, failing over only before the first emitted chunk."""

        if not self.supports_streaming:
            raise RoutingError("not every router member supports streaming")

        order = self._attempt_order()
        last_exc: Optional[Exception] = None
        for member in order:
            self._acquire(member)
            emitted = False
            try:
                stream_chat = getattr(member.backend, "stream_chat")
                for chunk in stream_chat(request):
                    emitted = True
                    yield chunk
                self._record_success(member)
                return
            except Exception as exc:  # noqa: BLE001 - fail over before streaming starts.
                last_exc = exc
                self._record_failure(member)
                if emitted:
                    raise
            finally:
                self._release(member)

        assert last_exc is not None
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
            priority = {"healthy": 0, "unknown": 1, "degraded": 2, "unhealthy": 3}
            return sorted(
                base,
                key=lambda member: priority[self._member_state_locked(member, now)],
            )

    def _member_state_locked(self, member: RouterMember, monotonic_now: float) -> str:
        if member.cooldown_until > monotonic_now and member.health_state == "healthy":
            return "degraded"
        return member.health_state

    def _record_success(self, member: RouterMember) -> None:
        with self._lock:
            member.failures = 0
            member.cooldown_until = 0.0
            member.last_success_at = time.time()
            member.consecutive_successes = self._health_success_threshold
            member.health_state = "healthy"

    def _record_failure(self, member: RouterMember) -> None:
        if self._cooldown_seconds <= 0 and not self._active_health_enabled:
            return
        with self._lock:
            member.failures += 1
            member.last_failure_at = time.time()
            member.consecutive_successes = 0
            if self._active_health_enabled:
                if member.failures >= self._health_failure_threshold:
                    member.health_state = "unhealthy"
                else:
                    member.health_state = "degraded"
            if member.failures >= self._unhealthy_threshold:
                if self._cooldown_seconds > 0:
                    member.cooldown_until = time.monotonic() + self._cooldown_seconds

    def _acquire(self, member: RouterMember) -> None:
        with self._lock:
            member.busy += 1

    def _release(self, member: RouterMember) -> None:
        with self._lock:
            member.busy -= 1
