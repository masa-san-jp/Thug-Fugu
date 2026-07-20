"""Configuration loading and validation for local LLM orchestration."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

SUPPORTED_BACKENDS = {"ollama", "openai-compatible", "echo"}
SUPPORTED_SELECTION_POLICIES = {"all", "keyword"}
SUPPORTED_PATTERNS = {"direct", "role_split", "parallel_ensemble"}
SUPPORTED_ENSEMBLE_VOTES = {"synth", "majority"}
SUPPORTED_TOOL_CALLING_MODES = {"disabled", "synthesizer_only"}
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SUPPORTED_POOL_POLICIES = {"round_robin", "least_busy"}
HTTP_BACKENDS = {"ollama", "openai-compatible"}


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
class HealthCheckConfig:
    """Active health-probe settings for a model pool. Disabled by default."""

    enabled: bool = False
    interval_seconds: float = 30.0
    timeout_seconds: float = 2.0
    failure_threshold: int = 2
    success_threshold: int = 1
    require_model: bool = False


@dataclass(frozen=True)
class ModelPoolConfig:
    name: str
    backend: str
    model: str
    endpoints: List[str]
    policy: str = "round_robin"
    api_key: Optional[str] = None
    timeout_seconds: float = 120.0
    cooldown_seconds: float = 0.0
    health: HealthCheckConfig = field(default_factory=HealthCheckConfig)


@dataclass(frozen=True)
class RoleConfig:
    name: str
    model: str
    system_prompt: str = ""
    keywords: List[str] = field(default_factory=list)
    always_include: bool = False
    is_synthesizer: bool = False
    is_verifier: bool = False


@dataclass(frozen=True)
class OrchestratorConfig:
    selection_policy: str = "all"
    max_parallel_workers: int = 4
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    request_timeout_seconds: Optional[float] = None


@dataclass(frozen=True)
class CoordinatorRule:
    """A simple substring rule mapping matched task text to a processing pattern."""

    match: List[str]
    pattern: str


@dataclass(frozen=True)
class EnsembleConfig:
    n: int = 3
    vote: str = "synth"


@dataclass(frozen=True)
class VerifyConfig:
    enabled: bool = False
    max_retries: int = 1
    role: Optional[str] = None


@dataclass(frozen=True)
class CoordinatorConfig:
    enabled: bool = False
    meta_model: Optional[str] = None
    default_pattern: str = "role_split"
    rules: List[CoordinatorRule] = field(default_factory=list)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)


@dataclass(frozen=True)
class ToolCallingConfig:
    enabled: bool = False
    mode: str = "disabled"
    execute: bool = False
    allowed_tools: List[str] = field(default_factory=list)
    timeout_seconds: float = 5.0
    max_output_chars: int = 4000


@dataclass(frozen=True)
class RequestQueueConfig:
    enabled: bool = False
    max_size: int = 16
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ServerConfig:
    queue: RequestQueueConfig = field(default_factory=RequestQueueConfig)


@dataclass(frozen=True)
class FuguLocalConfig:
    models: List[ModelConfig]
    roles: List[RoleConfig]
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    coordinator: CoordinatorConfig = field(default_factory=CoordinatorConfig)
    tool_calling: ToolCallingConfig = field(default_factory=ToolCallingConfig)
    model_pools: List[ModelPoolConfig] = field(default_factory=list)
    server: ServerConfig = field(default_factory=ServerConfig)

    def model_by_name(self) -> Dict[str, ModelConfig]:
        return {model.name: model for model in self.models}

    def pool_by_name(self) -> Dict[str, ModelPoolConfig]:
        return {pool.name: pool for pool in self.model_pools}

    def target_names(self) -> set:
        return {model.name for model in self.models} | {pool.name for pool in self.model_pools}


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
    coordinator = _coordinator_from_dict(raw.get("coordinator", {}))
    tool_calling = _tool_calling_from_dict(raw.get("tool_calling", {}))
    model_pools = [_model_pool_from_dict(item) for item in _optional_list(raw, "model_pools")]
    server = _server_from_dict(raw.get("server", {}))
    config = FuguLocalConfig(
        models=models,
        roles=roles,
        orchestrator=orchestrator,
        coordinator=coordinator,
        tool_calling=tool_calling,
        model_pools=model_pools,
        server=server,
    )
    validate_config(config)
    return config


def validate_config(config: FuguLocalConfig) -> None:
    if not config.models:
        raise ConfigError("At least one model is required")
    if not config.roles:
        raise ConfigError("At least one role is required")

    model_names = _ensure_unique([model.name for model in config.models], "model")
    pool_names = _ensure_unique([pool.name for pool in config.model_pools], "model pool")
    overlap = model_names & pool_names
    if overlap:
        raise ConfigError(
            f"model and model_pool names must be unique; duplicates: {sorted(overlap)}"
        )
    target_names = model_names | pool_names
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
        if role.model not in target_names:
            raise ConfigError(f"Role '{role.name}' references unknown model or pool '{role.model}'")

    _validate_model_pools(config)

    if config.orchestrator.selection_policy not in SUPPORTED_SELECTION_POLICIES:
        raise ConfigError(
            f"Unsupported selection_policy '{config.orchestrator.selection_policy}'. "
            f"Supported: {sorted(SUPPORTED_SELECTION_POLICIES)}"
        )
    if config.orchestrator.max_parallel_workers <= 0:
        raise ConfigError("max_parallel_workers must be positive")
    if config.orchestrator.max_tokens is not None and config.orchestrator.max_tokens <= 0:
        raise ConfigError("max_tokens must be positive when provided")
    if (
        config.orchestrator.request_timeout_seconds is not None
        and config.orchestrator.request_timeout_seconds <= 0
    ):
        raise ConfigError("request_timeout_seconds must be positive when provided")

    _validate_coordinator(config, model_names)
    _validate_tool_calling(config.tool_calling)
    _validate_server(config.server)


def _model_from_dict(raw: Any) -> ModelConfig:
    obj = _required_object(raw, "model entry")
    return ModelConfig(
        name=_required_str(obj, "name"),
        backend=_required_str(obj, "backend"),
        model=_required_str(obj, "model"),
        base_url=_optional_str(obj, "base_url"),
        api_key=_expand_optional_env(_optional_str(obj, "api_key")),
        timeout_seconds=_optional_number(obj, "timeout_seconds", default=120.0),
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
        system_prompt=_optional_str(obj, "system_prompt") or "",
        keywords=list(keywords_raw),
        always_include=_optional_bool(obj, "always_include", default=False),
        is_synthesizer=_optional_bool(obj, "is_synthesizer", default=False),
        is_verifier=_optional_bool(obj, "is_verifier", default=False),
    )


def _orchestrator_from_dict(raw: Any) -> OrchestratorConfig:
    if raw is None:
        raw = {}
    obj = _required_object(raw, "orchestrator")
    return OrchestratorConfig(
        selection_policy=_optional_str(obj, "selection_policy") or "all",
        max_parallel_workers=_optional_int(obj, "max_parallel_workers", default=4),
        temperature=_optional_number(obj, "temperature", default=0.2),
        max_tokens=_optional_int(obj, "max_tokens", default=None),
        request_timeout_seconds=_optional_positive_number(obj, "request_timeout_seconds"),
    )


def _coordinator_from_dict(raw: Any) -> CoordinatorConfig:
    if raw is None:
        raw = {}
    obj = _required_object(raw, "coordinator")
    rules = [_coordinator_rule_from_dict(item) for item in _optional_list(obj, "rules")]
    ensemble = _ensemble_from_dict(obj.get("ensemble", {}))
    verify = _verify_from_dict(obj.get("verify", {}))
    return CoordinatorConfig(
        enabled=_optional_bool(obj, "enabled", default=False),
        meta_model=_optional_str(obj, "meta_model"),
        default_pattern=_optional_str(obj, "default_pattern") or "role_split",
        rules=rules,
        ensemble=ensemble,
        verify=verify,
    )


def _coordinator_rule_from_dict(raw: Any) -> CoordinatorRule:
    obj = _required_object(raw, "coordinator rule")
    match_raw = obj.get("match")
    if isinstance(match_raw, str):
        match = [match_raw]
    elif isinstance(match_raw, list) and all(isinstance(item, str) for item in match_raw):
        match = list(match_raw)
    else:
        raise ConfigError("coordinator rule 'match' must be a string or list of strings")
    if not match:
        raise ConfigError("coordinator rule 'match' must not be empty")
    return CoordinatorRule(match=match, pattern=_required_str(obj, "pattern"))


def _ensemble_from_dict(raw: Any) -> EnsembleConfig:
    if raw is None:
        raw = {}
    obj = _required_object(raw, "coordinator.ensemble")
    n = _optional_int(obj, "n", default=3)
    return EnsembleConfig(
        n=n if n is not None else 3,
        vote=_optional_str(obj, "vote") or "synth",
    )


def _verify_from_dict(raw: Any) -> VerifyConfig:
    if raw is None:
        raw = {}
    obj = _required_object(raw, "coordinator.verify")
    max_retries = _optional_int(obj, "max_retries", default=1)
    return VerifyConfig(
        enabled=_optional_bool(obj, "enabled", default=False),
        max_retries=max_retries if max_retries is not None else 1,
        role=_optional_str(obj, "role"),
    )


def _validate_coordinator(config: FuguLocalConfig, model_names: set) -> None:
    coordinator = config.coordinator
    if coordinator.default_pattern not in SUPPORTED_PATTERNS:
        raise ConfigError(
            f"Unsupported coordinator.default_pattern '{coordinator.default_pattern}'. "
            f"Supported: {sorted(SUPPORTED_PATTERNS)}"
        )
    if coordinator.meta_model is not None and coordinator.meta_model not in model_names:
        raise ConfigError(
            f"coordinator.meta_model references unknown model '{coordinator.meta_model}'"
        )
    for rule in coordinator.rules:
        if rule.pattern not in SUPPORTED_PATTERNS:
            raise ConfigError(
                f"Unsupported pattern '{rule.pattern}' in coordinator rule. "
                f"Supported: {sorted(SUPPORTED_PATTERNS)}"
            )
    if coordinator.ensemble.n <= 0:
        raise ConfigError("coordinator.ensemble.n must be positive")
    if coordinator.ensemble.vote not in SUPPORTED_ENSEMBLE_VOTES:
        raise ConfigError(
            f"Unsupported coordinator.ensemble.vote '{coordinator.ensemble.vote}'. "
            f"Supported: {sorted(SUPPORTED_ENSEMBLE_VOTES)}"
        )
    if coordinator.verify.max_retries < 0:
        raise ConfigError("coordinator.verify.max_retries must be non-negative")

    role_names = {role.name for role in config.roles}
    flagged_verifiers = [role for role in config.roles if role.is_verifier]
    if coordinator.verify.role is not None and coordinator.verify.role not in role_names:
        raise ConfigError(
            f"coordinator.verify.role references unknown role '{coordinator.verify.role}'"
        )
    if coordinator.verify.enabled and coordinator.verify.role is None and not flagged_verifiers:
        raise ConfigError(
            "coordinator.verify.enabled=true requires coordinator.verify.role "
            "or a roles[] entry with is_verifier=true"
        )


def _tool_calling_from_dict(raw: Any) -> ToolCallingConfig:
    if raw is None:
        raw = {}
    obj = _required_object(raw, "tool_calling")
    allowed_tools_raw = obj.get("allowed_tools", [])
    if not isinstance(allowed_tools_raw, list) or not all(
        isinstance(item, str) for item in allowed_tools_raw
    ):
        raise ConfigError("tool_calling.allowed_tools must be a list of strings")
    return ToolCallingConfig(
        enabled=_optional_bool(obj, "enabled", default=False),
        mode=_optional_str(obj, "mode") or "disabled",
        execute=_optional_bool(obj, "execute", default=False),
        allowed_tools=list(allowed_tools_raw),
        timeout_seconds=_optional_number(obj, "timeout_seconds", default=5.0),
        max_output_chars=_optional_int(obj, "max_output_chars", default=4000),
    )


def _validate_tool_calling(config: ToolCallingConfig) -> None:
    if config.mode not in SUPPORTED_TOOL_CALLING_MODES:
        raise ConfigError(
            f"Unsupported tool_calling.mode '{config.mode}'. "
            f"Supported: {sorted(SUPPORTED_TOOL_CALLING_MODES)}"
        )
    if not config.enabled and config.mode != "disabled":
        raise ConfigError("tool_calling.mode must be 'disabled' when tool_calling.enabled=false")
    if config.enabled and config.mode == "disabled":
        raise ConfigError("tool_calling.mode must not be 'disabled' when tool_calling.enabled=true")
    if config.execute and not config.allowed_tools:
        raise ConfigError("tool_calling.allowed_tools must not be empty when execute=true")
    if config.timeout_seconds <= 0:
        raise ConfigError("tool_calling.timeout_seconds must be positive")
    if config.max_output_chars <= 0:
        raise ConfigError("tool_calling.max_output_chars must be positive")
    for tool in config.allowed_tools:
        if not TOOL_NAME_PATTERN.match(tool):
            raise ConfigError(
                "tool_calling.allowed_tools entries must match ^[A-Za-z0-9_-]{1,64}$; "
                f"invalid entry: {tool!r}"
            )


def _server_from_dict(raw: Any) -> ServerConfig:
    obj = _required_object(raw, "server")
    queue_obj = _required_object(obj.get("queue", {}), "server.queue")
    max_size = _optional_int(queue_obj, "max_size", default=16)
    assert max_size is not None
    return ServerConfig(
        queue=RequestQueueConfig(
            enabled=_optional_bool(queue_obj, "enabled", default=False),
            max_size=max_size,
            timeout_seconds=_optional_number(queue_obj, "timeout_seconds", default=30.0),
        )
    )


def _validate_server(config: ServerConfig) -> None:
    if config.queue.max_size <= 0:
        raise ConfigError("server.queue.max_size must be positive")
    if config.queue.timeout_seconds <= 0:
        raise ConfigError("server.queue.timeout_seconds must be positive")


def _health_check_from_dict(raw: Any) -> HealthCheckConfig:
    obj = _required_object(raw, "model_pool.health")
    failure_threshold = _optional_int(obj, "failure_threshold", default=2)
    success_threshold = _optional_int(obj, "success_threshold", default=1)
    assert failure_threshold is not None
    assert success_threshold is not None
    return HealthCheckConfig(
        enabled=_optional_bool(obj, "enabled", default=False),
        interval_seconds=_optional_number(obj, "interval_seconds", default=30.0),
        timeout_seconds=_optional_number(obj, "timeout_seconds", default=2.0),
        failure_threshold=failure_threshold,
        success_threshold=success_threshold,
        require_model=_optional_bool(obj, "require_model", default=False),
    )


def _model_pool_from_dict(raw: Any) -> ModelPoolConfig:
    obj = _required_object(raw, "model_pool entry")
    endpoints_raw = obj.get("endpoints")
    if not isinstance(endpoints_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in endpoints_raw
    ):
        raise ConfigError("model_pool.endpoints must be a non-empty list of URL strings")
    return ModelPoolConfig(
        name=_required_str(obj, "name"),
        backend=_required_str(obj, "backend"),
        model=_required_str(obj, "model"),
        endpoints=list(endpoints_raw),
        policy=_optional_str(obj, "policy") or "round_robin",
        api_key=_expand_optional_env(_optional_str(obj, "api_key")),
        timeout_seconds=_optional_number(obj, "timeout_seconds", default=120.0),
        cooldown_seconds=_optional_number(obj, "cooldown_seconds", default=0.0),
        health=_health_check_from_dict(obj.get("health", {})),
    )


def _validate_model_pools(config: FuguLocalConfig) -> None:
    for pool in config.model_pools:
        if pool.backend not in HTTP_BACKENDS:
            raise ConfigError(
                f"Unsupported backend '{pool.backend}' for model_pool '{pool.name}'. "
                f"Supported: {sorted(HTTP_BACKENDS)}"
            )
        if not pool.endpoints:
            raise ConfigError(f"model_pool '{pool.name}' must have at least one endpoint")
        if pool.policy not in SUPPORTED_POOL_POLICIES:
            raise ConfigError(
                f"Unsupported policy '{pool.policy}' for model_pool '{pool.name}'. "
                f"Supported: {sorted(SUPPORTED_POOL_POLICIES)}"
            )
        if pool.timeout_seconds <= 0:
            raise ConfigError(f"timeout_seconds must be positive for model_pool '{pool.name}'")
        if pool.cooldown_seconds < 0:
            raise ConfigError(f"cooldown_seconds must be non-negative for model_pool '{pool.name}'")
        if pool.health.interval_seconds <= 0:
            raise ConfigError(
                f"health.interval_seconds must be positive for model_pool '{pool.name}'"
            )
        if pool.health.timeout_seconds <= 0:
            raise ConfigError(
                f"health.timeout_seconds must be positive for model_pool '{pool.name}'"
            )
        if pool.health.failure_threshold <= 0:
            raise ConfigError(
                f"health.failure_threshold must be positive for model_pool '{pool.name}'"
            )
        if pool.health.success_threshold <= 0:
            raise ConfigError(
                f"health.success_threshold must be positive for model_pool '{pool.name}'"
            )


def _optional_list(raw: Mapping[str, Any], key: str) -> List[Any]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"'{key}' must be a list when provided")
    return value


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


def _optional_bool(raw: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"'{key}' must be a boolean when provided")
    return value


def _optional_int(raw: Mapping[str, Any], key: str, *, default: Optional[int]) -> Optional[int]:
    value = raw.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"'{key}' must be an integer when provided")
    return value


def _optional_number(raw: Mapping[str, Any], key: str, *, default: float) -> float:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{key}' must be a number when provided")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"'{key}' must be finite when provided")
    return number


def _optional_positive_number(raw: Mapping[str, Any], key: str) -> Optional[float]:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{key}' must be a number when provided")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"'{key}' must be finite when provided")
    return number


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
