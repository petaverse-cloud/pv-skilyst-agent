"""OpenAI-compatible chat client (chat completions + tool calling + SSE streaming).

No SDK dependency on purpose: the runtime ships as a single self-contained
process (bundled into the Tauri shell later), and the wire format is small
enough that the surface we actually use fits in one file. The transport is
injectable so the agent loop can be tested without a network.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

DEFAULT_TIMEOUT = 180


class LLMError(RuntimeError):
    """Transport/HTTP level failure (retryable or not is decided by the router)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = DEFAULT_TIMEOUT
    temperature: float = 0.0
    max_tokens: int | None = None
    fallbacks: tuple[str, ...] = ()

    @property
    def chat_url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = "{}"

    def parsed_arguments(self) -> dict:
        try:
            value = json.loads(self.arguments or "{}")
        except json.JSONDecodeError as exc:
            raise LLMError(f"tool call {self.name}: arguments are not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise LLMError(f"tool call {self.name}: arguments must be a JSON object")
        return value


@dataclass
class ChatResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _headers(api_key: str) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _post_json(url: str, body: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise LLMError(f"LLM HTTP {exc.code}: {detail}", status=exc.code,
                       retryable=exc.code in (408, 409, 429) or exc.code >= 500) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM endpoint unreachable: {exc.reason}", retryable=True) from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM returned a non-JSON body: {exc}") from exc


def _post_stream(url: str, body: dict, headers: dict, timeout: int) -> Iterator[str]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise LLMError(f"LLM HTTP {exc.code}: {detail}", status=exc.code,
                       retryable=exc.code in (408, 409, 429) or exc.code >= 500) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM endpoint unreachable: {exc.reason}", retryable=True) from exc
    with resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                yield line


class ChatClient:
    """Minimal chat-completions client."""

    def __init__(self, cfg: LLMConfig, post: Callable[..., dict] | None = None,
                 post_stream: Callable[..., Iterable[str]] | None = None):
        self.cfg = cfg
        self._post = post or _post_json
        self._post_stream = post_stream or _post_stream
        self.last_usage: dict = {}
        self.calls = 0

    def _body(self, messages: list[dict], tools: list[dict] | None, stream: bool) -> dict:
        body: dict = {"model": self.cfg.model, "messages": messages, "temperature": self.cfg.temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if self.cfg.max_tokens:
            body["max_tokens"] = self.cfg.max_tokens
        if stream:
            body["stream"] = True
        return body

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        self.calls += 1
        payload = self._post(self.cfg.chat_url, self._body(messages, tools, False),
                             _headers(self.cfg.api_key), self.cfg.timeout)
        return self._parse(payload)

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None,
                    on_delta: Callable[[str], None] | None = None) -> ChatResponse:
        """Streaming variant; returns the same shape as ``chat``."""
        self.calls += 1
        body = self._body(messages, tools, True)
        body["stream_options"] = {"include_usage": True}
        content_parts: list[str] = []
        calls: dict[int, dict] = {}
        finish_reason = ""
        usage: dict = {}
        model = self.cfg.model
        for line in self._post_stream(self.cfg.chat_url, body, _headers(self.cfg.api_key), self.cfg.timeout):
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                continue                      # keep-alive comments / partial frames
            model = event.get("model") or model
            usage = event.get("usage") or usage
            for choice in event.get("choices") or []:
                finish_reason = choice.get("finish_reason") or finish_reason
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    content_parts.append(text)
                    if on_delta:
                        on_delta(text)
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
        self.last_usage = usage
        return ChatResponse(content="".join(content_parts),
                            tool_calls=[ToolCall(id=s["id"] or f"call_{i}", name=s["name"],
                                                 arguments=s["arguments"] or "{}")
                                        for i, s in sorted(calls.items())],
                            finish_reason=finish_reason, model=model, usage=usage)

    @staticmethod
    def _parse(payload: dict) -> ChatResponse:
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError(f"LLM response carries no choices: {json.dumps(payload)[:300]}")
        message = choices[0].get("message") or {}
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=[ToolCall(id=t.get("id") or f"call_{i}",
                                 name=(t.get("function") or {}).get("name", ""),
                                 arguments=(t.get("function") or {}).get("arguments") or "{}")
                        for i, t in enumerate(message.get("tool_calls") or [])],
            finish_reason=choices[0].get("finish_reason") or "",
            model=payload.get("model", ""), usage=payload.get("usage") or {}, raw=payload)
