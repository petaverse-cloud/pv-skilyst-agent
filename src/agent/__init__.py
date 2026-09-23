"""Agent layer: prompt assembly, tool registry, the tool-calling loop, run assembly."""
from .loop import AgentLoop, LoopConfig, LoopResult, ToolExecution
from .prompt import PromptContext, build_system_prompt, skill_index
from .runner import (RunContext, gated_client, load_skill, open_run, preflight_payload, run_summary,
                     skill_store)
from .tools import ToolRegistry, ToolSpec, build_registry

__all__ = ["AgentLoop", "LoopConfig", "LoopResult", "ToolExecution",
           "PromptContext", "build_system_prompt", "skill_index",
           "ToolRegistry", "ToolSpec", "build_registry",
           "RunContext", "gated_client", "load_skill", "open_run", "preflight_payload", "run_summary",
           "skill_store"]
