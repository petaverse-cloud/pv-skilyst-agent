"""The agent loop: user turn -> model -> tool calls -> ... -> answer.

This is deliberately a *small* loop with sharp edges, because everything the
product will add later (sub-agents, approvals, cron, gateways) hangs off it:

  * the transcript lives in the session store, so a crash loses nothing already
    said and a session can be resumed with `--session`;
  * tool results go back to the model as data; tool *failures* go back as error
    text and are also recorded in the trace -- nothing is swallowed, and nothing
    is retried blindly;
  * the system prompt is rebuilt each turn, so a skill installed mid-session is
    visible to the next turn without restarting the process;
  * `stop_reason` distinguishes "the model finished" from "we ran out of turns",
    and the CLI exits non-zero on the latter rather than pretending success.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from llm import ChatResponse, LLMError, ModelRouter, ToolCall
from session import Session

from .prompt import PromptContext, build_system_prompt
from .tools import MAX_TOOL_RESULT, ToolRegistry, tool_error_message


@dataclass
class LoopConfig:
    max_turns: int = 8
    stream: bool = True
    max_tool_result_chars: int = MAX_TOOL_RESULT
    verbose: bool = True


@dataclass
class ToolExecution:
    name: str
    arguments: dict
    result: dict | None = None
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class LoopResult:
    answer: str
    stop_reason: str                     # completed | max_turns | llm_error
    turns: int = 0
    tool_calls: list[ToolExecution] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    wall_clock_s: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.stop_reason == "completed"

    def artifact_urls(self) -> list[str]:
        out = []
        for call in self.tool_calls:
            if call.result and call.result.get("artifact_url"):
                out.append(call.result["artifact_url"])
        return out


class AgentLoop:
    def __init__(self, router: ModelRouter, tools: ToolRegistry, session: Session,
                 prompt_ctx: PromptContext, config: LoopConfig | None = None,
                 on_event: Callable[[str], None] | None = None,
                 on_delta: Callable[[str], None] | None = None):
        self.router = router
        self.tools = tools
        self.session = session
        self.prompt_ctx = prompt_ctx
        self.config = config or LoopConfig()
        self.on_event = on_event or (lambda _m: None)
        self.on_delta = on_delta

    # -- one turn -----------------------------------------------------------
    def _messages(self) -> list[dict]:
        system = build_system_prompt(self.prompt_ctx)
        return [{"role": "system", "content": system}] + self.session.history()

    def _execute(self, call: ToolCall) -> ToolExecution:
        started = time.time()
        execution = ToolExecution(name=call.name, arguments={})
        try:
            execution.arguments = call.parsed_arguments()
        except LLMError as exc:
            execution.error = tool_error_message(exc)
        else:
            try:
                execution.result = self.tools.call(call.name, execution.arguments)
            except Exception as exc:                       # noqa: BLE001 -- reported, never masked
                execution.error = tool_error_message(exc)
        execution.duration_s = round(time.time() - started, 2)
        self.session.append_trace("tool_call", tool=call.name, arguments=execution.arguments,
                                  result=execution.result, error=execution.error,
                                  duration_s=execution.duration_s)
        return execution

    @staticmethod
    def _assistant_message(response: ChatResponse) -> dict:
        message: dict = {"role": "assistant", "content": response.content or ""}
        if response.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function",
                 "function": {"name": call.name, "arguments": call.arguments}}
                for call in response.tool_calls]
        return message

    # -- the loop -----------------------------------------------------------
    def run(self, user_input: str) -> LoopResult:
        started = time.time()
        self.session.append_message("user", user_input)
        result = LoopResult(answer="", stop_reason="completed")
        for turn in range(self.config.max_turns):
            result.turns = turn + 1
            try:
                response = self.router.chat(self._messages(), self.tools.schemas(),
                                            on_delta=self.on_delta, stream=self.config.stream)
            except LLMError as exc:
                result.stop_reason = "llm_error"
                result.error = str(exc)
                self.session.append_trace("llm_error", turn=turn, error=str(exc))
                self.session.update(status="error")
                break
            result.model = response.model or result.model
            self.session.add_usage({**response.usage, "calls": 1,
                                    "models_used": [response.model] if response.model else []})
            self.session.append_message("assistant", response.content,
                                        tool_calls=self._assistant_message(response).get("tool_calls"))
            self.session.append_trace("llm_turn", turn=turn, model=response.model,
                                      finish_reason=response.finish_reason,
                                      tool_calls=[c.name for c in response.tool_calls],
                                      usage=response.usage)
            if not response.tool_calls:
                result.answer = response.content
                result.stop_reason = "completed"
                break
            for call in response.tool_calls:
                self.on_event(f"tool {call.name} {json.dumps(call.arguments, ensure_ascii=False)[:160]}")
                execution = self._execute(call)
                result.tool_calls.append(execution)
                payload = execution.result if execution.error is None else {"error": execution.error}
                text = json.dumps(payload, ensure_ascii=False, default=str)[:self.config.max_tool_result_chars]
                self.session.append_message("tool", text, tool_call_id=call.id, name=call.name)
                if execution.error:
                    self.on_event(f"tool {call.name} refused: {execution.error}")
        else:
            result.stop_reason = "max_turns"
            result.error = (f"stopped after {self.config.max_turns} turns without a final answer -- "
                            f"raise --max-turns or narrow the request")
            self.session.append_trace("loop_stop", reason="max_turns", turns=result.turns)

        result.usage = self.router.total_usage
        result.wall_clock_s = round(time.time() - started, 1)
        self.session.update(status={"completed": "open", "max_turns": "incomplete",
                                    "llm_error": "error"}.get(result.stop_reason, "incomplete"))
        for url in result.artifact_urls():
            self.session.record_artifact({"artifact_url": url, "source": "tool_result"})
        return result
