"""Local LLM orchestration bridge inspired by Thug AI Fugu."""

from .config import (
    ConfigError,
    CoordinatorConfig,
    ModelConfig,
    OrchestratorConfig,
    RoleConfig,
    load_config,
)
from .coordinator import Coordinator, Plan
from .orchestrator import FuguLocalOrchestrator, OrchestrationError, OrchestrationResult
from .serverplan import ServerEndpoint, derive_server_plan, render_ollama_commands

__all__ = [
    "ConfigError",
    "Coordinator",
    "CoordinatorConfig",
    "FuguLocalOrchestrator",
    "ModelConfig",
    "OrchestrationError",
    "OrchestrationResult",
    "OrchestratorConfig",
    "Plan",
    "RoleConfig",
    "ServerEndpoint",
    "derive_server_plan",
    "load_config",
    "render_ollama_commands",
]

__version__ = "0.1.0"
