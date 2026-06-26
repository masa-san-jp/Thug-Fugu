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
    "load_config",
]

__version__ = "0.1.0"
