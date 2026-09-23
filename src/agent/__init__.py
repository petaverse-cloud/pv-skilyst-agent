"""Agent layer: prompt assembly, tool registry, and the tool-calling loop."""
from .loop import AgentLoop, LoopConfig, LoopResult, ToolExecution
from .prompt import PromptContext, build_system_prompt, skill_index
from .tools import ToolRegistry, ToolSpec, build_registry

__all__ = ["AgentLoop", "LoopConfig", "LoopResult", "ToolExecution",
           "PromptContext", "build_system_prompt", "skill_index",
           "ToolRegistry", "ToolSpec", "build_registry"]
