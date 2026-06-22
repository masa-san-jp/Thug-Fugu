"""Local LLM orchestration bridge inspired by Thug AI Fugu."""

from .config import (
    ConfigError,
    ModelConfig,
    OrchestratorConfig,
    RoleConfig,
    load_config,
)
from .orchestrator import FuguLocalOrchestrator, OrchestrationError, OrchestrationResult

__all__ = [
    "ConfigError",
    "FuguLocalOrchestrator",
    "ModelConfig",
    "OrchestrationError",
    "OrchestrationResult",
    "OrchestratorConfig",
    "RoleConfig",
    "load_config",
]

__version__ = "0.1.0"
