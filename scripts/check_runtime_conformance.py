#!/usr/bin/env python3
"""Check the protocol capabilities of a local model runtime.

The runner deliberately lives outside the orchestrator.  It probes only the
smallest common protocol surface and writes an explicit capability matrix:
every capability is ``supported``, ``unsupported``, or ``unknown``.  A JSON
fixture can be supplied for offline CI; fixtures are never presented as real
runtime or hardware verification.

The manifest format is intentionally compatible with a normal Thug-Fugu JSON
configuration.  Runtime metadata is kept under ``runtime_conformance`` and is
ignored by the core config loader, so changing runtimes remains a config-only
operation.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import platform
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
SUPPORTED_STATUSES = {"supported", "unsupported", "unknown"}
SUPPORTED_RUNTIMES = {
    "ollama": {
        "family": "ollama",
        "backend": "ollama",
        "health_path": "/api/tags",
        "chat_path": "/api/chat",
    },
    "openai-compatible": {
        "family": "openai-compatible",
        "backend": "openai-compatible",
        "health_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
    },
    "llama.cpp": {
        "family": "openai-compatible",
        "backend": "openai-compatible",
        "health_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
    },
    "vLLM": {
        "family": "openai-compatible",
        "backend": "openai-compatible",
        "health_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
    },
    "SGLang": {
        "family": "openai-compatible",
        "backend": "openai-compatible",
        "health_path": "/v1/models",
        "chat_path": "/v1/chat/completions",
    },
}

CAPABILITIES = (
    "non_stream",
    "stream",
    "usage",
    "seed",
    "tools",
    "structured_output",
    "max_concurrency",
    "queue_behavior",
    "warm_cold",
    "prefix_cache_visibility",
    "health_models",
    "timeout_error_shape",
)

_SECRET_KEY_RE = re.compile(r"(?:api[_-]?key|authorization|password|secret|token)", re.I)


class ConformanceError(ValueError):
    """Raised when a manifest or fixture cannot be interpreted safely."""


class ProbeError(RuntimeError):
    """A sanitized error from a live runtime probe."""

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _status(status: str, evidence: str) -> Dict[str, str]:
    if status not in SUPPORTED_STATUSES:
        raise ConformanceError("capability status must be supported, unsupported, or unknown")
    return {"status": status, "evidence": _sanitize_text(evidence)}


def _unknown(reason: str) -> Dict[str, str]:
    return _status("unknown", reason)


def _sanitize_text(value: Any) -> str:
    """Remove secrets and machine-local path fragments from evidence text."""

    text = str(value)
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1<redacted>", text)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[=:]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )
    text = text.replace("~", "<path>")
    text = re.sub(
        r"(?i)(?:/Users/[^\s/]+|/home/[^\s/]+|/private/[^\s]+|/tmp/[^\s]+|/var/[^\s]+)(?:/[^\s]*)?",
        "<path>",
        text,
    )
    text = re.sub(r"(?i)[A-Za-z]:\\[^\s]+", "<path>", text)
    return text


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY_RE.search(key):
        return "<redacted>" if value else value
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(child, key=key) for child in value]
    if isinstance(value, tuple):
        return [_sanitize_value(child, key=key) for child in value]
    if isinstance(value, str):
        if re.search(r"(?:path|file|directory|dir)$", key, re.I) and value:
            return "<path>"
        return _sanitize_text(value)
    return value


def sanitize_url(value: str) -> str:
    """Keep a loopback endpoint useful while hiding remote hosts and secrets."""

    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConformanceError("base_url must be an http(s) URL with a host")
    host = parsed.hostname.lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        host = "<host>"
    if ":" in host and host != "<host>":
        host = "[{}]".format(host)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConformanceError("base_url has an invalid port") from exc
    netloc = host if port is None else "{}:{}".format(host, port)
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def _request_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConformanceError("{} not found: {}".format(label, path)) from exc
    except json.JSONDecodeError as exc:
        raise ConformanceError("invalid {} JSON: {}".format(label, exc)) from exc
    if not isinstance(payload, dict):
        raise ConformanceError("{} must contain a JSON object: {}".format(label, path))
    return payload


def _metadata(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = manifest.get("runtime_conformance", manifest)
    if not isinstance(metadata, Mapping):
        raise ConformanceError("runtime_conformance must be a JSON object")
    return metadata


def _model_from_manifest(manifest: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    model = metadata.get("model")
    if isinstance(model, Mapping):
        model = model.get("name")
    if not model:
        models = manifest.get("models")
        if isinstance(models, list) and models and isinstance(models[0], Mapping):
            model = models[0].get("model")
    if not isinstance(model, str) or not model.strip():
        raise ConformanceError("manifest must specify runtime_conformance.model or models[0].model")
    return model.strip()


def _base_url_from_manifest(manifest: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    base_url = metadata.get("base_url") or metadata.get("endpoint")
    if not base_url:
        models = manifest.get("models")
        if isinstance(models, list) and models and isinstance(models[0], Mapping):
            base_url = models[0].get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConformanceError("manifest must specify base_url or models[0].base_url")
    return base_url.strip()


def _api_key_from_manifest(
    manifest: Mapping[str, Any], metadata: Mapping[str, Any]
) -> Optional[str]:
    """Read auth only for the live request; it is never returned in an artifact."""

    api_key_env = metadata.get("api_key_env")
    if api_key_env is not None and (not isinstance(api_key_env, str) or not api_key_env):
        raise ConformanceError("api_key_env must be a non-empty string")
    api_key = None
    if api_key_env:
        api_key = os.environ.get(api_key_env)
    if api_key:
        return api_key
    models = manifest.get("models")
    if not isinstance(models, list) or not models or not isinstance(models[0], Mapping):
        return None
    configured = models[0].get("api_key")
    if not isinstance(configured, str) or not configured:
        return None
    if configured.startswith("${") and configured.endswith("}"):
        return os.environ.get(configured[2:-1])
    return configured


def _runtime_name(metadata: Mapping[str, Any]) -> str:
    runtime = metadata.get("runtime")
    if not isinstance(runtime, str) or runtime not in SUPPORTED_RUNTIMES:
        raise ConformanceError(
            "runtime must be one of: {}".format(", ".join(SUPPORTED_RUNTIMES))
        )
    return runtime


def _launch_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    launch = metadata.get("launch", {})
    if not isinstance(launch, Mapping):
        raise ConformanceError("runtime_conformance.launch must be a JSON object")
    command = launch.get("command", [])
    flags = launch.get("flags", [])
    if isinstance(command, str):
        command = [command]
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise ConformanceError("launch.command must be a string or list of strings")
    if not isinstance(flags, list) or not all(isinstance(item, str) for item in flags):
        raise ConformanceError("launch.flags must be a list of strings")
    return {
        "command": [_sanitize_text(item) for item in command],
        "flags": [_sanitize_text(item) for item in flags],
        "automatic": False,
    }


def _auto_hardware() -> Dict[str, Any]:
    return {
        "source": "auto",
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "gpu": "unknown",
        "ram_gb": "unknown",
        "vram_gb": "unknown",
        "power": "not_recorded",
    }


def _hardware(metadata: Mapping[str, Any], hardware_path: Optional[Path]) -> Dict[str, Any]:
    if hardware_path is not None:
        return {
            "source": "provided",
            **_sanitize_value(_load_json(hardware_path, "hardware metadata")),
        }
    provided = metadata.get("hardware")
    if isinstance(provided, Mapping):
        return {"source": "manifest", **_sanitize_value(provided)}
    return _auto_hardware()


def _model_metadata(
    metadata: Mapping[str, Any], model: str, quantization: Optional[str]
) -> Dict[str, str]:
    model_name = model
    model_path = "/" in model or "\\" in model
    if model_path:
        model_name = "<model-path>"
    selected_quantization = quantization or metadata.get("quantization") or "unknown"
    if not isinstance(selected_quantization, str):
        selected_quantization = "unknown"
    return {
        "name": _sanitize_text(model_name),
        "quantization": _sanitize_text(selected_quantization),
    }


def _empty_capabilities(reason: str) -> Dict[str, Dict[str, str]]:
    return {name: _unknown(reason) for name in CAPABILITIES}


def _apply_explicit_capabilities(
    capabilities: Dict[str, Dict[str, str]], raw: Any
) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ConformanceError("capabilities must be a JSON object")
    for name, value in raw.items():
        if name not in CAPABILITIES:
            raise ConformanceError("unknown capability: {}".format(name))
        if isinstance(value, str):
            capabilities[name] = _status(value, "explicit manifest/fixture observation")
        elif isinstance(value, Mapping):
            status = value.get("status")
            evidence = value.get("evidence", "explicit manifest/fixture observation")
            if not isinstance(status, str) or not isinstance(evidence, str):
                raise ConformanceError("capability entries need string status and evidence")
            capabilities[name] = _status(status, evidence)
        else:
            raise ConformanceError("capability entries must be strings or objects")


def _success(status: int) -> bool:
    return 200 <= status < 300


def _shape_health(runtime: str, body: Any) -> bool:
    if not isinstance(body, Mapping):
        return False
    if runtime == "ollama":
        return isinstance(body.get("models"), list)
    return isinstance(body.get("data"), list)


def _shape_non_stream(runtime: str, body: Any) -> Tuple[bool, bool]:
    if not isinstance(body, Mapping):
        return False, False
    if runtime == "ollama":
        message = body.get("message")
        valid = isinstance(message, Mapping) and isinstance(message.get("content"), str)
        usage = any(key in body for key in ("prompt_eval_count", "eval_count"))
        return valid, usage
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return False, False
    first = choices[0]
    valid = isinstance(first, Mapping) and isinstance(first.get("message"), Mapping)
    usage = isinstance(body.get("usage"), Mapping)
    return valid, usage


def _shape_stream(runtime: str, lines: Iterable[str]) -> bool:
    observed = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if runtime != "ollama":
            if line == "data: [DONE]":
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
        try:
            payload = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, Mapping):
            return False
        if runtime == "ollama":
            observed = (
                observed
                or isinstance(payload.get("message"), Mapping)
                or bool(payload.get("done"))
            )
        else:
            choices = payload.get("choices")
            observed = observed or isinstance(choices, list)
    return observed


def _fixture_response(responses: Mapping[str, Any], name: str) -> Tuple[int, Any]:
    raw = responses.get(name)
    if not isinstance(raw, Mapping):
        raise ConformanceError("fixture.responses.{} must be an object".format(name))
    status = raw.get("status", 200)
    if not isinstance(status, int):
        raise ConformanceError("fixture response status must be an integer")
    return status, raw.get("body", raw.get("json", raw.get("lines")))


def _fixture_artifact(
    manifest: Mapping[str, Any],
    metadata: Mapping[str, Any],
    fixture: Mapping[str, Any],
    *,
    hardware_path: Optional[Path],
    quantization: Optional[str],
    runtime_version: Optional[str],
) -> Dict[str, Any]:
    runtime = _runtime_name(metadata)
    model = _model_from_manifest(manifest, metadata)
    base_url = sanitize_url(_base_url_from_manifest(manifest, metadata))
    capabilities = _empty_capabilities("not probed")
    responses = fixture.get("responses", {})
    if not isinstance(responses, Mapping):
        raise ConformanceError("fixture.responses must be a JSON object")

    health_status, health_body = _fixture_response(responses, "health_models")
    if _success(health_status):
        capabilities["health_models"] = _status(
            "supported" if _shape_health(runtime, health_body) else "unknown",
            "fixture health/models response shape",
        )
    else:
        capabilities["health_models"] = _status(
            "unsupported", "fixture health/models status {}".format(health_status)
        )

    non_stream_status, non_stream_body = _fixture_response(responses, "non_stream")
    if _success(non_stream_status):
        valid, has_usage = _shape_non_stream(runtime, non_stream_body)
        capabilities["non_stream"] = _status(
            "supported" if valid else "unknown", "fixture non-stream response shape"
        )
        if has_usage:
            capabilities["usage"] = _status("supported", "usage field present in fixture response")
    else:
        capabilities["non_stream"] = _status(
            "unsupported", "fixture non-stream status {}".format(non_stream_status)
        )

    stream_status, stream_body = _fixture_response(responses, "stream")
    if _success(stream_status) and isinstance(stream_body, list):
        capabilities["stream"] = _status(
            "supported"
            if _shape_stream(runtime, [str(line) for line in stream_body])
            else "unknown",
            "fixture stream response shape",
        )
    elif _success(stream_status):
        capabilities["stream"] = _unknown("fixture stream response must contain lines")
    else:
        capabilities["stream"] = _status(
            "unsupported", "fixture stream status {}".format(stream_status)
        )

    _apply_explicit_capabilities(capabilities, fixture.get("capabilities"))
    _apply_explicit_capabilities(capabilities, metadata.get("capabilities"))
    return _build_artifact(
        runtime=runtime,
        model=model,
        base_url=base_url,
        metadata=metadata,
        hardware=_hardware(metadata, hardware_path),
        capabilities=capabilities,
        source="fixture",
        runtime_version=runtime_version
        or fixture.get("runtime_version")
        or metadata.get("runtime_version"),
        quantization=quantization,
        notes=["Fixture contract only; no external runtime or model was contacted."],
    )


def _request_json(
    url: str,
    *,
    method: str,
    payload: Optional[Mapping[str, Any]],
    headers: Mapping[str, str],
    timeout: float,
) -> Tuple[int, Any]:
    data = None
    request_headers = {"Accept": "application/json", **headers}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(getattr(response, "status", response.getcode()))
    except urllib.error.HTTPError as exc:
        raise ProbeError("http error {}".format(exc.code), status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(type(exc).__name__) from exc
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("response was not JSON", status=status) from exc


def _request_stream(
    url: str,
    *,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: float,
) -> Tuple[int, List[str]]:
    request_headers = {"Accept": "application/json", **headers, "Content-Type": "application/json"}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            lines = [line.decode("utf-8", errors="replace") for line in response]
    except urllib.error.HTTPError as exc:
        raise ProbeError("http error {}".format(exc.code), status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(type(exc).__name__) from exc
    return status, lines


def _live_artifact(
    manifest: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    hardware_path: Optional[Path],
    quantization: Optional[str],
    runtime_version: Optional[str],
    timeout: float,
) -> Dict[str, Any]:
    runtime = _runtime_name(metadata)
    runtime_spec = SUPPORTED_RUNTIMES[runtime]
    model = _model_from_manifest(manifest, metadata)
    raw_base_url = _base_url_from_manifest(manifest, metadata)
    base_url = sanitize_url(raw_base_url)
    capabilities = _empty_capabilities("not probed or not observable from the common protocol")
    headers: Dict[str, str] = {}
    api_key = _api_key_from_manifest(manifest, metadata)
    if api_key:
        headers["Authorization"] = "Bearer {}".format(api_key)

    health_url = _request_url(raw_base_url, runtime_spec["health_path"])
    try:
        health_status, health_body = _request_json(
            health_url, method="GET", payload=None, headers=headers, timeout=timeout
        )
        capabilities["health_models"] = _status(
            "supported"
            if _success(health_status) and _shape_health(runtime, health_body)
            else "unknown",
            "live health/models response shape",
        )
    except ProbeError as exc:
        capabilities["health_models"] = _status(
            "unsupported" if exc.status in {400, 404, 405, 501} else "unknown",
            "health/models probe failed: {}".format(exc),
        )

    common_payload: Dict[str, Any]
    if runtime == "ollama":
        common_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "conformance probe"}],
            "stream": False,
        }
    else:
        common_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "conformance probe"}],
            "stream": False,
            "max_tokens": 8,
        }
    chat_url = _request_url(raw_base_url, runtime_spec["chat_path"])
    try:
        non_stream_status, non_stream_body = _request_json(
            chat_url, method="POST", payload=common_payload, headers=headers, timeout=timeout
        )
        valid, has_usage = _shape_non_stream(runtime, non_stream_body)
        capabilities["non_stream"] = _status(
            "supported" if _success(non_stream_status) and valid else "unknown",
            "live non-stream response shape",
        )
        if has_usage:
            capabilities["usage"] = _status("supported", "usage field present in live response")
    except ProbeError as exc:
        capabilities["non_stream"] = _status(
            "unsupported" if exc.status in {400, 404, 405, 501} else "unknown",
            "non-stream probe failed: {}".format(exc),
        )

    stream_payload = dict(common_payload)
    stream_payload["stream"] = True
    try:
        stream_status, stream_lines = _request_stream(
            chat_url, payload=stream_payload, headers=headers, timeout=timeout
        )
        capabilities["stream"] = _status(
            "supported"
            if _success(stream_status) and _shape_stream(runtime, stream_lines)
            else "unknown",
            "live stream response shape",
        )
    except ProbeError as exc:
        capabilities["stream"] = _status(
            "unsupported" if exc.status in {400, 404, 405, 501} else "unknown",
            "stream probe failed: {}".format(exc),
        )

    _apply_explicit_capabilities(capabilities, metadata.get("capabilities"))
    return _build_artifact(
        runtime=runtime,
        model=model,
        base_url=base_url,
        metadata=metadata,
        hardware=_hardware(metadata, hardware_path),
        capabilities=capabilities,
        source="live",
        runtime_version=runtime_version or metadata.get("runtime_version"),
        quantization=quantization,
        notes=[
            "Optional semantic probes are not inferred from a successful HTTP response.",
            "Run a benchmark with the #126 artifact schema before making "
            "parallel-performance claims.",
        ],
    )


def _build_artifact(
    *,
    runtime: str,
    model: str,
    base_url: str,
    metadata: Mapping[str, Any],
    hardware: Mapping[str, Any],
    capabilities: Mapping[str, Mapping[str, str]],
    source: str,
    runtime_version: Any,
    quantization: Optional[str],
    notes: Sequence[str],
) -> Dict[str, Any]:
    version = runtime_version if isinstance(runtime_version, str) and runtime_version else "unknown"
    required_capabilities = ("health_models", "non_stream", "stream")
    required_passed = all(
        capabilities[name]["status"] == "supported" for name in required_capabilities
    )
    real_runtime = source == "live" and required_passed
    return {
        "artifact_type": "runtime_conformance",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "runtime": {
            "name": runtime,
            "family": SUPPORTED_RUNTIMES[runtime]["family"],
            "backend": SUPPORTED_RUNTIMES[runtime]["backend"],
            "version": _sanitize_text(version),
        },
        "model": _model_metadata(metadata, model, quantization),
        "endpoint": sanitize_url(base_url),
        "launch": _launch_metadata(metadata),
        "hardware": _sanitize_value(hardware),
        "verification": {
            "source": source,
            "real_runtime": real_runtime,
            "status": "verified" if real_runtime else "unverified",
            "required_checks_passed": required_passed,
            "required_capabilities": list(required_capabilities),
        },
        "capabilities": {name: dict(capabilities[name]) for name in CAPABILITIES},
        "probe_contract": {
            "non_stream_request_fields": ["model", "messages", "stream"],
            "stream_request_fields": ["model", "messages", "stream"],
            "optional_field_mapping": {
                "seed": "options.seed" if runtime == "ollama" else "seed",
                "tools": "tools",
                "structured_output": "format" if runtime == "ollama" else "response_format",
            },
            "optional_capabilities_checked_as_unknown": ["seed", "tools", "structured_output"],
            "operational_observations": [
                "max_concurrency",
                "queue_behavior",
                "warm_cold",
                "prefix_cache_visibility",
                "timeout_error_shape",
            ],
        },
        "sanitization": {
            "secrets_redacted": True,
            "local_paths_redacted": True,
            "remote_hosts_redacted": True,
            "raw_prompts_and_completions_omitted": True,
        },
        "notes": [_sanitize_text(note) for note in notes],
    }


def run_manifest(
    manifest_path: Path,
    *,
    fixture_path: Optional[Path] = None,
    hardware_path: Optional[Path] = None,
    quantization: Optional[str] = None,
    runtime_version: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    manifest = _load_json(manifest_path, "manifest")
    metadata = _metadata(manifest)
    if fixture_path is not None:
        fixture = _load_json(fixture_path, "fixture")
        return _fixture_artifact(
            manifest,
            metadata,
            fixture,
            hardware_path=hardware_path,
            quantization=quantization,
            runtime_version=runtime_version,
        )
    return _live_artifact(
        manifest,
        metadata,
        hardware_path=hardware_path,
        quantization=quantization,
        runtime_version=runtime_version,
        timeout=timeout,
    )


def _bundle(artifacts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "artifact_type": "runtime_conformance_bundle",
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "runtimes": list(artifacts),
        "notes": [
            "A bundle is a matrix container; each runtime entry retains its own "
            "verification status."
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        required=True,
        action="append",
        type=Path,
        help="runtime manifest; repeat to produce one bundle for multiple runtimes",
    )
    parser.add_argument(
        "--fixture", type=Path, help="offline JSON fixture; never contacts a runtime"
    )
    parser.add_argument("--hardware-json", type=Path, help="sanitized manual hardware metadata")
    parser.add_argument("--quantization", help="override quantization metadata")
    parser.add_argument("--runtime-version", help="override recorded runtime/server version")
    parser.add_argument("--timeout", type=float, default=30.0, help="live probe timeout in seconds")
    parser.add_argument("--output", required=True, type=Path, help="artifact JSON output path")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.fixture is not None and len(args.manifest) != 1:
        parser.error("--fixture can be used only with one --manifest")
    try:
        artifacts = [
            run_manifest(
                manifest,
                fixture_path=args.fixture,
                hardware_path=args.hardware_json,
                quantization=args.quantization,
                runtime_version=args.runtime_version,
                timeout=args.timeout,
            )
            for manifest in args.manifest
        ]
        payload = artifacts[0] if len(artifacts) == 1 else _bundle(artifacts)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (ConformanceError, OSError) as exc:
        print("runtime conformance failed: {}".format(_sanitize_text(exc)), file=sys.stderr)
        return 2
    if payload["artifact_type"] == "runtime_conformance_bundle":
        print("wrote {} runtime conformance artifacts to {}".format(len(artifacts), args.output))
    else:
        print("wrote runtime conformance artifact to {}".format(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
