"""Run assembly: the single place a session + tool registry + loop is built.

The CLI (`skilyst run|chat`) and the local server (`skilyst serve`) must start a
run *identically* -- same skill lookup, same credential gate, same pre-flight,
same tool set, same summary shape. Keeping that in one function is what stops
the desktop shell from quietly becoming a second, weaker runtime: the shell only
ever calls `open_run`, so an invariant added here is an invariant the GUI has.

Nothing in this module talks to the network by itself. `client_factory` exists so
a test can drive the whole chain (store -> registry -> loop -> session) with a
scripted transport instead of a live model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from beehive import BeehiveClient, client_for, client_from_bearer
from config import ConfigError, RuntimeConfig
from llm import ChatClient, LLMConfig, ModelRouter
from sandbox import PermissionGate
from session import Session, SessionStore
from skills import SkillPackage, SkillStore, SkillValidationError
from skills.store import PreflightReport, preflight_nodes

from .loop import AgentLoop, LoopConfig, LoopResult
from .prompt import PromptContext
from .tools import build_registry


def skill_store(cfg: RuntimeConfig, verify: bool = True) -> SkillStore:
    return SkillStore(cfg.store_dir, verify=verify)


def gated_client(cfg: RuntimeConfig, bypass: bool = False) -> BeehiveClient:
    """The only way a skill-facing component reaches the platform."""
    if cfg.beehive.has_api_key:
        client = client_for(cfg.beehive.access_key, cfg.beehive.secret_key, cfg.beehive.user,
                            cfg.beehive.base_url)
        client.bypass_scope = bypass
        return client
    if cfg.beehive.has_password:
        bearer = BeehiveClient(cfg.beehive.base_url).login(cfg.beehive.user, cfg.beehive.password)
        client = client_from_bearer(cfg.beehive.base_url, bearer, cfg.beehive.user)
        client.bypass_scope = bypass
        return client
    raise ConfigError("no usable Beehive credential (need AK/SK or user/password)")


def load_skill(store: SkillStore, skill_id: str) -> SkillPackage:
    try:
        return store.get(skill_id)
    except KeyError as exc:
        raise SkillValidationError(str(exc)) from exc


@dataclass
class RunContext:
    """Everything one run needs, plus the loop that executes it."""

    cfg: RuntimeConfig
    session: Session
    active: SkillPackage | None
    preflight: PreflightReport | None
    loop: AgentLoop
    skills: list[SkillPackage] = field(default_factory=list)

    def result_payload(self, result: LoopResult) -> dict:
        return run_summary(result, self.session, self.active, self.preflight)


def open_run(cfg: RuntimeConfig, *, skill_id: str | None = None, session_id: str | None = None,
             title: str = "", dry_run: bool = False, max_turns: int = 8, stream: bool = True,
             allow_fallback: bool = False, max_jobs: int = 1,
             on_event: Callable[[str], None] | None = None,
             on_delta: Callable[[str], None] | None = None,
             client_factory: Callable[[LLMConfig], ChatClient] | None = None) -> RunContext:
    """Build the run for one user turn.

    ``max_jobs`` is a spend guard carried over from the tool registry: a run gets
    a job budget (default 1) and the runtime refuses to submit beyond it.
    """
    note = on_event or (lambda _message: None)
    store = skill_store(cfg)
    skills, broken = store.list_partial()
    for row in broken:
        note(f"integrity[{row['skill_id']}]: {row['error']}")
    active = load_skill(store, skill_id) if skill_id else None
    if active is not None and active.skill_id not in {p.skill_id for p in skills}:
        raise SkillValidationError(f"{active.skill_id} is not installed in {cfg.store_dir}")

    gate = PermissionGate(active.dir, active.permission, workspace=cfg.workspace_dir) if active else None
    client = None
    preflight: PreflightReport | None = None
    if active is not None and active.requires_nodes:
        client = gated_client(cfg)
        preflight = preflight_nodes(active, client, allow_fallback=allow_fallback)

    cfg.workspace_dir.mkdir(parents=True, exist_ok=True)
    sessions = SessionStore(cfg.session_dir)
    session = (sessions.open(session_id) if session_id
               else sessions.create(title=title, model=cfg.llm.model,
                                    workspace=str(cfg.workspace_dir),
                                    skills=[active.skill_id] if active else []))
    registry = build_registry(store, client, active, gate, cfg.workspace_dir, dry_run=dry_run,
                              on_event=note, max_jobs=max_jobs,
                              node_schemas=preflight.node_schemas if preflight else None)
    prompt_ctx = PromptContext(skills=skills, active_skill=active, workspace=str(cfg.workspace_dir),
                               model=cfg.llm.model, platform=cfg.beehive.base_url)
    loop = AgentLoop(ModelRouter(cfg.llm, client_factory=client_factory), registry, session, prompt_ctx,
                     LoopConfig(max_turns=max_turns, stream=stream), on_event=note, on_delta=on_delta)
    return RunContext(cfg=cfg, session=session, active=active, preflight=preflight, loop=loop,
                      skills=skills)


def run_summary(result: LoopResult, session: Session, active: SkillPackage | None,
                preflight: PreflightReport | None) -> dict:
    """The one JSON shape a finished run is reported in (CLI stdout and HTTP body)."""
    return {"session_id": session.session_id, "skill": active.skill_id if active else None,
            "stop_reason": result.stop_reason, "answer": result.answer, "model": result.model,
            "turns": result.turns, "wall_clock_s": result.wall_clock_s, "usage": result.usage,
            "artifacts": result.artifact_urls(),
            "jobs": {"submitted": result.jobs_submitted, "budget": result.job_budget},
            "tool_calls": [{"tool": c.name, "arguments": c.arguments, "result": c.result, "error": c.error,
                            "duration_s": c.duration_s} for c in result.tool_calls],
            "preflight": None if preflight is None else {
                "registry_available": preflight.registry_available,
                "problems": [{"node_id": p.node_id, "severity": p.severity, "detail": p.detail}
                             for p in preflight.problems]},
            "error": result.error or None}


def preflight_payload(preflight: PreflightReport | None) -> dict | None:
    if preflight is None:
        return None
    return {"registry_available": preflight.registry_available,
            "runnable": preflight.runnable,
            "problems": [{"node_id": p.node_id, "severity": p.severity, "detail": p.detail}
                         for p in preflight.problems]}


__all__ = ["RunContext", "gated_client", "load_skill", "open_run", "preflight_payload", "run_summary",
           "skill_store"]
