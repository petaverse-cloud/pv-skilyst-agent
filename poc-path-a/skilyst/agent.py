"""Minimal agent loop: the part of an agent runtime that is NOT the loader.

The PoC keeps this deliberately small -- one LLM tool-calling loop over the
skill's declared node plan -- precisely to expose the size of the gap versus a
mature runtime (session state, streaming, multi-tool orchestration, approvals,
sub-agents, cron, gateways). Everything here uses the OpenAI-compatible
endpoint already configured for the platform agent profile.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

JOB_TOOL = {
    "type": "function",
    "function": {
        "name": "beehive_submit_video",
        "description": "Submit a Beehive video-generation job and wait for the artifact. "
                       "Allowed parameters come from the loaded skill's requires.nodes plan.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text prompt for the video model"},
                "duration": {"type": "integer", "description": "Seconds, 4-15"},
                "resolution": {"type": "string", "enum": ["768P", "2K"]},
                "ratio": {"type": "string", "description": "e.g. 9:16"},
            },
            "required": ["prompt", "duration"],
        },
    },
}


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 120


class LLMError(RuntimeError):
    pass


class MinimishAgent:
    """tool-calling loop; deliberately no session/streaming/multi-agent features."""

    def __init__(self, cfg: LLMConfig, tools: list[dict], executor, max_turns: int = 6, verbose: bool = True):
        self.cfg = cfg
        self.tools = tools
        self.executor = executor
        self.max_turns = max_turns
        self.verbose = verbose
        self.trace: list[dict] = []

    def _chat(self, messages: list[dict]) -> dict:
        body = json.dumps({"model": self.cfg.model, "messages": messages,
                           "tools": self.tools, "temperature": 0}).encode()
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"LLM HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}") from exc

    def run(self, system: str, user: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for turn in range(self.max_turns):
            started = time.time()
            resp = self._chat(messages)
            choice = resp["choices"][0]["message"]
            tool_calls = choice.get("tool_calls") or []
            self.trace.append({"turn": turn, "latency_s": round(time.time() - started, 1),
                               "tool_calls": [t["function"]["name"] for t in tool_calls]})
            messages.append({"role": "assistant", "content": choice.get("content") or "",
                             **({"tool_calls": tool_calls} if tool_calls else {})})
            if not tool_calls:
                return choice.get("content") or ""
            for call in tool_calls:
                args = json.loads(call["function"].get("arguments") or "{}")
                try:
                    result = self.executor(call["function"]["name"], args)
                except Exception as exc:  # tool errors go back to the model, not to a silent fallback
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)[:4000]})
        raise RuntimeError(f"agent exceeded {self.max_turns} turns without finishing")


def llm_from_hermes_config(config_path: str) -> LLMConfig:
    """Reuse the platform agent profile's model settings (same model => fair comparison)."""
    import re
    text = open(config_path).read()
    model = re.search(r"^\s*default:\s*(\S+)", text, re.M)
    base = re.search(r"^\s*base_url:\s*(\S+)", text, re.M)
    key = re.search(r"^\s*api_key:\s*(\S+)", text, re.M)
    provider = re.search(r"^\s*provider:\s*(\S+)", text, re.M)
    if not all((model, base, key)):
        raise RuntimeError(f"could not read model settings from {config_path}")
    if provider and "custom:" in provider.group(1):
        pass
    return LLMConfig(base_url=base.group(1), api_key=key.group(1), model=model.group(1))
