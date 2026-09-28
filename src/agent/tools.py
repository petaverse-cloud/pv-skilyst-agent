"""Tool registry: JSON-schema declarations + executors.

A tool is only registered when the runtime can actually honour it:

  * skill tools (`list_skills`, `read_skill`, `read_skill_file`, `list_skill_files`)
    are always present -- reading is what the runtime is for;
  * platform tools (`beehive_submit_job`, `beehive_get_job`, `beehive_verify_artifact`,
    `beehive_list_assets`) are registered only when a skill is active AND that skill
    declares both a node requirement and `permission.secrets` -- the manifest is the
    contract, so a skill that declares it needs no credential never gets one;
  * canvas tools (`canvas_*`, the A3 workbench: ten FR-3 tools plus two board reads)
    are registered only when the ACTIVE skill is `skilyst/canvas-ops` and it holds
    `permission.secrets` -- the board writer belongs to the canvas methodology, not
    to every skill with a credential;
  * `write_workspace_file` is gated by the skill's declared filesystem permission.

Every executor returns a JSON-serialisable dict; every refusal raises, and the
loop hands the exception text back to the model instead of hiding it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from beehive import BeehiveClient, job_handle, verify_artifact
from canvas import CanvasOps, LockHeldError
from manifest import BINDING_CONTROL_ARGS, NodeBinding
from sandbox import PermissionGate, SandboxViolation, list_resources, read_resource
from skills import SkillPackage, SkillStore

MAX_TOOL_RESULT = 6000

# The one tool that can spend money, and therefore the only tool a manifest's
# requires.nodes[].binding.tool may name today (the binding is validated against it).
SUBMIT_TOOL = "beehive_submit_job"

# Arguments that steer the call itself rather than the node's input. One definition
# lives in the manifest spec (it is what a binding may not map); the runtime reads it
# from there so the two can never disagree.
CONTROL_ARGS = BINDING_CONTROL_ARGS


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


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
    def __init__(self, job_budget: int | None = None):
        self._tools: dict[str, ToolSpec] = {}
        # Spend accounting lives with the budget: a caller that never checked would
        # otherwise have to scrape the tool results to learn what a run cost.
        self.job_budget = job_budget
        self.jobs_submitted = 0

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


def _submit_properties(node_ids: list[str], declared_args, json_shape, default_node: str = "") -> dict:
    """The tool's argument surface: what the active skill's bindings declare.

    Not a hand-maintained list. Every multimodal argument (images / image_roles /
    audio_refs / video_refs / text) exists here because a manifest declared it, and
    its type comes from the node's live input schema, so a manifest that adds a
    node field does not need a runtime release to forward it.
    """
    properties: dict = {
        "node_id": {"type": "string", "enum": list(node_ids),
                    "description": f"node definition id (default {default_node or node_ids[0]})"}}
    seen: dict[str, dict] = {}
    for node_id in node_ids:
        for argument in declared_args(node_id):
            if argument in seen:
                continue
            shape = json_shape(argument) or {}
            shape.setdefault("description", f"forwarded as the node field "
                                            f"{argument} (see manifest binding.config_map)")
            seen[argument] = shape
    properties.update(seen)
    properties.update({"workflow_id": {"type": "string"},
                       "wait": {"type": "boolean",
                                "description": "block until the job reaches a terminal status and "
                                               "HEAD-verify the artifact"},
                       "timeout_s": {"type": "integer"}})
    return properties


def _required_intersection(node_ids: list[str], required_args) -> list[str]:
    """Arguments every declared node requires.

    A JSON-schema `required` list is enforced before the model can explain itself, so
    it may only contain arguments that *no* declared node would reject -- with one
    node that is exactly the node's own required set; with several it is their
    intersection (the per-node requirement is reported by the tool when it refuses).
    """
    sets = [set(required_args(node_id)) for node_id in node_ids]
    if not sets:
        return []
    common = set.intersection(*sets)
    return sorted(a for a in common if a not in CONTROL_ARGS)


def _submit_description(active, node_ids: list[str], default_node: str, plan: dict,
                        declared_args, required_args, dry_run: bool = False) -> str:
    lines = [f"Submit a generation job to the Beehive platform and return its job id. Node ids allowed "
             f"for {active.skill_id}: {', '.join(node_ids)} (default {default_node})."]
    if dry_run:
        # Without this line an agent that is careful about spending money reads the call
        # as a live submission and refuses to rehearse at all (observed: it declined and
        # explained why). Dry-run must be visible in the contract, not inferred.
        lines.append("This runtime is running in DRY-RUN: a call is validated and returns the exact "
                     "node config it would send, and nothing is submitted or charged.")
    lines.append(
        "Every argument is sent under the node input field the manifest's "
        "requires.nodes[].binding.config_map declares; an argument the binding does not declare "
        "for the chosen node is refused, never dropped.")
    for node_id in node_ids:
        args = ", ".join(declared_args(node_id)) or "none declared"
        required = ", ".join(required_args(node_id)) or "none"
        lines.append(f"{node_id}: accepts {args} (required by the node: {required})")
    if plan:
        lines.append(f"Unset arguments fall back to the skill's declared plan {plan}.")
    lines.append("Pass `wait=true` to block until the artifact is ready.")
    return " ".join(lines)


CANVAS_SKILL_ID = "skilyst/canvas-ops"

# The ten FR-3 canvas tools (docs/a3-canvas-workbench.md) plus the two
# read-only board reads the workbench needs. `create_workflow` deliberately
# stays unexposed: the workbench assumes an existing board.
CANVAS_TOOLS = (
    "canvas_create_node", "canvas_write_node_config", "canvas_connect_ports",
    "canvas_query_schema", "canvas_read_node_output", "canvas_submit_node_job",
    "canvas_run_workflow", "canvas_generate_image", "canvas_list_media",
    "canvas_add_media", "canvas_list_workflows", "canvas_read_board",
)


def build_registry(store: SkillStore, client: BeehiveClient | None, active: SkillPackage | None,
                   gate: PermissionGate | None, workspace: Path, dry_run: bool = False,
                   on_event: Callable[[str], None] | None = None,
                   artifact_verifier: Callable[[str], object] | None = None,
                   max_jobs: int = 1, node_schemas: dict | None = None,
                   session_id: str = "",
                   on_action: Callable[[dict], None] | None = None) -> ToolRegistry:
    """Assemble the tool set for one run.

    ``max_jobs`` is a spend guard, not a convenience: a submitted job costs real
    money, so a run gets a job budget and the runtime refuses to submit beyond it.
    Raising it is an explicit operator decision (``--max-jobs`` / ``SKILYST_MAX_JOBS``).

    ``node_schemas`` maps a declared node id to its registry entry. The runtime uses
    the node's own ``input_schema`` to type the tool arguments and to learn which of
    them the node requires -- the same rules the platform enforces, applied before a
    paid submission instead of after a rejected one.
    """
    registry = ToolRegistry()
    events = on_event or (lambda _msg: None)
    verify = artifact_verifier or verify_artifact

    # -- layer 1/2/3: skill reading -----------------------------------------
    def list_skills(_args: dict) -> dict:
        packages, broken = store.list_partial()
        return {"skills": [{"skill_id": p.skill_id, "version": p.version, "degraded": p.degraded,
                            "description": p.description,
                            "requires_nodes": [n.node_id for n in p.requires_nodes]}
                           for p in packages],
                "unloadable": [{"skill_id": row["skill_id"], "error": row["error"]} for row in broken]}

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

    # -- canvas tools (only for the canvas-ops skill with a live client) --------
    # Gated on the ACTIVE skill being skilyst/canvas-ops (canvas-ops is mostly
    # methodology and declares no node requirements, so this block sits BEFORE
    # the platform-tools early returns): the canvas routes need the credential,
    # and a skill that never declared it must not grow a board writer by
    # accident. `session_id` doubles as the lock holder id (the lock contract's
    # crash recovery checks holder liveness, so the holder must be the thing
    # that dies when the agent dies).
    if client is not None and active is not None and active.skill_id == CANVAS_SKILL_ID \
            and active.permission.get("secrets"):
        _register_canvas_tools(registry, client, session_id, events, on_action)

    # -- platform tools (only with an active skill that declares them) -------
    if active is None or client is None:
        return registry
    if not active.requires_nodes or not active.permission.get("secrets"):
        return registry

    node_ids = [n.node_id for n in active.requires_nodes]
    # The default node is the first *required* one: an optional node is a helper the
    # skill may or may not use (embed-video declares the still generator first), and a
    # call that lands on it because nobody passed node_id submits the wrong thing at
    # the same price.
    default_node = next((n.node_id for n in active.requires_nodes if not n.optional), node_ids[0])
    plan = active.plan or {}
    bindings = {n.node_id: n.binding for n in active.requires_nodes if n.binding}
    schemas = {nid: ((node_schemas or {}).get(nid) or {}).get("input_schema") or {} for nid in node_ids}
    jobs: dict[str, dict] = {}
    registry.job_budget = max_jobs

    def _require_secrets() -> None:
        if gate is not None:
            gate.require_secrets("Beehive credential")

    # -- argument contract, derived from the manifest + the live node schema ----
    def _binding_for(node_id: str) -> NodeBinding:
        binding = bindings.get(node_id)
        if binding is None:
            raise ValueError(
                f"{active.skill_id} declares node {node_id} without "
                f"requires.nodes[].binding.config_map, so the runtime has no declared mapping from "
                f"tool arguments to node fields. Refusing to guess: the package must declare every "
                f"argument it forwards (tool, node_id, config_map) before it can submit a job.")
        if binding.tool != SUBMIT_TOOL:
            raise ValueError(
                f"{active.skill_id} binds node {node_id} to tool {binding.tool!r}, but the tool "
                f"executing this call is {SUBMIT_TOOL!r} -- the package names a tool that does not "
                f"exist (or is not this one)")
        return binding

    def _declared_args(node_id: str) -> list[str]:
        binding = bindings.get(node_id)
        return binding.tool_args() if binding else []

    def _required_args(node_id: str) -> list[str]:
        """The node's required arguments, read off the live input schema.

        The platform rejects a job whose required field is missing; asking here means
        the model is told *which field* before money is spent, and the name it is told
        is the manifest's argument name (not the node's internal spelling).
        """
        binding = bindings.get(node_id)
        out: list[str] = []
        for field in schemas.get(node_id, {}).get("required") or []:
            argument = binding.argument_for(str(field)) if binding else None
            out.append(argument or str(field))
        return out

    def _json_shape(argument: str) -> dict:
        """Tool-argument shape for one argument, taken from the node input schema."""
        for node_id in node_ids:
            binding = bindings.get(node_id)
            if binding is None or argument not in binding.config_map:
                continue
            field_schema = (schemas.get(node_id, {}).get("properties") or {}).get(binding.api_field(argument)) or {}
            shape: dict = {}
            kind = field_schema.get("type")
            if kind == "array":
                items = field_schema.get("items") or {}
                shape["type"] = "array"
                item: dict = {"type": items.get("type", "string")}
                if items.get("enum"):
                    item["enum"] = [v for v in items["enum"] if v != ""]
                shape["items"] = item
            elif kind in ("integer", "number", "boolean", "string"):
                shape["type"] = kind
            values = [v for v in (field_schema.get("enum") or []) if v != ""]
            if values:
                shape["enum"] = values
            if field_schema.get("description"):
                shape["description"] = field_schema["description"]
            return shape
        return {}

    def _coerce(argument: str, field: str, value, field_schema: dict):
        """Reject a value the node cannot accept, under the node's own field name."""
        kind = field_schema.get("type")
        where = f"{argument!r} -> node field {field!r}"
        if kind is None:
            # No schema for this field (registry entry without input_schema): pass the
            # value through untouched rather than stringifying it, but still refuse a
            # malformed reference list -- that check needs no schema.
            if isinstance(value, list):
                if not all(isinstance(item, str) and item.strip() for item in value):
                    raise ValueError(f"{where} entries must be non-empty strings, got {value!r}")
                return [item.strip() for item in value]
            return value
        if kind == "array":
            if not isinstance(value, list):
                raise ValueError(f"{where} must be a JSON array, got {type(value).__name__}")
            items = field_schema.get("items") or {}
            allowed = [v for v in (items.get("enum") or []) if v != ""]
            cleaned = []
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(f"{where} entries must be non-empty strings, got {item!r}")
                if allowed and item not in allowed:
                    raise ValueError(f"{where} entry {item!r} is not one of {allowed}")
                cleaned.append(item.strip())
            return cleaned
        if kind == "integer":
            if isinstance(value, bool):
                raise ValueError(f"{where} must be an integer, got a boolean")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where} must be an integer, got {value!r}") from exc
        if kind == "number":
            if isinstance(value, bool):
                raise ValueError(f"{where} must be a number, got a boolean")
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where} must be a number, got {value!r}") from exc
        if kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{where} must be a boolean, got {value!r}")
            return value
        allowed = [v for v in (field_schema.get("enum") or []) if v != ""]
        if allowed and str(value) not in allowed:
            raise ValueError(f"{where} {value!r} is not one of {allowed}")
        return str(value)

    def _build_config(node_id: str, args: dict) -> dict:
        """Translate the call's arguments into the node config the manifest declares."""
        binding = _binding_for(node_id)
        declared = binding.tool_args()
        unknown = sorted(set(args) - set(declared) - set(CONTROL_ARGS))
        if unknown:
            raise ValueError(
                f"argument(s) {', '.join(repr(u) for u in unknown)} are not declared by "
                f"{active.skill_id} for node {node_id} (declared: {', '.join(declared)}; "
                f"control arguments: {', '.join(CONTROL_ARGS)}). Refusing rather than dropping "
                f"them silently -- a dropped image is a paid render of the wrong thing.")
        properties = schemas.get(node_id, {}).get("properties") or {}
        config: dict = {}
        for argument in declared:
            value = args.get(argument)
            if _empty(value):
                value = plan.get(argument)          # the skill's declared plan, if it defaults it
            if _empty(value):
                continue
            field = binding.api_field(argument)
            config[field] = _coerce(argument, field, value, properties.get(field) or {})
        # Positional pairing is a platform rule (images[i] ↔ image_roles[i]); a mismatch
        # there is refused upstream, so catch it before a paid submission.
        if "images" in config and "image_roles" in config:
            if len(config["images"]) != len(config["image_roles"]):
                raise ValueError(
                    f"images ({len(config['images'])}) and image_roles "
                    f"({len(config['image_roles'])}) are paired positionally -- every image needs a "
                    f"role, otherwise the un-roled ones read as reference images and the platform "
                    f"rejects the mix")
        missing = [argument for argument in _required_args(node_id) if argument not in config]
        if missing:
            raise ValueError(
                f"node {node_id} requires {', '.join(missing)} (the platform rejects the job "
                f"without it); pass it as a tool argument -- declared arguments for this node: "
                f"{', '.join(declared)}")
        return config

    def submit_job(args: dict) -> dict:
        _require_secrets()
        node_id = args.get("node_id") or default_node
        if node_id not in node_ids:
            raise ValueError(f"node_id {node_id!r} is not declared by {active.skill_id} "
                             f"(declared: {node_ids})")
        node_type, _, variant = node_id.partition(":")
        config = _build_config(node_id, args)
        node = {"type": node_type, "provider": variant, "config": config}
        if dry_run:
            events(f"dry-run: would submit {node_id}")
            return {"dry_run": True, "node": node, "workflow_id": args.get("workflow_id", ""),
                    "arguments_sent_as": config, "job_budget": max_jobs, "jobs_submitted": registry.jobs_submitted}
        if registry.jobs_submitted >= max_jobs:
            raise RuntimeError(
                f"this run has already submitted {registry.jobs_submitted} job(s) and its budget is "
                f"{max_jobs} -- a submitted job costs real money, so a further one needs a new run "
                f"or an explicit --max-jobs / SKILYST_MAX_JOBS")
        payload = client.submit_job([node], workflow_id=args.get("workflow_id", ""))
        handle = job_handle(payload)
        jobs[handle.job_id] = payload
        registry.jobs_submitted += 1
        events(f"submitted job {handle.job_id} ({node_id})")
        result = {"job_id": handle.job_id, "status": handle.status, "node": node,
                  "arguments_sent_as": config, "jobs_submitted": registry.jobs_submitted,
                  "job_budget": max_jobs,
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
        _submit_description(active, node_ids, default_node, plan, _declared_args, _required_args,
                            dry_run=dry_run),
        _obj(_submit_properties(node_ids, _declared_args, _json_shape, default_node),
             _required_intersection(node_ids, _required_args)),
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


# -- canvas tools ---------------------------------------------------------------


def _micro_to_usd_cost(quote: dict | None) -> dict | None:
    """The action-stream cost view of a quote: USD floats for display, raw
    micro-USD for accounting. None when the call carried no quote."""
    if not quote:
        return None
    estimate = quote.get("total_estimate_usd")
    hold = quote.get("total_hold")
    cost: dict = {}
    if estimate is not None:
        cost["estimate_usd"] = round(estimate / 1_000_000, 6)
    if hold is not None:
        cost["hold_micro_usd"] = int(hold)
    return cost or None


def _register_canvas_tools(registry: ToolRegistry, client, session_id: str,
                           events: Callable[[str], None],
                           on_action: Callable[[dict], None] | None) -> None:
    """Wire the twelve canvas tools (ten FR-3 tools + two board reads).

    Every tool shares one wrapper (`_make`) that provides the R3 action
    stream contract: timing, board activity events, the action row
    ({type, tool, params, result_ref, board_delta, origin} + duration/cost when
    known), and the lock-held note (画板正被占用) when a write hits a live
    holder -- the loop's error path tells the model; the note tells the human
    transcript.
    """
    ops = CanvasOps(client, session_id)
    actions = on_action or (lambda _action: None)

    def _make(tool_name: str, method_name: str,
              delta: Callable[[dict, dict], dict | None] | None = None,
              event: Callable[[dict, dict], str] | None = None):
        """Build one canvas tool handler around a CanvasOps method.

        The shared body provides the whole R3 contract: timing (duration_s on
        the action row), a board-activity progress line on the SSE note stream
        (`event`), the action row itself ({type, tool, params, board_delta,
        origin} + result_ref/cost when the call produced one), and the
        lock-held note -- a LockHeldError becomes BOTH a note row on the
        transcript (for the human) and the re-raised error the loop reports
        to the model. A lock-held failure is an action too: the desktop's
        board replay must show that the write was refused.
        """
        method = getattr(ops, method_name)

        def handler(args: dict) -> dict:
            started = time.time()
            try:
                result = method(**args)
            except LockHeldError as exc:
                holder = exc.held_by.get("holder") or {}
                actions({"type": "canvas", "tool": tool_name, "params": args,
                         "result_ref": None, "board_delta": None, "origin": "agent",
                         "note_kind": "lock",
                         "note_text": f"画板正被占用: held by "
                                      f"{holder.get('kind', '?')}/{holder.get('id', '?')} "
                                      f"since {exc.held_by.get('acquired_at', '?')}",
                         "duration_s": round(time.time() - started, 2)})
                events(f"canvas: {tool_name} refused -- workflow {exc.workflow_id} "
                       f"is locked by another holder")
                raise
            duration_s = round(time.time() - started, 2)
            action = {"type": "canvas", "tool": tool_name, "params": args,
                      "board_delta": delta(result, args) if delta else None,
                      "origin": "agent", "duration_s": duration_s,
                      "result_ref": _result_ref(result)}
            quote = result.get("quote") if isinstance(result, dict) else None
            cost = _micro_to_usd_cost(quote)
            if cost:
                action["cost"] = cost
            if event:
                events(event(result, args))
            actions(action)
            return result

        return handler

    _register_canvas_specs(registry, _make)


def _result_ref(result) -> dict | None:
    """The compact action-row summary of a canvas call's result: ids the
    desktop can follow up on (job id, node key, pool entry id), never the
    whole response."""
    if not isinstance(result, dict):
        return None
    ref = {key: result[key] for key in ("job_id", "node_count", "found") if result.get(key) is not None}
    node = result.get("node")
    if isinstance(node, dict) and node.get("key") is not None:
        ref["node"] = node["key"]
    added = result.get("added")
    if isinstance(added, dict) and added.get("id") is not None:
        ref["pool_entry"] = added["id"]
    return ref or None


def _register_canvas_specs(registry: ToolRegistry, make) -> None:
    """The twelve ToolSpecs: names, teaching descriptions, schemas, handlers."""

    def _node_key(result: dict, _args: dict):
        node = result.get("node") or {}
        return {"added_node": node.get("key"), "workflow_id": _args.get("workflow_id")}

    def _wired(result: dict, args: dict):
        return {"wired": [args.get("from_key"), args.get("to_key"),
                          args.get("input_port") or ""],
                "workflow_id": args.get("workflow_id")}

    def _config_written(result: dict, args: dict):
        return {"updated_config": args.get("key"), "workflow_id": args.get("workflow_id")}

    def _submitted(result: dict, args: dict):
        return {"submitted_job": result.get("job_id"),
                "workflow_id": args.get("workflow_id")}

    def _ran(result: dict, args: dict):
        return {"submitted_job": result.get("job_id"), "node_count": result.get("node_count"),
                "workflow_id": args.get("workflow_id")}

    def _pool_entry(result: dict, args: dict):
        added = result.get("added") or {}
        return {"added_pool_entry": added.get("id"), "workflow_id": args.get("workflow_id")}

    registry.register(ToolSpec(
        "canvas_create_node",
        "Add a node to a canvas board (workflow blueprint). Auto-key is '{type}-{provider}-{n}', "
        "auto-position spreads nodes horizontally. The write is wrapped in the server-side board "
        "lock (lock -> read -> modify -> PUT -> unlock), so a board someone else is editing fails "
        "with a lock-held error instead of overwriting.",
        _obj({"workflow_id": {"type": "string", "description": "the board to edit"},
              "node_type": {"type": "string", "description": "e.g. 'generate', 'process', 'material'"},
              "provider": {"type": "string", "description": "node variant, e.g. 'minimax-h3'"},
              "key": {"type": "string", "description": "explicit node key (default: auto-key)"},
              "config": {"type": "object", "description": "initial node config"},
              "position": {"type": "object", "description": "{x, y} board position (default: auto)"},
              "depends_on": {"type": "array", "items": {"type": "string"},
                             "description": "keys of nodes this node consumes"}},
             ["workflow_id", "node_type", "provider"]),
        make("canvas_create_node", "create_node", _node_key,
             lambda r, a: f"canvas: node {(r.get('node') or {}).get('key')} added to "
                          f"{a.get('workflow_id')}"), scope="workflows:write"))

    registry.register(ToolSpec(
        "canvas_write_node_config",
        "Deep-merge a config patch into one node of a board (new keys added, scalars "
        "overwritten, nested dicts merged) -- a partial edit, no need to restate the whole config.",
        _obj({"workflow_id": {"type": "string"}, "key": {"type": "string",
                                                          "description": "the node to edit"},
              "config": {"type": "object"}}, ["workflow_id", "key", "config"]),
        make("canvas_write_node_config", "write_node_config", _config_written,
             lambda r, a: f"canvas: config of {a.get('key')} updated on {a.get('workflow_id')}"),
        scope="workflows:write"))

    registry.register(ToolSpec(
        "canvas_connect_ports",
        "WIRE THE SOURCE FIRST: call canvas_query_schema on the target node before connecting "
        "to check input_schema enum/port constraints (e.g. minimax-h3 image inputs are "
        "exclusive: first_frame vs reference_image cannot mix). Wires an edge on the board: a "
        "material node wires as material_deps {key, input_port} on the consumer plus a "
        "depends_on edge; any other node wires as a plain depends_on edge. Image materials "
        "only accept the ports reference_image/first_frame/last_frame.",
        _obj({"workflow_id": {"type": "string"},
              "from_key": {"type": "string", "description": "source node key"},
              "to_key": {"type": "string", "description": "consumer node key"},
              "input_port": {"type": "string",
                             "description": "material input port (reference_image/first_frame/"
                                            "last_frame); omit for a plain edge"}},
             ["workflow_id", "from_key", "to_key"]),
        make("canvas_connect_ports", "connect_ports", _wired,
             lambda r, a: f"canvas: wired {a.get('from_key')} -> {a.get('to_key')} "
                          f"on {a.get('workflow_id')}"), scope="workflows:write"))

    registry.register(ToolSpec(
        "canvas_query_schema",
        "Read a node definition (input/output schema, config defaults) plus the derived port "
        "analysis: enum fields, required fields, and alternative input modes. ALWAYS call this "
        "before canvas_connect_ports or a submission -- it is how an invalid port or a mixed "
        "input mode is caught BEFORE the wiring decision instead of as a platform 400 after it.",
        _obj({"node_type": {"type": "string"}, "provider": {"type": "string"},
              "node_id": {"type": "string", "description": "exact id, e.g. 'generate:minimax-h3'"}},
             []),
        make("canvas_query_schema", "query_schema"), scope="nodes:read"))

    registry.register(ToolSpec(
        "canvas_read_node_output",
        "Read the newest job output for one node of a board (poll after submitting: generation "
        "is async). Returns {found: false} when no job has produced output for that node yet.",
        _obj({"workflow_id": {"type": "string"}, "key": {"type": "string"}},
             ["workflow_id", "key"]),
        make("canvas_read_node_output", "read_node_output"), scope="jobs:read"))

    registry.register(ToolSpec(
        "canvas_submit_node_job",
        "Submit ONE node as a job on the board. Quote-first by default: the paid estimate is "
        "fetched before the submission exists, and the quote (micro-USD + USD display) is "
        "returned with the job id. Submitting does NOT mutate the blueprint and does not take "
        "the board lock; poll the result with canvas_read_node_output.",
        _obj({"workflow_id": {"type": "string"},
              "node_type": {"type": "string"}, "provider": {"type": "string"},
              "config": {"type": "object"}, "key": {"type": "string"},
              "quote_first": {"type": "boolean", "description": "default true"}},
             ["workflow_id", "node_type", "provider", "config"]),
        make("canvas_submit_node_job", "submit_node_job", _submitted,
             lambda r, a: f"canvas: submitted job {r.get('job_id')} on {a.get('workflow_id')}"),
        scope="jobs:write"))

    registry.register(ToolSpec(
        "canvas_run_workflow",
        "Submit every node of a board as ONE job, verbatim -- the workflow is the plan of "
        "record, so the job is a faithful copy of it. Quote-first by default. Does not take "
        "the board lock (a job never mutates the blueprint).",
        _obj({"workflow_id": {"type": "string"}, "quote_first": {"type": "boolean"}},
             ["workflow_id"]),
        make("canvas_run_workflow", "run_workflow", _ran,
             lambda r, a: f"canvas: ran workflow {a.get('workflow_id')} "
                          f"({r.get('node_count')} nodes, job {r.get('job_id')})"),
        scope="jobs:write"))

    registry.register(ToolSpec(
        "canvas_generate_image",
        "Submit a PAID image generation node on a board (model gpt-image-2). Always "
        "quote-first. This only submits -- generation is async: poll with canvas_read_node_output "
        "or beehive_get_job; when the job completes the server auto-captures the image into the "
        "board's media pool (origin.kind=generated).",
        _obj({"workflow_id": {"type": "string"}, "prompt": {"type": "string"},
              "model": {"type": "string", "description": "default 'gpt-image-2'"},
              "size": {"type": "string", "description": "default '1024x1024'"}},
             ["workflow_id", "prompt"]),
        make("canvas_generate_image", "generate_image", _submitted,
             lambda r, a: f"canvas: image job {r.get('job_id')} submitted on "
                          f"{a.get('workflow_id')}"), scope="jobs:write"))

    registry.register(ToolSpec(
        "canvas_list_media",
        "List a board's media pool entries (id, name, kind, url, thumb).",
        _obj({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        make("canvas_list_media", "list_media"), scope="workflows:read"))

    registry.register(ToolSpec(
        "canvas_add_media",
        "Add an upload-origin entry to a board's media pool (an image the user supplied by URL). "
        "The server generates the entry id; the new entry is identified in the result by url.",
        _obj({"workflow_id": {"type": "string"}, "url": {"type": "string"},
              "name": {"type": "string"}, "kind": {"type": "string",
                                                   "description": "default 'image'"},
              "mime": {"type": "string", "description": "default 'image/png'"}},
             ["workflow_id", "url", "name"]),
        make("canvas_add_media", "add_media", _pool_entry,
             lambda r, a: f"canvas: media {(r.get('added') or {}).get('id')} added to "
                          f"{a.get('workflow_id')}"), scope="workflows:write"))

    registry.register(ToolSpec(
        "canvas_list_workflows",
        "List the canvas boards (workflows) this account can see: id, name, description, "
        "updated_at. Read-only. Use this to ask which board to operate on before editing.",
        _obj({"limit": {"type": "integer", "description": "default 50"}}, []),
        make("canvas_list_workflows", "list_workflows"), scope="workflows:read"))

    registry.register(ToolSpec(
        "canvas_read_board",
        "Read one board's full state: nodes, edges (derived from material_deps/depends_on) and "
        "the media pool. Read-only, no lock. Read this before editing to see what is already "
        "on the board.",
        _obj({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        make("canvas_read_board", "read_board"), scope="workflows:read"))


def tool_error_message(exc: Exception) -> str:
    """What the model sees when a tool refuses: the reason, never a silent fallback.

    The type name is kept in the message because it tells the model which layer
    refused (ScopeRefusal = the credential gate, SandboxViolation = the skill's
    declared sandbox, BeehiveError = the platform, ...) and therefore what to do
    differently instead of retrying the same call.
    """
    return f"{type(exc).__name__}: {exc}"
