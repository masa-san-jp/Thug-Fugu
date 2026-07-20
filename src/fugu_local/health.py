"""Background active health probes for model-pool endpoints."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .backends import probe_ollama, probe_openai_compatible
from .config import ModelPoolConfig
from .routing import ModelRouter


@dataclass(frozen=True)
class HealthProbeTarget:
    pool_name: str
    member_key: str
    backend: str
    model: str
    interval_seconds: float
    timeout_seconds: float
    require_model: bool
    api_key: Optional[str] = field(default=None, repr=False, compare=False)


ProbeFunction = Callable[[HealthProbeTarget], bool]


class HealthMonitor:
    """Poll configured pool endpoints and update their routers."""

    def __init__(
        self,
        routers: Mapping[str, ModelRouter],
        pools: Iterable[ModelPoolConfig],
        *,
        probe: Optional[ProbeFunction] = None,
    ):
        self._routers = routers
        self._targets = self._build_targets(pools)
        self._probe = probe or self._default_probe
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._next_probe_at: Dict[Tuple[str, str], float] = {}

    @property
    def targets(self) -> List[HealthProbeTarget]:
        return list(self._targets)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if not self._targets:
            return
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop_event.clear()
            self.poll_once()
            now = time.monotonic()
            self._next_probe_at = {
                (target.pool_name, target.member_key): now + target.interval_seconds
                for target in self._targets
            }
            self._thread = threading.Thread(
                target=self._run,
                name="fugu-local-health-monitor",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
        if thread is not threading.current_thread():
            max_timeout = max(
                (target.timeout_seconds for target in self._targets),
                default=0.0,
            )
            thread.join(timeout=max_timeout + 1.0)
        with self._lifecycle_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def poll_once(self) -> None:
        """Synchronously probe every target once. Primarily useful for tests."""

        for target in self._targets:
            self._poll_target(target)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now = time.monotonic()
            due = [
                target
                for target in self._targets
                if self._next_probe_at[(target.pool_name, target.member_key)] <= now
            ]
            if due:
                for target in due:
                    if self._stop_event.is_set():
                        return
                    self._poll_target(target)
                    self._next_probe_at[(target.pool_name, target.member_key)] = (
                        time.monotonic() + target.interval_seconds
                    )
                continue

            next_due = min(self._next_probe_at.values())
            self._stop_event.wait(max(0.0, next_due - now))

    def _poll_target(self, target: HealthProbeTarget) -> None:
        try:
            healthy = bool(self._probe(target))
        except Exception:  # noqa: BLE001 - probes must not terminate the monitor.
            healthy = False
        self._routers[target.pool_name].record_probe_result(target.member_key, healthy)

    @staticmethod
    def _build_targets(pools: Iterable[ModelPoolConfig]) -> List[HealthProbeTarget]:
        targets = []
        for pool in pools:
            if not pool.health.enabled:
                continue
            for endpoint in pool.endpoints:
                targets.append(
                    HealthProbeTarget(
                        pool_name=pool.name,
                        member_key=endpoint,
                        backend=pool.backend,
                        model=pool.model,
                        interval_seconds=pool.health.interval_seconds,
                        timeout_seconds=pool.health.timeout_seconds,
                        require_model=pool.health.require_model,
                        api_key=pool.api_key,
                    )
                )
        return targets

    @staticmethod
    def _default_probe(target: HealthProbeTarget) -> bool:
        if target.backend == "ollama":
            return probe_ollama(
                target.member_key,
                timeout_seconds=target.timeout_seconds,
                model=target.model,
                require_model=target.require_model,
            )
        if target.backend == "openai-compatible":
            return probe_openai_compatible(
                target.member_key,
                timeout_seconds=target.timeout_seconds,
                api_key=target.api_key,
                model=target.model,
                require_model=target.require_model,
            )
        return False
