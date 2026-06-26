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


class LLMBackend(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse:
        ...


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
        return ChatResponse(content=content, raw=response)


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
        return ChatResponse(content=content, raw=response)


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

