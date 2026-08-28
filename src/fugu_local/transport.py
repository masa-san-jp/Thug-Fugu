"""Small, injectable HTTP transport implementations for local backends.

The default transport keeps one connection per endpoint and calling thread.
This gives sequential calls from a worker thread connection reuse without ever
sharing an ``HTTPConnection`` concurrently between threads.  A response owns
the pool entry until it is closed, so streamed responses cannot race a later
request on the same connection.
"""

from __future__ import annotations

import http.client
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlsplit


class TransportError(RuntimeError):
    """Raised when a request cannot be opened or a transport is closed."""


@dataclass(frozen=True)
class TransportTiming:
    """Monotonic request timing emitted when a response is closed."""

    method: str
    endpoint: str
    connect_ms: Optional[float]
    ttfb_ms: Optional[float]
    read_ms: Optional[float]
    total_ms: float
    reused_connection: bool


class TransportResponse(Protocol):
    status: int
    headers: Mapping[str, str]

    def read(self, amount: int = -1) -> bytes: ...

    def iter_lines(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...

    def __enter__(self) -> "TransportResponse": ...

    def __exit__(self, exc_type, exc_value, traceback) -> None: ...


TimingHook = Callable[[TransportTiming], None]


class HTTPTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        body: Optional[bytes],
        headers: Mapping[str, str],
        timeout: float,
        timing_hook: Optional[TimingHook] = None,
    ) -> TransportResponse: ...

    def close(self) -> None: ...


@dataclass
class _ConnectionEntry:
    key: Tuple[str, str, int]
    connection: http.client.HTTPConnection
    lock: threading.Lock
    reusable: bool = True
    connected: bool = False


class PersistentHTTPTransport:
    """Thread-safe endpoint connection reuse using only ``http.client``.

    Connections are thread-local, while the transport tracks every entry so
    ``close`` can reclaim connections created by other request threads.  A
    failed response invalidates its entry; the next request creates a fresh
    connection.  POST requests are deliberately not retried to avoid duplicate
    model inference after an ambiguous write failure.
    """

    def __init__(self) -> None:
        self._local = threading.local()
        self._entries: Dict[int, _ConnectionEntry] = {}
        self._lock = threading.Lock()
        self._closed = False

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Optional[bytes],
        headers: Mapping[str, str],
        timeout: float,
        timing_hook: Optional[TimingHook] = None,
    ) -> TransportResponse:
        if timeout <= 0:
            raise TransportError("timeout must be positive")
        endpoint, path, connection_key = _parse_url(url)
        entry = self._entry_for(connection_key)
        entry.lock.acquire()
        started = time.perf_counter()
        connect_started = time.perf_counter()
        connect_ms: Optional[float] = None
        reused_connection = entry.connected and entry.reusable
        try:
            deadline = started + timeout
            if not entry.connected or not entry.reusable:
                entry.connection.timeout = _remaining(deadline)
                entry.connection.connect()
                entry.connected = True
                connect_ms = _elapsed_ms(connect_started)
            else:
                connect_ms = 0.0
            _set_socket_timeout(entry.connection, _remaining(deadline))
            entry.connection.request(
                method.upper(),
                path,
                body=body,
                headers=dict(headers),
            )
            request_sent = time.perf_counter()
            response = entry.connection.getresponse()
            ttfb_ms = _elapsed_ms(request_sent)
            return _PersistentResponse(
                transport=self,
                entry=entry,
                response=response,
                method=method.upper(),
                endpoint=endpoint,
                started=started,
                deadline=deadline,
                connect_ms=connect_ms,
                ttfb_ms=ttfb_ms,
                reused_connection=reused_connection,
                timing_hook=timing_hook,
            )
        except Exception as exc:
            entry.reusable = False
            entry.connected = False
            self._invalidate_entry(entry)
            entry.lock.release()
            if isinstance(exc, (OSError, TimeoutError)):
                raise
            if isinstance(exc, http.client.HTTPException):
                raise TransportError(f"HTTP transport request failed for {endpoint}") from exc
            raise TransportError(f"Could not open {endpoint}") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            with entry.lock:
                entry.reusable = False
                entry.connected = False
                entry.connection.close()

    def _entry_for(self, key: Tuple[str, str, int]) -> _ConnectionEntry:
        with self._lock:
            if self._closed:
                raise TransportError("HTTP transport is closed")
            local_entries = getattr(self._local, "entries", None)
            if local_entries is None:
                local_entries = {}
                self._local.entries = local_entries
            entry = local_entries.get(key)
            if entry is None or not entry.reusable:
                connection = _make_connection(key)
                entry = _ConnectionEntry(key=key, connection=connection, lock=threading.Lock())
                local_entries[key] = entry
                self._entries[id(entry)] = entry
            return entry

    def _invalidate_entry(self, entry: _ConnectionEntry) -> None:
        entry.reusable = False
        entry.connected = False
        with self._lock:
            self._entries.pop(id(entry), None)
        local_entries = getattr(self._local, "entries", {})
        if local_entries.get(entry.key) is entry:
            local_entries.pop(entry.key, None)
        entry.connection.close()


class _PersistentResponse:
    def __init__(
        self,
        *,
        transport: PersistentHTTPTransport,
        entry: _ConnectionEntry,
        response: http.client.HTTPResponse,
        method: str,
        endpoint: str,
        started: float,
        deadline: float,
        connect_ms: Optional[float],
        ttfb_ms: float,
        reused_connection: bool,
        timing_hook: Optional[TimingHook],
    ) -> None:
        self.status = response.status
        self.headers = {key.lower(): value for key, value in response.getheaders()}
        self._transport = transport
        self._entry = entry
        self._response = response
        self._response_will_close = bool(getattr(response, "will_close", False))
        self._method = method
        self._endpoint = endpoint
        self._started = started
        self._deadline = deadline
        self._connect_ms = connect_ms
        self._ttfb_ms = ttfb_ms
        self._reused_connection = reused_connection
        self._timing_hook = timing_hook
        self._read_started = time.perf_counter()
        self._read_complete = False
        self._closed = False

    def read(self, amount: int = -1) -> bytes:
        self._set_timeout()
        try:
            data = self._response.read(amount)
        except Exception as exc:
            self._mark_broken()
            if isinstance(exc, http.client.HTTPException):
                raise TransportError(f"HTTP response read failed for {self._endpoint}") from exc
            raise
        if amount < 0 or len(data) < amount:
            self._read_complete = True
        return data

    def iter_lines(self) -> Iterator[bytes]:
        iterator = iter(self._response)
        try:
            while True:
                self._set_timeout()
                try:
                    line = next(iterator)
                except StopIteration:
                    break
                yield line
            self._read_complete = True
        except Exception as exc:
            self._mark_broken()
            if isinstance(exc, http.client.HTTPException):
                raise TransportError(f"HTTP response read failed for {self._endpoint}") from exc
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        finally:
            if (
                self._read_complete
                and not self._response_will_close
                and _connection_can_reuse(self.headers)
            ):
                self._entry.reusable = True
            else:
                self._mark_broken()
            self._entry.lock.release()
            self._emit_timing()

    def __enter__(self) -> "_PersistentResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _set_timeout(self) -> None:
        _set_socket_timeout(self._entry.connection, _remaining(self._deadline))

    def _mark_broken(self) -> None:
        self._read_complete = False
        self._entry.reusable = False
        self._entry.connected = False
        self._transport._invalidate_entry(self._entry)

    def _emit_timing(self) -> None:
        if self._timing_hook is None:
            return
        timing = TransportTiming(
            method=self._method,
            endpoint=self._endpoint,
            connect_ms=self._connect_ms,
            ttfb_ms=self._ttfb_ms,
            read_ms=_elapsed_ms(self._read_started),
            total_ms=_elapsed_ms(self._started),
            reused_connection=self._reused_connection,
        )
        try:
            self._timing_hook(timing)
        except Exception:
            return


class UrllibHTTPTransport:
    """Compatibility transport useful for tests and custom urllib handlers."""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Optional[bytes],
        headers: Mapping[str, str],
        timeout: float,
        timing_hook: Optional[TimingHook] = None,
    ) -> TransportResponse:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method=method.upper(),
        )
        started = time.perf_counter()
        response = urllib.request.urlopen(request, timeout=timeout)
        return _UrllibResponse(
            response=response,
            method=method.upper(),
            endpoint=_safe_endpoint(url),
            started=started,
            timing_hook=timing_hook,
        )

    def close(self) -> None:
        return


class _UrllibResponse:
    def __init__(
        self,
        *,
        response,
        method: str,
        endpoint: str,
        started: float,
        timing_hook: Optional[TimingHook],
    ) -> None:
        self._response = response
        self.status = getattr(response, "status", 200)
        self.headers = {
            str(key).lower(): str(value) for key, value in getattr(response, "headers", {}).items()
        }
        self._method = method
        self._endpoint = endpoint
        self._started = started
        self._read_started = time.perf_counter()
        self._timing_hook = timing_hook
        self._closed = False
        self._read_complete = False

    def read(self, amount: int = -1) -> bytes:
        data = self._response.read() if amount < 0 else self._response.read(amount)
        if amount < 0 or len(data) < amount:
            self._read_complete = True
        return data

    def iter_lines(self) -> Iterator[bytes]:
        try:
            for line in self._response:
                yield line
            self._read_complete = True
        except Exception:
            self._read_complete = False
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._response, "close", None)
        if callable(close):
            close()
        else:
            exit_method = getattr(self._response, "__exit__", None)
            if callable(exit_method):
                exit_method(None, None, None)
        if self._timing_hook is not None:
            try:
                self._timing_hook(
                    TransportTiming(
                        method=self._method,
                        endpoint=self._endpoint,
                        connect_ms=None,
                        ttfb_ms=_elapsed_ms(self._started),
                        read_ms=_elapsed_ms(self._read_started),
                        total_ms=_elapsed_ms(self._started),
                        reused_connection=False,
                    )
                )
            except Exception:
                return

    def __enter__(self) -> "_UrllibResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _parse_url(url: str) -> Tuple[str, str, Tuple[str, str, int]]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TransportError("HTTP transport requires an absolute http(s) URL")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise TransportError("HTTP URL has an invalid port") from exc
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    endpoint = f"{parsed.scheme}://{parsed.netloc}"
    return endpoint, path, (parsed.scheme, parsed.hostname, port)


def _make_connection(key: Tuple[str, str, int]) -> http.client.HTTPConnection:
    scheme, host, port = key
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, context=ssl.create_default_context())
    return http.client.HTTPConnection(host, port)


def _set_socket_timeout(connection: http.client.HTTPConnection, timeout: float) -> None:
    if timeout <= 0:
        raise TimeoutError("HTTP request deadline exceeded")
    if connection.sock is not None:
        connection.sock.settimeout(timeout)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("HTTP request deadline exceeded")
    return remaining


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, time.perf_counter() - started) * 1000, 3)


def _connection_can_reuse(headers: Mapping[str, str]) -> bool:
    return headers.get("connection", "").casefold() != "close"


def _safe_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return parsed.path or "?"
