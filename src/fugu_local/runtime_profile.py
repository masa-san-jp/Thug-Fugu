"""Static runtime capability and endpoint-capacity profiles.

This module owns the small, backend-neutral contract shared by configuration,
future scheduling, and benchmark reporting.  It intentionally does not probe
endpoints or discover hardware; those are separate health and discovery
concerns.
"""

from __future__ import annotations

import math
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping

CAPABILITY_NAMES = (
    "streaming",
    "tool_calling",
    "usage_accounting",
    "structured_output",
    "prefix_cache",
)
CAPABILITY_STATES = {"supported", "unsupported", "unknown"}
PROFILE_VALUE_SOURCES = {"configured", "probed", "default"}


def endpoint_url_from_config(raw: Any, field_name: str = "endpoint") -> str:
    """Extract a URL from a legacy string or an endpoint object.

    Object endpoints use ``url``.  ``base_url`` is accepted as an explicit
    alias so direct model entries can be migrated without a semantic change.
    The raw URL is returned for backend use; profiles only retain its safe
    canonical identity.
    """

    if isinstance(raw, str):
        if raw.strip():
            return raw.strip()
        raise ValueError(f"{field_name} must be a non-empty URL string")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field_name} must be a URL string or object")

    url = raw.get("url")
    base_url = raw.get("base_url")
    if url is not None and base_url is not None and url != base_url:
        raise ValueError(f"{field_name}.url and {field_name}.base_url must match when both set")
    candidate = url if url is not None else base_url
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"{field_name}.url must be a non-empty URL string")
    return candidate.strip()


def canonical_endpoint_identity(endpoint: str) -> str:
    """Return a stable endpoint identity without credentials or query data.

    Scheme and hostname are lower-cased, default HTTP(S) ports are omitted,
    root/trailing path slashes are normalized, and userinfo, query, and
    fragment are never retained.  Non-URL legacy labels are preserved after
    removing a trailing slash so old configurations continue to load.
    """

    value = endpoint.strip()
    if not value:
        raise ValueError("endpoint URL must not be empty")
    parsed = urllib.parse.urlsplit(value)
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint URL has an invalid port or IPv6 host") from exc
    if not hostname:
        label = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if "@" in label:
            raise ValueError("endpoint credentials require an unambiguous URL authority")
        return label

    scheme = parsed.scheme.lower()
    hostname = hostname.lower()
    if ":" in hostname:
        host = f"[{hostname}]"
    else:
        host = hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, host, path, "", ""))


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Tri-state capability flags; ``unknown`` is never treated as supported."""

    streaming: str = "unknown"
    tool_calling: str = "unknown"
    usage_accounting: str = "unknown"
    structured_output: str = "unknown"
    prefix_cache: str = "unknown"

    def __post_init__(self) -> None:
        for name in CAPABILITY_NAMES:
            value = getattr(self, name)
            if value not in CAPABILITY_STATES:
                raise ValueError(f"capabilities.{name} must be one of {sorted(CAPABILITY_STATES)}")

    @classmethod
    def from_mapping(cls, raw: Any) -> "RuntimeCapabilities":
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise ValueError("capabilities must be an object")
        unknown = sorted(set(raw) - set(CAPABILITY_NAMES))
        if unknown:
            raise ValueError(f"unsupported capability field(s): {', '.join(unknown)}")
        values = {name: raw.get(name, "unknown") for name in CAPABILITY_NAMES}
        return cls(**values)

    def to_dict(self) -> Dict[str, str]:
        return {name: getattr(self, name) for name in CAPABILITY_NAMES}

    def state_for(self, name: str) -> str:
        if name not in CAPABILITY_NAMES:
            raise ValueError(f"unknown capability: {name}")
        return getattr(self, name)

    def supports(self, name: str) -> bool:
        return self.state_for(name) == "supported"


@dataclass(frozen=True)
class EndpointRuntimeProfile:
    """Backend-neutral static profile for one physical inference endpoint."""

    identity: str
    backend: str
    runtime: str
    model: str
    max_inflight: int = 1
    weight: float = 1.0
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)
    value_source: str = "default"

    def __post_init__(self) -> None:
        for field_name in ("identity", "backend", "runtime", "model"):
            if (
                not isinstance(getattr(self, field_name), str)
                or not getattr(self, field_name).strip()
            ):
                raise ValueError(f"{field_name} must be a non-empty string")
        if isinstance(self.max_inflight, bool) or not isinstance(self.max_inflight, int):
            raise ValueError("max_inflight must be a positive integer")
        if self.max_inflight <= 0:
            raise ValueError("max_inflight must be a positive integer")
        if isinstance(self.weight, bool) or not isinstance(self.weight, (int, float)):
            raise ValueError("weight must be a positive number")
        if not math.isfinite(float(self.weight)) or self.weight <= 0:
            raise ValueError("weight must be a positive number")
        if not isinstance(self.capabilities, RuntimeCapabilities):
            raise ValueError("capabilities must be a RuntimeCapabilities object")
        if self.value_source not in PROFILE_VALUE_SOURCES:
            raise ValueError(f"value_source must be one of {sorted(PROFILE_VALUE_SOURCES)}")

    @classmethod
    def from_endpoint_config(
        cls,
        raw: Any,
        *,
        backend: str,
        model: str,
        field_name: str = "endpoint",
    ) -> "EndpointRuntimeProfile":
        endpoint = endpoint_url_from_config(raw, field_name)
        settings: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        configured_fields = {
            "runtime",
            "max_inflight",
            "weight",
            "capabilities",
            "value_source",
        }
        has_configured_value = any(key in settings for key in configured_fields)
        value_source = settings.get(
            "value_source", "configured" if has_configured_value else "default"
        )
        try:
            capabilities = RuntimeCapabilities.from_mapping(settings.get("capabilities"))
            return cls(
                identity=canonical_endpoint_identity(endpoint),
                backend=backend,
                runtime=settings.get("runtime", backend),
                model=model,
                max_inflight=settings.get("max_inflight", 1),
                weight=settings.get("weight", 1.0),
                capabilities=capabilities,
                value_source=value_source,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field_name} runtime profile: {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        """Serialize only safe profile fields; raw URL material is not retained."""

        return {
            "identity": self.identity,
            "backend": self.backend,
            "runtime": self.runtime,
            "model": self.model,
            "max_inflight": self.max_inflight,
            "weight": float(self.weight),
            "capabilities": self.capabilities.to_dict(),
            "value_source": self.value_source,
        }
