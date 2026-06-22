"""Configuration loading and validation for local LLM orchestration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


SUPPORTED_BACKENDS = {"ollama", "openai-compatible", "echo"}
SUPPORTED_SELECTION_POLICIES = {"all", "keyword"}


class ConfigError(ValueError):
    """Raised when a configuration file is invalid."""


@dataclass(frozen=True)
class ModelConfig:
    name: str
    backend: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_seconds: float = 120.0


@dataclass(frozen=True)
class RoleConfig:
    name: str
    model: str
    system_prompt: str = ""
    keywords: List[str] = field(default_factory=list)
    always_include: bool = False
    is_synthesizer: bool = False


@dataclass(frozen=True)
class OrchestratorConfig:
    selection_policy: str = "all"
    max_parallel_workers: int = 4
    temperature: float = 0.2
    max_tokens: Optional[int] = None


@dataclass(frozen=True)
class FuguLocalConfig:
    models: List[ModelConfig]
    roles: List[RoleConfig]
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

    def model_by_name(self) -> Dict[str, ModelConfig]:
        return {model.name: model for model in self.models}


def load_config(path: str) -> FuguLocalConfig:
    """Load and validate a JSON configuration file."""

    config_path = Path(path)
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config file {path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ConfigError("Top-level config must be a JSON object")
    return config_from_dict(raw)


def config_from_dict(raw: Mapping[str, Any]) -> FuguLocalConfig:
    models = [_model_from_dict(item) for item in _required_list(raw, "models")]
    roles = [_role_from_dict(item) for item in _required_list(raw, "roles")]
    orchestrator = _orchestrator_from_dict(raw.get("orchestrator", {}))
    config = FuguLocalConfig(models=models, roles=roles, orchestrator=orchestrator)
    validate_config(config)
    return config


def validate_config(config: FuguLocalConfig) -> None:
    if not config.models:
        raise ConfigError("At least one model is required")
    if not config.roles:
        raise ConfigError("At least one role is required")

    model_names = _ensure_unique([model.name for model in config.models], "model")
    _ensure_unique([role.name for role in config.roles], "role")

    for model in config.models:
        if model.backend not in SUPPORTED_BACKENDS:
            raise ConfigError(
                f"Unsupported backend '{model.backend}' for model '{model.name}'. "
                f"Supported: {sorted(SUPPORTED_BACKENDS)}"
            )
        if model.timeout_seconds <= 0:
            raise ConfigError(f"timeout_seconds must be positive for model '{model.name}'")
        if model.backend in {"ollama", "openai-compatible"} and not model.base_url:
            raise ConfigError(f"base_url is required for backend '{model.backend}'")

    for role in config.roles:
        if role.model not in model_names:
            raise ConfigError(f"Role '{role.name}' references unknown model '{role.model}'")

    if config.orchestrator.selection_policy not in SUPPORTED_SELECTION_POLICIES:
        raise ConfigError(
            f"Unsupported selection_policy '{config.orchestrator.selection_policy}'. "
            f"Supported: {sorted(SUPPORTED_SELECTION_POLICIES)}"
        )
    if config.orchestrator.max_parallel_workers <= 0:
        raise ConfigError("max_parallel_workers must be positive")
    if config.orchestrator.max_tokens is not None and config.orchestrator.max_tokens <= 0:
        raise ConfigError("max_tokens must be positive when provided")


def _model_from_dict(raw: Any) -> ModelConfig:
    obj = _required_object(raw, "model entry")
    return ModelConfig(
        name=_required_str(obj, "name"),
        backend=_required_str(obj, "backend"),
        model=_required_str(obj, "model"),
        base_url=_optional_str(obj, "base_url"),
        api_key=_expand_optional_env(_optional_str(obj, "api_key")),
        timeout_seconds=float(obj.get("timeout_seconds", 120.0)),
    )


def _role_from_dict(raw: Any) -> RoleConfig:
    obj = _required_object(raw, "role entry")
    keywords_raw = obj.get("keywords", [])
    if not isinstance(keywords_raw, list) or not all(
        isinstance(keyword, str) for keyword in keywords_raw
    ):
        raise ConfigError("role.keywords must be a list of strings")
    return RoleConfig(
        name=_required_str(obj, "name"),
        model=_required_str(obj, "model"),
        system_prompt=str(obj.get("system_prompt", "")),
        keywords=list(keywords_raw),
        always_include=bool(obj.get("always_include", False)),
        is_synthesizer=bool(obj.get("is_synthesizer", False)),
    )


def _orchestrator_from_dict(raw: Any) -> OrchestratorConfig:
    if raw is None:
        raw = {}
    obj = _required_object(raw, "orchestrator")
    max_tokens = obj.get("max_tokens")
    return OrchestratorConfig(
        selection_policy=str(obj.get("selection_policy", "all")),
        max_parallel_workers=int(obj.get("max_parallel_workers", 4)),
        temperature=float(obj.get("temperature", 0.2)),
        max_tokens=int(max_tokens) if max_tokens is not None else None,
    )


def _required_list(raw: Mapping[str, Any], key: str) -> List[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ConfigError(f"'{key}' must be a list")
    return value


def _required_object(raw: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{label} must be an object")
    return raw


def _required_str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' is required and must be a non-empty string")
    return value


def _optional_str(raw: Mapping[str, Any], key: str) -> Optional[str]:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"'{key}' must be a string when provided")
    return value


def _expand_optional_env(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return os.path.expandvars(value)


def _ensure_unique(values: Iterable[str], label: str) -> set:
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ConfigError(f"Duplicate {label} name(s): {sorted(duplicates)}")
    return seen

