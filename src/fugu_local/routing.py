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
from dataclasses import dataclass
from typing import List, Optional

from .backends import ChatRequest, ChatResponse, LLMBackend


class RoutingError(RuntimeError):
    """Raised when a router has no members to dispatch to."""


@dataclass
class RouterMember:
    key: str
    backend: LLMBackend
    busy: int = 0


class ModelRouter:
    """Dispatch a chat request to one or more backends with policy and failover."""

    def __init__(
        self,
        model_string: str,
        members: List[RouterMember],
        *,
        policy: str = "round_robin",
    ):
        if not members:
            raise RoutingError("ModelRouter requires at least one member")
        self.model_string = model_string
        self._members = members
        self._policy = policy
        self._lock = threading.Lock()
        self._round_robin_index = 0

    @property
    def members(self) -> List[RouterMember]:
        return list(self._members)

    def chat(self, request: ChatRequest) -> ChatResponse:
        order = self._attempt_order()
        last_exc: Optional[Exception] = None
        for member in order:
            self._acquire(member)
            try:
                return member.backend.chat(request)
            except Exception as exc:  # noqa: BLE001 - try next member on any failure.
                last_exc = exc
            finally:
                self._release(member)
        assert last_exc is not None  # order is non-empty, so a failure must have occurred.
        raise last_exc

    def _attempt_order(self) -> List[RouterMember]:
        with self._lock:
            if self._policy == "least_busy":
                # Stable sort keeps config order among equally-busy members.
                return sorted(self._members, key=lambda member: member.busy)
            start = self._round_robin_index
            self._round_robin_index = (self._round_robin_index + 1) % len(self._members)
            return self._members[start:] + self._members[:start]

    def _acquire(self, member: RouterMember) -> None:
        with self._lock:
            member.busy += 1

    def _release(self, member: RouterMember) -> None:
        with self._lock:
            member.busy -= 1
