"""LLM layer: OpenAI-compatible client + model routing."""
from .client import ChatClient, ChatResponse, LLMConfig, LLMError, ToolCall
from .router import DEFAULT_BASE_URL, DEFAULT_MODEL, FALLBACK_MODELS, ModelRouter, RouteAttempt

__all__ = ["ChatClient", "ChatResponse", "LLMConfig", "LLMError", "ToolCall",
           "DEFAULT_BASE_URL", "DEFAULT_MODEL", "FALLBACK_MODELS", "ModelRouter", "RouteAttempt"]
