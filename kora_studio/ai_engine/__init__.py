"""KORA STUDIO — AI Engine Manager.

Provider-agnostic layer that lets KORA use OpenCode, Codex, Ollama or any
OpenAI-compatible API through one interface, with real verification, routing
per agent, a hard zero-budget default and a single versioned KORA context.
"""

from kora_studio.ai_engine.base import (  # noqa: F401
    AIProviderAdapter,
    EngineError,
    EngineResult,
    ModelInfo,
)
from kora_studio.ai_engine.manager import AIEngineManager  # noqa: F401

__all__ = [
    "AIEngineManager",
    "AIProviderAdapter",
    "EngineError",
    "EngineResult",
    "ModelInfo",
]
