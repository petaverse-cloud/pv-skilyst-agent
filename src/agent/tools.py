"""Tool registry: JSON-schema declarations + executors.

A tool is only registered when the runtime can actually honour it:

  * skill tools (`list_skills`, `read_skill`, `read_skill_file`, `list_skill_files`)
    are always present -- reading is what the runtime is for;
  * platform tools (`beehive_submit_job`, `beehive_get_job`, `beehive_verify_artifact`,
    `beehive_list_assets`) are registered only when a skill is active AND that skill
    declares both a node requirement and `permission.secrets` -- the manifest is the
    contract, so a skill that declares it needs no credential never gets one;
  * `write_workspace_file` is gated by the skill's declared filesystem permission.

Every executor returns a JSON-serialisable dict; every refusal raises, and the
loop hands the exception text back to the model instead of hiding it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from beehive import BeehiveClient, job_handle, verify_artifact
from sandbox import PermissionGate, SandboxViolation, list_resources, read_resource
from skills import SkillPackage, SkillStore

MAX_TOOL_RESULT = 6000


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict], dict]
    scope: str = ""            # informational: which sandbox dimension it needs

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for _, t in sorted(self._tools.items())]

    def call(self, name: str, args: dict) -> dict:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"unknown tool {name!r} (available: {self.names})")
        return spec.handler(args or {})


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


def build_registry(store: SkillStore, client: BeehiveClient | None, active: SkillPackage | None,
                   gate: PermissionGate | None, workspace: Path, dry_run: bool = False,
                   on_event: Callable[[str], None] | None = None,
                   artifact_verifier: Callable[[str], object] | None = None,
                   max_jobs: int = 1) -> ToolRegistry:
    """Assemble the tool set for one run.

    ``max_jobs`` is a spend guard, not a convenience: a submitted job costs real
    money, so a run gets a job budget (default 1) and the runtime refuses to
    submit beyond it. Raising it is an explicit operator decision.
    """
    registry = ToolRegistry()
    events = on_event or (lambda _msg: None)
    verify = artifact_verifier or verify_artifact

    # -- layer 1/2/3: skill reading -----------------------------------------
    def list_skills(_args: dict) -> dict:
        return {"skills": [{"skill_id": p.skill_id, "version": p.version, "degraded": p.degraded,
                            "description": p.description,
                            "requires_nodes": [n.node_id for n in p.requires_nodes]}
                           for p in store.list()]}

    def read_skill(args: dict) -> dict:
        pkg = store.get(args["skill_id"])
        return {"skill_id": pkg.skill_id, "version": pkg.version, "degraded": pkg.degraded,
                "digest": pkg.digest, "permission": pkg.permission, "plan": pkg.plan,
                "requires_nodes": [{"node_id": n.node_id, "version_range": n.version_range,
                                    "optional": n.optional, "fallback": n.fallback}
                                   for n in pkg.requires_nodes],
                "instructions": pkg.body, "files": list_resources(pkg.dir),
                "spec_warnings": list(pkg.warnings)}

    def read_skill_file(args: dict) -> dict:
        pkg = store.get(args["skill_id"])
        return {"skill_id": pkg.skill_id, "path": args["path"],
                "content": read_resource(pkg.dir, args["path"])}

    def list_skill_files(args: dict) -> dict:
        pkg = store.get(args["skill_id"])
        return {"skill_id": pkg.skill_id, "files": list_resources(pkg.dir)}

    registry.register(ToolSpec(
        "list_skills", "List the skills installed in this runtime with their one-line descriptions.",
        _obj({}), list_skills))
    registry.register(ToolSpec(
        "read_skill", "Load a skill's full SKILL.md instructions plus its sandbox declaration, node "
                      "requirements and file index. Call this before acting on a task that matches the "
                      "skill's description.",
        _obj({"skill_id": {"type": "string", "description": "e.g. skilyst/video-15s"}}, ["skill_id"]),
        read_skill))
    registry.register(ToolSpec(
        "list_skill_files", "List every file inside a skill package (progressive disclosure layer 3).",
        _obj({"skill_id": {"type": "string"}}, ["skill_id"]), list_skill_files))
    registry.register(ToolSpec(
        "read_skill_file", "Read one file inside a skill package (references/, scripts/, assets/).",
        _obj({"skill_id": {"type": "string"}, "path": {"type": "string"}}, ["skill_id", "path"]),
        read_skill_file))

    # -- workspace writes (sandbox filesystem dimension) ---------------------
    def write_workspace_file(args: dict) -> dict:
        if gate is None:
            raise SandboxViolation("no active skill: workspace writes are refused")
        target = Path(args["path"])
        target = target if target.is_absolute() else workspace / target
        gate.check_write(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.get("content", ""), encoding="utf-8")
        events(f"wrote {target}")
        return {"written": str(target), "bytes": target.stat().st_size}

    registry.register(ToolSpec(
        "write_workspace_file", "Write a file inside the run workspace (notes, prompt drafts, manifests).",
        _obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
        write_workspace_file, scope="filesystem"))

    # -- platform tools (only with an active skill that declares them) -------
    if active is None or client is None:
        return registry
    if not active.requires_nodes or not active.permission.get("secrets"):
        return registry

    node_ids = [n.node_id for n in active.requires_nodes]
    default_node = node_ids[0]
    plan = active.plan or {}
    jobs: dict[str, dict] = {}
    submitted = {"count": 0}

    def _require_secrets() -> None:
        if gate is not None:
            gate.require_secrets("Beehive credential")

    def submit_job(args: dict) -> dict:
        _require_secrets()
        node_id = args.get("node_id") or default_node
        if node_id not in node_ids:
            raise ValueError(f"node_id {node_id!r} is not declared by {active.skill_id} "
                             f"(declared: {node_ids})")
        node_type, _, variant = node_id.partition(":")
        config = {"prompt": args.get("prompt", ""),
                  "duration": int(args.get("duration") or plan.get("duration") or 0) or None,
                  "resolution": args.get("resolution") or plan.get("resolution"),
                  "ratio": args.get("ratio") or plan.get("ratio")}
        config = {k: v for k, v in config.items() if v not in (None, "")}
        if not config.get("prompt"):
            raise ValueError("prompt is required")
        node = {"type": node_type, "provider": variant, "config": config}
        if dry_run:
            events(f"dry-run: would submit {node_id}")
            return {"dry_run": True, "node": node, "workflow_id": args.get("workflow_id", "")}
        if submitted["count"] >= max_jobs:
            raise RuntimeError(
                f"this run has already submitted {submitted['count']} job(s) and its budget is "
                f"{max_jobs} -- a submitted job costs real money, so a second one needs a new run "
                f"or an explicit --max-jobs")
        payload = client.submit_job([node], workflow_id=args.get("workflow_id", ""))
        handle = job_handle(payload)
        jobs[handle.job_id] = payload
        submitted["count"] += 1
        events(f"submitted job {handle.job_id} ({node_id})")
        result = {"job_id": handle.job_id, "status": handle.status, "node": node,
                  "next": f"call beehive_get_job with job_id={handle.job_id!r} to follow it"}
        if args.get("wait"):
            final = wait(args.get("timeout_s") or 900, handle.job_id)
            result.update(final)
        return result

    def get_job(args: dict) -> dict:
        _require_secrets()
        job_id = args["job_id"]
        if args.get("wait"):
            return wait(args.get("timeout_s") or 900, job_id)
        payload = client.get_job(job_id)
        return _job_view(payload)

    def wait(timeout_s: int, job_id: str) -> dict:
        ticks = {"n": 0}

        def on_tick(state: dict) -> None:
            ticks["n"] += 1
            if ticks["n"] % 6 == 1:
                events(f"job {job_id}: {state.get('status')} progress={state.get('progress')}")

        final = client.wait_for_job(job_id, timeout_s=int(timeout_s), on_tick=on_tick)
        view = _job_view(final)
        if view.get("artifact_url"):
            check = verify(view["artifact_url"])
            view["artifact_verified"] = check.reachable
            view["artifact_detail"] = check.detail
        return view

    def _job_view(payload: dict) -> dict:
        handle = job_handle(payload)
        view = {"job_id": handle.job_id, "status": handle.status, "progress": handle.progress,
                "artifact_url": handle.artifact_url, "error": handle.error,
                "nodes": len(payload.get("nodes") or [])}
        if handle.artifact_url:
            view["report_url_verbatim"] = True
        return view

    def verify_artifact_tool(args: dict) -> dict:
        check = verify(args["url"])
        return {"url": check.url, "reachable": check.reachable, "status": check.status,
                "content_type": check.content_type, "content_length": check.content_length,
                "detail": check.detail}

    def list_assets(args: dict) -> dict:
        _require_secrets()
        assets = client.list_assets(limit=int(args.get("limit") or 20))
        return {"assets": assets}

    registry.register(ToolSpec(
        "beehive_submit_job",
        f"Submit a generation job to the Beehive platform and return its job id. Allowed node ids for "
        f"{active.skill_id}: {', '.join(node_ids)}. Parameters default to the skill's declared plan "
        f"{plan or '{}'}; pass `wait=true` to block until the artifact is ready.",
        _obj({"node_id": {"type": "string", "enum": node_ids,
                          "description": f"node definition id (default {default_node})"},
              "prompt": {"type": "string", "description": "the generation prompt, in English"},
              "duration": {"type": "integer", "description": "seconds"},
              "resolution": {"type": "string"}, "ratio": {"type": "string"},
              "workflow_id": {"type": "string"}, "wait": {"type": "boolean"},
              "timeout_s": {"type": "integer"}}, ["prompt"]),
        submit_job, scope="secrets"))
    registry.register(ToolSpec(
        "beehive_get_job",
        "Poll a job: status, progress, artifact URL. `wait=true` blocks until the job reaches a terminal "
        "status and verifies the artifact with a HEAD request.",
        _obj({"job_id": {"type": "string"}, "wait": {"type": "boolean"},
              "timeout_s": {"type": "integer"}}, ["job_id"]),
        get_job, scope="jobs:read"))
    registry.register(ToolSpec(
        "beehive_verify_artifact",
        "Check that an artifact URL is really downloadable (HEAD: status, content-type, size).",
        _obj({"url": {"type": "string"}}, ["url"]), verify_artifact_tool, scope="jobs:read"))
    registry.register(ToolSpec(
        "beehive_list_assets",
        "List recent assets produced by this account.",
        _obj({"limit": {"type": "integer"}}, []), list_assets, scope="assets:read"))
    return registry


def tool_error_message(exc: Exception) -> str:
    """What the model sees when a tool refuses: the reason, never a silent fallback.

    The type name is kept in the message because it tells the model which layer
    refused (ScopeRefusal = the credential gate, SandboxViolation = the skill's
    declared sandbox, BeehiveError = the platform, ...) and therefore what to do
    differently instead of retrying the same call.
    """
    return f"{type(exc).__name__}: {exc}"
