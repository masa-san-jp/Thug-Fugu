"""Backend adapters for local LLM servers."""

from __future__ import annotations

import json
import urllib.error
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise BackendError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise BackendError(f"Could not reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise BackendError(f"Timed out calling {url}") from exc

    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BackendError(f"Non-JSON response from {url}: {body[:200]}") from exc
    if not isinstance(decoded, Mapping):
        raise BackendError(f"JSON response from {url} must be an object")
    return decoded

