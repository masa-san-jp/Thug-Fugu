"""Local LLM orchestration bridge inspired by Thug AI Fugu."""

from .config import (
    ConfigError,
    CoordinatorConfig,
    ModelConfig,
    ModelPoolConfig,
    OrchestratorConfig,
    RoleConfig,
    ToolCallingConfig,
    load_config,
)
from .consult import consult
from .coordinator import Coordinator, Plan
from .orchestrator import FuguLocalOrchestrator, OrchestrationError, OrchestrationResult
from .routing import ModelRouter, RouterMember
from .serverplan import ServerEndpoint, derive_server_plan, render_ollama_commands

__all__ = [
    "ConfigError",
    "consult",
    "Coordinator",
    "CoordinatorConfig",
    "FuguLocalOrchestrator",
    "ModelConfig",
    "ModelPoolConfig",
    "ModelRouter",
    "OrchestrationError",
    "OrchestrationResult",
    "OrchestratorConfig",
    "Plan",
    "RoleConfig",
    "RouterMember",
    "ToolCallingConfig",
    "ServerEndpoint",
    "derive_server_plan",
    "load_config",
    "render_ollama_commands",
]

__version__ = "0.1.0"
