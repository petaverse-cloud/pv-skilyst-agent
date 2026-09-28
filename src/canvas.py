"""Canvas operations: the agent side of the A3 canvas workbench (FR-2/FR-3).

`CanvasOps` implements the ten tools of the official canvas skill
(`skilyst/canvas-ops`, docs/a3-canvas-workbench.md FR-3) against the real
Beehive core API. Two platform contracts shape everything in here:

* **Lock discipline** (a3-canvas-workbench.md R1 / a3-lock-contract.md v0.4):
  every blueprint write is an explicit lock -> read -> modify -> PUT -> unlock
  action pair. The lock lives on the server, so the same discipline binds the
  web console and the desktop app -- the agent is a *disciplined* writer, not
  the only writer. Acquiring a lock held by a live holder raises
  `LockHeldError` (user-facing 画板正被占用, holder detail in English); unlock
  is idempotent, so replaying it after crash recovery is safe.
* **Quote-first** (D3: the agent never touches money unexamined): paid
  submissions estimate before they submit. Quote amounts are integer
  micro-USD; both the raw micro values and a USD display figure are returned
  so nobody has to guess the unit.

Media-pool preservation on PUT: the body is built from the GET row *without*
`media_pool` -- the server keeps the stored pool when the field is absent and
`X-Beehive-Pool-Replace` is not set. Echoing the pool back would race with
server-side auto-capture (a finished generation lands in the pool without the
agent's help), so the field is dropped, never resent.

Job submission is deliberately NOT wrapped in the lock: submitting refreshes
the holder heartbeat (any authenticated call does, per the lock contract) and
a job never mutates the blueprint, so there is nothing to protect.
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from typing import Callable

from beehive import BeehiveError

# The ports an image material may feed. The server enforces this on
# material_deps; we validate early so the agent gets a named-port error
# instead of a platform 400 after the wiring decision was already made.
VALID_IMAGE_PORTS = ("reference_image", "first_frame", "last_frame")

# How many node ids a failed query_schema lists before the message stops helping.
NODE_ID_LIST_LIMIT = 40

# Paid image models the canvas knows: model name -> node provider (node id is
# always f"generate:{provider}").
IMAGE_MODELS = {"gpt-image-2": "gpt-image-2"}

__all__ = ["CanvasOps", "LockHeldError", "VALID_IMAGE_PORTS", "IMAGE_MODELS"]


class LockHeldError(RuntimeError):
    """The workflow write lock is held by a live holder (lock endpoint 409).

    Carries the `held_by` block from the lock contract so the caller can say
    who is on the board and since when, instead of a bare conflict.
    """

    def __init__(self, workflow_id: str, held_by: dict | None = None):
        self.workflow_id = workflow_id
        self.held_by = dict(held_by or {})
        holder = self.held_by.get("holder") or {}
        detail = (f"workflow {workflow_id} is locked by holder "
                  f"kind={holder.get('kind')!r} id={holder.get('id')!r} "
                  f"acquired_at={self.held_by.get('acquired_at')!r}")
        super().__init__(f"画板正被占用 ({detail})")


# -- pure helpers -------------------------------------------------------------


def _deep_merge(target: dict, patch: dict) -> dict:
    """Merge `patch` into `target`: new keys added, scalars overwritten, nested
    dicts merged recursively. In-place on `target` (callers pass a copy)."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def _find_node(workflow: dict, key: str) -> dict:
    """The blueprint node with this key, or a loud ValueError naming the keys
    that DO exist -- 'not found' without the alternatives is a dead end for
    the agent."""
    for node in workflow.get("nodes") or []:
        if node.get("key") == key:
            return node
    keys = [str(node.get("key")) for node in workflow.get("nodes") or []]
    raise ValueError(f"no node with key {key!r} in workflow {workflow.get('id')!r} "
                     f"(existing keys: {', '.join(keys) or 'none'})")


def _material_kind(workflow: dict, node: dict) -> str:
    """What kind of media a material node carries: its own config declaration
    first, then the media-pool entry it points at."""
    config = node.get("config") or {}
    if config.get("kind"):
        return str(config["kind"])
    entry_id = config.get("pool_entry_id")
    for entry in workflow.get("media_pool") or []:
        if entry.get("id") == entry_id:
            return str(entry.get("kind") or "")
    return ""


def _put_body(workflow: dict) -> dict:
    """Build the PUT body from a GET row.

    `media_pool` is dropped on purpose: an absent field means "server, keep
    the stored pool" (X-Beehive-Pool-Replace stays unset), which is the only
    way to write the blueprint without racing server-side auto-capture.
    `expected_updated_at` is carried from the row so a concurrent write
    surfaces as a 409 instead of silently overwriting.
    """
    body = {key: value for key, value in workflow.items() if key != "media_pool"}
    expected = workflow.get("expected_updated_at") or workflow.get("updated_at")
    if expected is not None:
        body["expected_updated_at"] = expected
    return body


def _micro_to_usd(micro) -> float | None:
    return None if micro is None else round(micro / 1_000_000, 6)


def _quote_view(quote: dict) -> dict:
    """Expose the quote both as the API returns it (integer micro-USD) and as
    a USD display figure -- a unit guessed wrong is a budget guessed wrong."""
    quote = quote or {}
    return {"total_estimate_usd": quote.get("total_estimate_usd"),
            "total_hold": quote.get("total_hold"),
            "total_estimate_usd_display": _micro_to_usd(quote.get("total_estimate_usd")),
            "total_hold_display": _micro_to_usd(quote.get("total_hold")),
            "nodes": quote.get("nodes") or []}


def _newest_first(jobs: list[dict]) -> list[dict]:
    """The core lists jobs newest-first; sort explicitly when timestamps are
    present so the caller's 'newest match' really is the newest."""
    if jobs and all(job.get("created_at") for job in jobs):
        return sorted(jobs, key=lambda job: str(job.get("created_at")), reverse=True)
    return jobs


def _port_analysis(input_schema: dict) -> dict:
    """What connect_ports needs to know before it wires anything.

    The lanes lesson, institutionalized (FR-3: connect must be preceded by
    query_schema): the platform rejects an invalid port or a mixed input mode
    with a 400 *after* the wiring decision was made. Reading the enums and the
    alternative input modes off the input_schema puts the refusal before the
    decision, where the agent can still act on it.
    """
    properties = (input_schema or {}).get("properties") or {}
    enum_fields: list[dict] = []
    for field, shape in properties.items():
        items_enum = (shape.get("items") or {}).get("enum") if shape.get("type") == "array" else None
        if items_enum:
            enum_fields.append({"field": field, "enum": list(items_enum), "on": "items"})
        elif shape.get("enum"):
            enum_fields.append({"field": field, "enum": list(shape["enum"]), "on": "field"})
    notes: list[str] = []
    if all(name in properties for name in ("images", "image_roles", "video_refs")):
        notes.append("images/image_roles and video_refs are alternative input modes")
    return {"enum_fields": enum_fields,
            "required": list((input_schema or {}).get("required") or []),
            "notes": notes}


# -- the ops ------------------------------------------------------------------


class CanvasOps:
    """The ten canvas tools plus the lock action pair they are wrapped in.

    `client` is a scope-gated `BeehiveClient` (the canvas routes need the
    workflows:read / workflows:write scopes granted to the skill token).
    `session_id` is the agent session id and doubles as the lock holder id:
    the lock contract's crash recovery checks holder liveness, so the holder
    must be the thing that actually dies when the agent dies.
    """

    def __init__(self, client, session_id: str):
        self.client = client
        self.session_id = session_id

    # -- transport ----------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None,
              ok: tuple[int, ...] = (200, 201)) -> dict:
        """request + payload unwrap, refusing loudly on unexpected status."""
        status, resp = self.client.request(method, path, body)
        payload = resp.get("payload", resp) if isinstance(resp, dict) else resp
        if status not in ok:
            raise BeehiveError(f"{method.upper()} {path} failed: HTTP {status} "
                               f"{json.dumps(resp, ensure_ascii=False)[:300]}")
        return payload

    def get_workflow(self, workflow_id: str) -> dict:
        return self._call("GET", f"/api/v1/workflows/{workflow_id}")

    # -- board reads (read-only, no lock needed) -------------------------------

    def list_workflows(self, limit: int = 50) -> dict:
        """The boards this account can see (id, name, description, updated)."""
        payload = self._call("GET", f"/api/v1/workflows?limit={int(limit)}")
        workflows = payload.get("workflows") if isinstance(payload, dict) else payload
        fields = ("id", "name", "description", "updated_at")
        return {"workflows": [{field: wf.get(field) for field in fields}
                              for wf in workflows or []]}

    def read_board(self, workflow_id: str) -> dict:
        """One board's full state: nodes, edges derived from material_deps /
        depends_on, and the media pool -- the read-side picture the agent (and
        the desktop canvas) renders."""
        workflow = self.get_workflow(workflow_id)
        nodes = workflow.get("nodes") or []
        edges: list[dict] = []
        for node in nodes:
            for dep in node.get("depends_on") or []:
                edges.append({"from": dep, "to": node.get("key"), "kind": "dep"})
            for dep in (node.get("config") or {}).get("material_deps") or []:
                edges.append({"from": dep.get("key"), "to": node.get("key"),
                              "kind": "material", "input_port": dep.get("input_port", "")})
        return {"id": workflow.get("id"), "name": workflow.get("name"),
                "nodes": nodes, "edges": edges,
                "media_pool": workflow.get("media_pool") or []}

    # -- lock discipline ------------------------------------------------------

    def lock_workflow(self, workflow_id: str) -> dict:
        """Acquire the server-side write lock as this agent session."""
        status, resp = self.client.request(
            "POST", f"/api/v1/workflows/{workflow_id}/lock",
            {"holder": {"kind": "agent-session", "id": self.session_id}})
        payload = resp.get("payload", resp) if isinstance(resp, dict) else resp
        if status == 200:
            return payload
        if status == 409:
            held_by = {}
            if isinstance(payload, dict):
                held_by = payload.get("held_by") or {}
            elif isinstance(resp, dict):
                held_by = resp.get("held_by") or {}
            raise LockHeldError(workflow_id, held_by)
        raise BeehiveError(f"POST /api/v1/workflows/{workflow_id}/lock failed: HTTP {status} "
                           f"{json.dumps(resp, ensure_ascii=False)[:300]}")

    def unlock_workflow(self, workflow_id: str) -> dict:
        """Release the lock. Idempotent by contract (200 on already-unlocked):
        a replay after crash recovery must not fail."""
        status, resp = self.client.request(
            "POST", f"/api/v1/workflows/{workflow_id}/unlock",
            {"holder_id": self.session_id})
        if status not in (200, 201):
            raise BeehiveError(f"POST /api/v1/workflows/{workflow_id}/unlock failed: "
                               f"HTTP {status} {json.dumps(resp, ensure_ascii=False)[:300]}")
        return resp.get("payload", resp) if isinstance(resp, dict) else resp

    @contextmanager
    def locked(self, workflow_id: str):
        """lock -> read -> (caller mutates) -> unlock, with finally semantics:
        a raising write still releases the lock -- a held lock after a failed
        write is exactly the deadlock the contract's crash path exists for."""
        self.lock_workflow(workflow_id)
        try:
            yield self.get_workflow(workflow_id)
        finally:
            self.unlock_workflow(workflow_id)

    def _locked_write(self, workflow_id: str, mutate: Callable[[dict], None]) -> dict:
        """The write path every mutating tool shares.

        `mutate` edits the workflow dict in place (nodes / node config); it must
        be re-appliable to a fresh read, because a 409 from PUT means someone
        wrote between our GET and our PUT -- we re-GET and re-apply once. A
        second 409 is a real conflict and surfaces loudly.
        """
        with self.locked(workflow_id) as workflow:
            return self._put_mutated(workflow_id, mutate, workflow)

    def _put_mutated(self, workflow_id: str, mutate: Callable[[dict], None],
                     workflow: dict | None = None) -> dict:
        current = workflow
        for attempt in (0, 1):
            if current is None:
                current = self.get_workflow(workflow_id)
            mutated = copy.deepcopy(current)
            mutate(mutated)
            status, resp = self.client.request(
                "PUT", f"/api/v1/workflows/{workflow_id}", _put_body(mutated))
            if status in (200, 201):
                return resp.get("payload", resp) if isinstance(resp, dict) else resp
            if status == 409 and attempt == 0:
                current = None            # concurrent write: re-read, re-apply
                continue
            raise BeehiveError(f"PUT /api/v1/workflows/{workflow_id} failed: HTTP {status} "
                               f"{json.dumps(resp, ensure_ascii=False)[:300]}")
        raise BeehiveError(f"PUT /api/v1/workflows/{workflow_id}: retried after 409 and "
                           f"conflicted again")   # pragma: no cover - loop always returns or raises

    # -- 1. create_workflow ---------------------------------------------------

    def create_workflow(self, name: str, description: str) -> dict:
        """POST a fresh, empty workflow; the response payload carries its id."""
        return self._call("POST", "/api/v1/workflows",
                          {"name": name, "description": description, "nodes": []})

    # -- 2. create_node -------------------------------------------------------

    def create_node(self, workflow_id: str, node_type: str, provider: str, key: str | None = None,
                    config: dict | None = None, position: dict | None = None,
                    depends_on: list[str] | None = None) -> dict:
        """Append a NodeConfig to the blueprint under the lock.

        Auto-key is `{type}-{provider}-{n}` (n = existing node count + 1) and
        auto-position spreads nodes horizontally (x = 80 + 240*i, y = 80) so an
        untouched board stays readable without a layout pass.
        """
        created: dict = {}

        def mutate(wf: dict) -> None:
            nodes = wf.setdefault("nodes", [])
            node = {"type": node_type, "provider": provider,
                    "key": key or f"{node_type}-{provider}-{len(nodes) + 1}",
                    "config": copy.deepcopy(config or {}),
                    "position": dict(position) if position
                    else {"x": 80 + 240 * len(nodes), "y": 80}}
            if depends_on is not None:
                node["depends_on"] = list(depends_on)
            nodes.append(node)
            created["node"] = node

        updated = self._locked_write(workflow_id, mutate)
        return {"node": created["node"], "workflow": updated}

    # -- 3. write_node_config ---------------------------------------------------

    def write_node_config(self, workflow_id: str, key: str, config: dict) -> dict:
        """Deep-merge `config` into the node's config (new keys added, scalars
        overwritten, nested dicts merged) -- a partial edit must not need the
        caller to restate the whole config."""
        def mutate(wf: dict) -> None:
            node = _find_node(wf, key)
            _deep_merge(node.setdefault("config", {}), copy.deepcopy(config or {}))

        updated = self._locked_write(workflow_id, mutate)
        return {"key": key, "workflow": updated}

    # -- 4. connect_ports -------------------------------------------------------

    def connect_ports(self, workflow_id: str, from_key: str, to_key: str,
                      input_port: str | None = None) -> dict:
        """Wire an edge, material or plain.

        A material node (type 'material' with config.pool_entry_id) wires as
        beehive material wiring: a material_deps entry `{key, input_port}` on
        the consumer plus a depends_on edge. Any other node wires as a plain
        DAG edge (depends_on only). Image materials only accept the valid
        image ports -- validated here, before the PUT, with the port names in
        the error (the server enforces the same rule one step later and with
        less context).
        """
        def mutate(wf: dict) -> None:
            from_node = _find_node(wf, from_key)
            to_node = _find_node(wf, to_key)
            is_material = (from_node.get("type") == "material"
                           and bool((from_node.get("config") or {}).get("pool_entry_id")))
            if is_material and input_port is not None:
                if _material_kind(wf, from_node) == "image" and input_port not in VALID_IMAGE_PORTS:
                    raise ValueError(
                        f"input_port {input_port!r} is not a valid image port for material "
                        f"{from_key!r}; valid image ports: {', '.join(VALID_IMAGE_PORTS)}. "
                        f"Call query_schema first -- the platform rejects any other port.")
            if is_material:
                deps = to_node.setdefault("config", {}).setdefault("material_deps", [])
                port = input_port or ""
                if not any(d.get("key") == from_key and d.get("input_port") == port for d in deps):
                    deps.append({"key": from_key, "input_port": port})
            depends = to_node.setdefault("depends_on", [])
            if from_key not in depends:
                depends.append(from_key)

        updated = self._locked_write(workflow_id, mutate)
        return {"from": from_key, "to": to_key, "workflow": updated}

    # -- 5. query_schema ---------------------------------------------------------

    def query_schema(self, node_type: str | None = None, provider: str | None = None,
                     node_id: str | None = None) -> dict:
        """Read a node definition plus the derived port analysis.

        The port analysis exists so the agent can read the input_schema enums
        and required fields *before* connecting (FR-3 institutionalizes the
        lanes 400 lesson: connect must be preceded by query_schema).
        """
        target = node_id or (f"{node_type}:{provider}" if node_type and provider else None)
        if not target:
            raise ValueError("query_schema needs either node_id, or both node_type "
                             "and provider (node id is '{node_type}:{provider}')")
        payload = self._call("GET", "/api/v1/nodes")
        definitions = payload.get("nodes") if isinstance(payload, dict) else payload
        for definition in definitions or []:
            if definition.get("id") == target:
                return {"node_id": definition.get("id"),
                        "name": definition.get("name"),
                        "description": definition.get("description"),
                        "input_schema": definition.get("input_schema") or {},
                        "output_schema": definition.get("output_schema") or {},
                        "config_defaults": definition.get("config_defaults") or {},
                        "enabled": definition.get("enabled", True),
                        "port_analysis": _port_analysis(definition.get("input_schema") or {})}
        ids = [str(d.get("id")) for d in definitions or []]
        listing = ", ".join(ids[:NODE_ID_LIST_LIMIT]) + (" ..." if len(ids) > NODE_ID_LIST_LIMIT else "")
        raise ValueError(f"no node definition {target!r} (available: {listing or 'none'})")

    # -- 6. read_node_output ------------------------------------------------------

    def read_node_output(self, workflow_id: str, key: str) -> dict:
        """Newest job output for one node of this workflow.

        Job nodes match on `key`; a keyless single-node payload can only be
        the node that was submitted, so it matches any requested key
        (submit_node_job always sends exactly one node). Keyless multi-node
        payloads are skipped rather than guessed at.
        """
        payload = self._call("GET", "/api/v1/jobs?limit=50")
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        jobs = [job for job in (jobs or []) if job.get("workflow_id") == workflow_id]
        for job in _newest_first(jobs):
            job_nodes = job.get("nodes") or []
            if any(node.get("key") for node in job_nodes):
                match = next((n for n in job_nodes if n.get("key") == key), None)
            else:
                match = job_nodes[0] if len(job_nodes) == 1 else None
            if match is not None:
                return {"found": True, "job_id": job.get("id"), "status": job.get("status"),
                        "node": {"key": match.get("key") or key, "status": match.get("status"),
                                 "output": match.get("output")}}
        return {"found": False}

    # -- 7/8. submission (quote-first) ---------------------------------------------

    def submit_node_job(self, workflow_id: str, node_type: str, provider: str, config: dict,
                        key: str | None = None, quote_first: bool = True) -> dict:
        """Submit one node as a job (quote first by default: a paid submission
        is estimated before it exists, never after).

        ``execution_mode: "node"`` is load-bearing: with a workflow_id and the
        default "workflow" mode the server MERGES the blueprint's sibling nodes
        into the job (mergeConfigs template semantics) -- a single-node run
        would inherit every sibling's config and the wrong node's provider.
        The "node" mode executes exactly the caller's node (BEE-151), matched
        by key against the stored workflow when a key is given.
        """
        node = {"type": node_type, "provider": provider, "config": config or {}}
        if key:
            node["key"] = key
        return self._submit_nodes(workflow_id, [node], quote_first=quote_first,
                                  execution_mode="node")

    def run_workflow(self, workflow_id: str, quote_first: bool = True) -> dict:
        """Submit every node of the blueprint as one job, verbatim -- the
        workflow is the plan of record, so the job is a faithful copy of it,
        not a re-interpretation."""
        workflow = self.get_workflow(workflow_id)
        nodes = workflow.get("nodes") or []
        if not nodes:
            raise ValueError(f"workflow {workflow_id} has no nodes to run")
        result = self._submit_nodes(workflow_id, nodes, quote_first=quote_first)
        result["node_count"] = len(nodes)
        return result

    def _submit_nodes(self, workflow_id: str, nodes: list[dict], quote_first: bool = True,
                      execution_mode: str = "workflow") -> dict:
        result: dict = {"quoted": False}
        if quote_first:
            quote = self._call("POST", "/api/v1/billing/quote", {"nodes": nodes})
            result["quoted"] = True
            result["quote"] = _quote_view(quote)
        body = {"nodes": nodes, "workflow_id": workflow_id}
        if execution_mode:
            body["execution_mode"] = execution_mode
        job = self._call("POST", "/api/v1/jobs", body)
        # The create endpoint answers {job_id, status, is_new} (unlike GET
        # /jobs/{id}, whose rows carry `id`) -- accept both spellings.
        result["job_id"] = job.get("job_id") or job.get("id")
        result["status"] = job.get("status")
        return result

    # -- 9. generate_image ----------------------------------------------------------

    def generate_image(self, workflow_id: str, prompt: str, model: str = "gpt-image-2",
                       size: str = "1024x1024") -> dict:
        """Submit a PAID image generation node (always quote-first).

        This only submits: the output lands in the workflow media pool
        automatically via server auto-capture (origin.kind=generated) once the
        job completes -- the caller polls, this tool does not block.
        """
        provider = IMAGE_MODELS.get(model)
        if provider is None:
            raise ValueError(f"unknown image model {model!r} "
                             f"(supported: {', '.join(sorted(IMAGE_MODELS))})")
        result = self.submit_node_job(workflow_id, "generate", provider,
                                      {"prompt": prompt, "size": size}, quote_first=True)
        result["note"] = ("image generation is async: poll the job with beehive_get_job "
                          f"(job_id={result.get('job_id')!r}) or read_node_output "
                          f"(workflow_id={workflow_id!r}); when it completes the server "
                          "auto-captures the image into the workflow media pool "
                          "(origin.kind=generated)")
        return result

    # -- 10/11. media pool -------------------------------------------------------------

    def list_media(self, workflow_id: str) -> dict:
        """The workflow's media pool entries (id, name, kind, url, thumb)."""
        workflow = self.get_workflow(workflow_id)
        fields = ("id", "name", "kind", "url", "thumb")
        return {"entries": [{field: entry.get(field) for field in fields}
                            for entry in workflow.get("media_pool") or []]}

    def add_media(self, workflow_id: str, url: str, name: str, kind: str = "image",
                  mime: str = "image/png") -> dict:
        """Add an upload-origin entry to the media pool.

        The server generates id and created_at; the new entry is identified in
        the returned pool by url (the one field the caller actually knows).
        """
        entry = {"url": url, "name": name, "kind": kind, "mime_type": mime,
                 "origin": {"kind": "upload"}}
        payload = self._call("POST", f"/api/v1/workflows/{workflow_id}/media-pool", entry)
        pool = payload.get("media_pool") if isinstance(payload, dict) else None
        if pool is None:
            # the endpoint did not hand back the updated workflow: read it
            pool = self.get_workflow(workflow_id).get("media_pool") or []
        return {"added": next((e for e in pool if e.get("url") == url), None),
                "entries": pool}
