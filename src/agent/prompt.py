"""System prompt assembly (agentskills.io progressive disclosure, three layers).

  layer 1 -- metadata: every installed skill contributes exactly one line
             (`skill_id@version: description`), never its full instructions;
  layer 2 -- instructions: the model pulls a skill's SKILL.md with `read_skill`
             when the task actually matches it;
  layer 3 -- resources: the model opens individual package files with
             `read_skill_file` / `list_skill_files`.

Injecting full skill bodies up front is what makes a runtime stop scaling at a
handful of skills, so the index is the only thing always in context. The prompt
also carries the hard behavioural rules the platform depends on: artifacts are
reported verbatim, failures are reported verbatim, and a render tier is never
silently upgraded (it costs real money).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from skills import SkillPackage

IDENTITY = """You are Skilyst Agent, the audio/video production agent for the Skilyst platform.
You work the way a director works: read the method, decide the shot, then call the platform to produce it."""

RULES = """Hard rules:
1. A skill's method is the method: after `read_skill`, follow its instructions in the order it gives them.
2. Report an artifact URL exactly as the platform returned it. Never invent, shorten or "fix" a URL.
3. If a job fails, report the platform's error verbatim. Never substitute another result.
4. Never change a render tier (duration/resolution/provider) that the user or the skill fixed. A more
   expensive tier is a separate decision that needs explicit user confirmation.
5. `billing` and `admin` endpoints are not available to you at any point: the runtime refuses those calls
   client-side before any request is sent. If a user asks you to top up or to change pricing, tell them
   the Skilyst app handles it (the client deep-links to the recharge page) -- you cannot spend money.
6. When a tool returns an error, adapt or stop. Do not retry the same failing call more than once."""


@dataclass
class PromptContext:
    skills: list[SkillPackage] = field(default_factory=list)
    active_skill: SkillPackage | None = None
    workspace: str = ""
    model: str = ""
    platform: str = ""
    extra: list[str] = field(default_factory=list)


def skill_index(skills: list[SkillPackage]) -> str:
    if not skills:
        return ("(no skills installed -- install a skill package first: "
                "`skilyst install <dir>` or `skilyst preload <bundle-dir>`)")
    lines = []
    for pkg in sorted(skills, key=lambda p: p.skill_id):
        suffix = " [community/compat: no manifest]" if pkg.degraded else ""
        lines.append(pkg.prompt_index_line + suffix)
    return "\n".join(lines)


def build_system_prompt(ctx: PromptContext) -> str:
    parts = [IDENTITY, RULES, f"Today: {time.strftime('%Y-%m-%d %H:%M %Z')}."]
    parts.append("Installed skills (metadata only -- call `read_skill` to load one's full instructions "
                 "before using it):\n" + skill_index(ctx.skills))
    if ctx.active_skill:
        pkg = ctx.active_skill
        perm = pkg.permission
        nodes = ", ".join(f"{n.node_id} ({n.version_range}{', optional' if n.optional else ''})"
                          for n in pkg.requires_nodes) or "none"
        parts.append(
            f"Active skill: {pkg.skill_id}@{pkg.version}.\n"
            f"- content digest: {pkg.digest}\n"
            f"- declared sandbox: egress={perm.get('egress')}, filesystem={perm.get('filesystem')}, "
            f"exec={perm.get('exec')}, secrets={perm.get('secrets')}\n"
            f"- declared node requirements: {nodes}\n"
            f"- declared plan: {pkg.plan or 'none'}\n"
            f"Its tools are the only route to the platform in this run; `beehive_submit_job` defaults "
            f"come from that plan.")
    else:
        parts.append("No skill is active in this session: you can read and discuss skills, but platform "
                     "tools are only enabled for a run with an active skill (`skilyst run <skill-id> …`).")
    if ctx.workspace:
        parts.append(f"Workspace (the only directory you may write to): {ctx.workspace}")
    if ctx.platform:
        parts.append(f"Platform API: {ctx.platform}")
    if ctx.model:
        parts.append(f"Model: {ctx.model}")
    parts.extend(ctx.extra)
    return "\n\n".join(parts)
