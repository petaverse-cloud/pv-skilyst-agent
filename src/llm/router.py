"""Model routing: one logical model name, a primary target plus declared fallbacks.

Default model is `deepseek/deepseek-v4.1-flash` on the OpenAI-compatible gateway
(`https://new-api.verse4.pet/v1`), with `MiMo-V2.6-Flash` as the declared
fallback (decision: M1 default model).

Routing rules kept deliberately narrow (a fallback that hides a real error is
worse than no fallback):

  * a *transient* transport failure (timeout, 429, 5xx) moves to the next target
    and is recorded on the trace;
  * a *client* error (400/401/403/404) is raised immediately -- a bad request or
    a bad key must be visible, never retried into a different model;
  * fallback never happens mid-stream after the first token was delivered
    (the caller would see interleaved text), only before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .client import ChatClient, ChatResponse, LLMConfig, LLMError

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_BASE_URL = "https://new-api.verse4.pet/v1"
FALLBACK_MODELS = ("MiMo-V2.6-Flash",)


@dataclass
class RouteAttempt:
    model: str
    ok: bool
    error: str = ""
    usage: dict = field(default_factory=dict)


class ModelRouter:
    """Dispatches chat calls across an ordered model list."""

    def __init__(self, cfg: LLMConfig, client_factory: Callable[[LLMConfig], ChatClient] | None = None):
        self.cfg = cfg
        self._factory = client_factory or (lambda c: ChatClient(c))
        self.targets = (cfg.model,) + tuple(m for m in cfg.fallbacks if m != cfg.model)
        self.trace: list[RouteAttempt] = []

    @property
    def model(self) -> str:
        return self.targets[0]

    def _client(self, model: str) -> ChatClient:
        return self._factory(LLMConfig(base_url=self.cfg.base_url, api_key=self.cfg.api_key, model=model,
                                       timeout=self.cfg.timeout, temperature=self.cfg.temperature,
                                       max_tokens=self.cfg.max_tokens))

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             on_delta: Callable[[str], None] | None = None, stream: bool = False) -> ChatResponse:
        errors: list[str] = []
        for model in self.targets:
            client = self._client(model)
            try:
                if stream:
                    emitted = {"any": False}

                    def guarded(text: str) -> None:
                        emitted["any"] = True
                        if on_delta:
                            on_delta(text)

                    response = client.chat_stream(messages, tools, guarded)
                else:
                    response = client.chat(messages, tools)
                self.trace.append(RouteAttempt(model=model, ok=True, usage=response.usage))
                response.model = response.model or model
                return response
            except LLMError as exc:
                self.trace.append(RouteAttempt(model=model, ok=False, error=str(exc)))
                errors.append(f"{model}: {exc}")
                if not exc.retryable:
                    raise
                if stream and emitted["any"]:
                    raise LLMError(f"stream failed after partial output on {model} -- not retrying into "
                                   f"another model (the caller already received text): {exc}") from exc
        raise LLMError("every model target failed:\n  " + "\n  ".join(errors))

    @property
    def total_usage(self) -> dict:
        prompt = sum(int(a.usage.get("prompt_tokens") or 0) for a in self.trace if a.ok)
        completion = sum(int(a.usage.get("completion_tokens") or 0) for a in self.trace if a.ok)
        return {"prompt_tokens": prompt, "completion_tokens": completion,
                "total_tokens": prompt + completion, "calls": len(self.trace),
                "models_used": sorted({a.model for a in self.trace if a.ok})}
