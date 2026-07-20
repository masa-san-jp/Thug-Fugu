"""Backend adapters for local LLM servers."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Protocol

from .config import ModelConfig


class BackendError(RuntimeError):
    """Raised when a backend call fails."""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: List[ChatMessage]
    temperature: float = 0.2
    max_tokens: Optional[int] = None


@dataclass(frozen=True)
class ChatResponse:
    content: str
    raw: Optional[Mapping] = None
    usage: Optional["TokenUsage"] = None


@dataclass(frozen=True)
class TokenUsage:
    """Token counts reported by a backend. Fields are None when not reported."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    @property
    def known(self) -> bool:
        return (
            self.prompt_tokens is not None
            or self.completion_tokens is not None
            or self.total_tokens is not None
        )


def _coerce_token_count(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def _usage_from_openai(response: Mapping) -> Optional[TokenUsage]:
    usage = response.get("usage") if isinstance(response, Mapping) else None
    if not isinstance(usage, Mapping):
        return None
    prompt = _coerce_token_count(usage.get("prompt_tokens"))
    completion = _coerce_token_count(usage.get("completion_tokens"))
    total = _coerce_token_count(usage.get("total_tokens"))
    parsed = TokenUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
    return parsed if parsed.known else None


def _usage_from_ollama(response: Mapping) -> Optional[TokenUsage]:
    if not isinstance(response, Mapping):
        return None
    prompt = _coerce_token_count(response.get("prompt_eval_count"))
    completion = _coerce_token_count(response.get("eval_count"))
    if prompt is None and completion is None:
        return None
    total = (prompt or 0) + (completion or 0)
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


class LLMBackend(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse: ...


def probe_ollama(
    base_url: str,
    *,
    timeout_seconds: float,
    model: Optional[str] = None,
    require_model: bool = False,
) -> bool:
    """Return whether an Ollama endpoint responds successfully to ``/api/tags``."""

    url = f"{base_url.rstrip('/')}/api/tags"
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if not 200 <= response.status < 300:
                return False
            if not require_model:
                return True
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError):
        return False
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False

    models = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(models, list) or model is None:
        return False
    return any(
        isinstance(item, Mapping) and model in {item.get("name"), item.get("model")}
        for item in models
    )


def probe_openai_compatible(
    base_url: str,
    *,
    timeout_seconds: float,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    require_model: bool = False,
) -> bool:
    """Return whether an OpenAI-compatible endpoint responds to ``/v1/models``."""

    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {"accept": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if not 200 <= response.status < 300:
                return False
            if not require_model:
                return True
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError):
        return False
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False

    models = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(models, list) or model is None:
        return False
    return any(isinstance(item, Mapping) and item.get("id") == model for item in models)


def build_backend(config: ModelConfig) -> LLMBackend:
    if config.backend == "ollama":
        return OllamaBackend(config)
    if config.backend == "openai-compatible":
        return OpenAICompatibleBackend(config)
    if config.backend == "echo":
        return EchoBackend(config)
    raise BackendError(f"Unsupported backend: {config.backend}")


class OpenAICompatibleBackend:
    """Adapter for LM Studio, llama.cpp server, vLLM, and similar local servers."""

    def __init__(self, config: ModelConfig):
        self.config = config

    def chat(self, request: ChatRequest) -> ChatResponse:
        base_url = (self.config.base_url or "").rstrip("/")
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in request.messages],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        response = _post_json(
            f"{base_url}/v1/chat/completions",
            payload,
            timeout=self.config.timeout_seconds,
            api_key=self.config.api_key,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BackendError("OpenAI-compatible backend returned an unexpected response") from exc
        if not isinstance(content, str):
            raise BackendError("OpenAI-compatible backend response content is not a string")
        return ChatResponse(content=content, raw=response, usage=_usage_from_openai(response))


class OllamaBackend:
    """Adapter for Ollama's /api/chat endpoint."""

    def __init__(self, config: ModelConfig):
        self.config = config

    def chat(self, request: ChatRequest) -> ChatResponse:
        base_url = (self.config.base_url or "").rstrip("/")
        payload = {
            "model": self.config.model,
            "messages": [message.to_dict() for message in request.messages],
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        if request.max_tokens is not None:
            payload["options"]["num_predict"] = request.max_tokens

        response = _post_json(
            f"{base_url}/api/chat",
            payload,
            timeout=self.config.timeout_seconds,
            api_key=self.config.api_key,
        )
        try:
            content = response["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise BackendError("Ollama backend returned an unexpected response") from exc
        if not isinstance(content, str):
            raise BackendError("Ollama backend response content is not a string")
        return ChatResponse(content=content, raw=response, usage=_usage_from_ollama(response))


class EchoBackend:
    """Offline backend for tests, examples, and development without a real LLM."""

    def __init__(self, config: ModelConfig):
        self.config = config

    def chat(self, request: ChatRequest) -> ChatResponse:
        system = next((m.content for m in request.messages if m.role == "system"), "")
        user = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        content = (
            f"[echo:{self.config.name}/{self.config.model}]\n"
            f"system={system[:160]}\n"
            f"user={user[:1000]}"
        )
        return ChatResponse(content=content, raw={"backend": "echo"})


def _post_json(
    url: str,
    payload: Mapping,
    *,
    timeout: float,
    api_key: Optional[str] = None,
) -> Mapping:
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
    }
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    safe_url = _safe_url(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Read and discard the body so the connection can be cleaned up, but never
        # surface backend response bodies because they may contain prompts, model
        # output, request metadata, or credentials echoed by a local server.
        try:
            exc.read()
        except Exception:  # noqa: BLE001 - best-effort cleanup only.
            pass
        raise BackendError(
            f"HTTP {exc.code} from {safe_url} (backend response body redacted)"
        ) from exc
    except urllib.error.URLError as exc:
        raise BackendError(f"Could not reach {safe_url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise BackendError(f"Timed out calling {safe_url}") from exc

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BackendError(f"Non-JSON response from {safe_url} (body redacted)") from exc
    if not isinstance(decoded, Mapping):
        raise BackendError(f"JSON response from {safe_url} must be an object")
    return decoded


def _safe_url(url: str) -> str:
    """Return scheme, host, port, and path while dropping query/fragment data."""

    parsed = urllib.parse.urlsplit(url)
    path = parsed.path or "/"
    if parsed.scheme and parsed.netloc:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return path
