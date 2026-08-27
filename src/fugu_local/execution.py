"""Bounded, lifecycle-aware request fan-out execution.

The executor owns a fixed worker pool for the lifetime of its orchestrator.
Tasks are submitted in one batch and results are returned in input order. A
deadline prevents queued work from starting, while a running backend call is
reported explicitly because Python cannot safely interrupt arbitrary I/O.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, List, Optional, Set, Tuple, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class FanoutTask(Generic[T]):
    """One independently executable task in a fan-out batch."""

    key: str
    callback: Callable[[], T]


@dataclass(frozen=True)
class TaskTiming:
    """Monotonic timing data emitted at task start and terminal state."""

    key: str
    state: str
    submitted_at: float
    started_at: Optional[float]
    finished_at: Optional[float]
    queue_wait_ms: Optional[float]


@dataclass(frozen=True)
class TaskResult(Generic[T]):
    """Result or lifecycle state for one submitted task."""

    key: str
    state: str
    value: Optional[T] = None
    error: Optional[BaseException] = None
    submitted_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    queue_wait_ms: Optional[float] = None
    cancel_requested: bool = False


TimingHook = Callable[[TaskTiming], None]


class FanoutExecutor:
    """A fixed-size executor reused across requests by one orchestrator."""

    def __init__(self, max_workers: int, *, thread_name_prefix: str = "fugu-fanout") -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        self.max_workers = max_workers
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )
        self._lock = threading.Lock()
        self._futures: Set[concurrent.futures.Future] = set()
        self._closed = False

    @property
    def active_task_count(self) -> int:
        """Return submitted tasks not yet observed as done by the pool."""

        with self._lock:
            return len(self._futures)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def run(
        self,
        tasks: List[FanoutTask[T]],
        *,
        deadline: Optional[float] = None,
        timing_hook: Optional[TimingHook] = None,
    ) -> List[TaskResult[T]]:
        """Submit all tasks and return results in the original task order.

        ``deadline`` is an absolute ``time.perf_counter()`` value. Tasks that
        remain queued at the deadline are cancelled when possible. A task that
        already entered its callback is returned as ``running_at_deadline``;
        its underlying future remains tracked until it really completes.
        """

        if not tasks:
            return []
        submitted: List[Tuple[FanoutTask[T], float, concurrent.futures.Future]] = []
        for task in tasks:
            submitted_at = time.perf_counter()
            with self._lock:
                if self._closed:
                    raise RuntimeError("fan-out executor is closed")
                future = self._executor.submit(
                    self._execute_task,
                    task,
                    submitted_at,
                    deadline,
                    timing_hook,
                )
                self._futures.add(future)
            future.add_done_callback(self._forget_future)
            submitted.append((task, submitted_at, future))

        futures = [future for _, _, future in submitted]
        if deadline is None:
            done = set(futures)
        else:
            remaining = max(0.0, deadline - time.perf_counter())
            done, _ = concurrent.futures.wait(futures, timeout=remaining)

        results = []
        for task, submitted_at, future in submitted:
            if future in done or future.done():
                results.append(self._future_result(task, submitted_at, future))
                continue
            if future.cancel():
                result = TaskResult(
                    key=task.key,
                    state="not_started",
                    submitted_at=submitted_at,
                    finished_at=time.perf_counter(),
                    queue_wait_ms=_queue_wait_ms(submitted_at),
                    cancel_requested=True,
                )
                _emit_timing(timing_hook, result)
                results.append(result)
                continue
            if future.done():
                results.append(self._future_result(task, submitted_at, future))
                continue
            result = TaskResult(
                key=task.key,
                state="running_at_deadline",
                submitted_at=submitted_at,
                started_at=None,
                cancel_requested=True,
            )
            _emit_timing(timing_hook, result)
            results.append(result)
        return results

    def close(self) -> None:
        """Stop accepting work and wait for running callbacks to release resources."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> "FanoutExecutor":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _execute_task(
        self,
        task: FanoutTask[T],
        submitted_at: float,
        deadline: Optional[float],
        timing_hook: Optional[TimingHook],
    ) -> TaskResult[T]:
        now = time.perf_counter()
        if deadline is not None and now >= deadline:
            result = TaskResult(
                key=task.key,
                state="not_started",
                submitted_at=submitted_at,
                finished_at=now,
                queue_wait_ms=_queue_wait_ms(submitted_at, now),
                cancel_requested=True,
            )
            _emit_timing(timing_hook, result)
            return result

        started_at = now
        _emit_timing_event(
            timing_hook,
            TaskTiming(
                key=task.key,
                state="started",
                submitted_at=submitted_at,
                started_at=started_at,
                finished_at=None,
                queue_wait_ms=_queue_wait_ms(submitted_at, started_at),
            ),
        )
        try:
            value = task.callback()
        except Exception as exc:  # noqa: BLE001 - isolate one worker from the batch.
            result = TaskResult(
                key=task.key,
                state="failed",
                error=exc,
                submitted_at=submitted_at,
                started_at=started_at,
                finished_at=time.perf_counter(),
                queue_wait_ms=_queue_wait_ms(submitted_at, started_at),
            )
        else:
            result = TaskResult(
                key=task.key,
                state="completed",
                value=value,
                submitted_at=submitted_at,
                started_at=started_at,
                finished_at=time.perf_counter(),
                queue_wait_ms=_queue_wait_ms(submitted_at, started_at),
            )
        _emit_timing(timing_hook, result)
        return result

    def _future_result(
        self,
        task: FanoutTask[T],
        submitted_at: float,
        future: concurrent.futures.Future,
    ) -> TaskResult[T]:
        if future.cancelled():
            return TaskResult(
                key=task.key,
                state="not_started",
                submitted_at=submitted_at,
                finished_at=time.perf_counter(),
                queue_wait_ms=_queue_wait_ms(submitted_at),
                cancel_requested=True,
            )
        try:
            result = future.result()
        except Exception as exc:  # pragma: no cover - _execute_task isolates callbacks.
            return TaskResult(
                key=task.key,
                state="failed",
                error=exc,
                submitted_at=submitted_at,
            )
        return result

    def _forget_future(self, future: concurrent.futures.Future) -> None:
        with self._lock:
            self._futures.discard(future)


def _queue_wait_ms(submitted_at: float, started_at: Optional[float] = None) -> float:
    measured_at = time.perf_counter() if started_at is None else started_at
    return round(max(0.0, measured_at - submitted_at) * 1000, 3)


def _emit_timing(hook: Optional[TimingHook], result: TaskResult) -> None:
    _emit_timing_event(
        hook,
        TaskTiming(
            key=result.key,
            state=result.state,
            submitted_at=result.submitted_at,
            started_at=result.started_at,
            finished_at=result.finished_at,
            queue_wait_ms=result.queue_wait_ms,
        ),
    )


def _emit_timing_event(hook: Optional[TimingHook], timing: TaskTiming) -> None:
    if hook is None:
        return
    try:
        hook(timing)
    except Exception:  # noqa: BLE001 - observability must not break execution.
        return
