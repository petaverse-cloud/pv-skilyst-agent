"""Manifest sidecar spec (`manifest.json`) -- validation + hot/cold update rules.

Extension layer of the dual-file scheme: SKILL.md frontmatter stays the
community subset (agentskills.io), the sidecar carries the structured blocks
(identity / lineage / supply chain / permission / node requires / i18n /
attribution / compatibility / update).

Two semantics that T1 got wrong on contact with the live platform registry and
that are fixed here (manifest v0.2):

  * ``requires.nodes[].node_id`` -- the *node definition* identifier as the
    platform registry names it (``generate:minimax-h3``). v0.1 called this field
    ``node_type``, which collides with the registry field of that name meaning
    the *workflow node type* (``generate``). Requirement matching therefore
    happens on registry ``id`` only; the legacy spelling is still read (so
    already-published v0.1 packages keep loading) but is recorded as a
    deprecation warning on the package.

  * ``content_digest`` -- the digest of the package *content*, which by
    definition excludes ``manifest.json`` itself (otherwise the field is
    self-referential and can never be written). Mutation of any file, including
    ``manifest.json``, is caught by the store's install receipt instead
    (see ``skills.store``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from errors import ManifestError

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*:[A-Za-z0-9][A-Za-z0-9._-]*$")

REQUIRED_KEYS = ("skill_id", "version", "description", "kind", "content_digest", "upstream",
                 "supply_chain", "permission", "i18n", "author", "license", "compatibility", "update")
KINDS = ("knowledge", "methodology", "official-bundle")
FS_MODES = ("none", "skill-dir", "workspace")
EXEC_MODES = ("none", "scripts-whitelist")
CHANNELS = ("official", "community")
POLICIES = ("auto", "manual", "pinned")
# `requires.nodes[]` field spellings: v0.2 name first, v0.1 name second.
NODE_ID_KEYS = ("node_id", "node_type")
# Tool arguments the runtime owns. A binding must not map them into node config:
# they steer the *call* (which node, whether to block, how long), and letting a
# manifest route them into a node field would let a package click its own controls.
BINDING_CONTROL_ARGS = ("node_id", "workflow_id", "wait", "timeout_s")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# v0.3 workflow_skeleton (skills-as-product-core.md §3.2): freedom tiers.
SKELETON_FREEDOMS = ("pinned", "parameterized", "free")
# A skeleton node_id may pin an exact definition ("generate:minimax-h3") or a
# wildcard family ("generate:*"). The wildcard only means "some variant in this
# family" -- it must still be a legal node-id shape.
SKELETON_NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*:(\*|[A-Za-z0-9][A-Za-z0-9._*-]*)$")


@dataclass
class NodeBinding:
    """How a skill's method is called, in the manifest (v0.2 `requires.nodes[].binding`).

    ``config_map`` maps a *tool argument* to the *node input field* it must be sent
    as (``{"ratio": "aspect_ratio"}`` for Nano Banana 2). That mapping is the whole
    point: without it the runtime would have to guess field names, and a guess that
    silently drops an argument is how an image-to-video call becomes a paid
    text-to-video render.
    """

    tool: str
    node_id: str
    config_map: dict[str, str]

    def tool_args(self) -> list[str]:
        return sorted(self.config_map)

    def api_field(self, argument: str) -> str:
        return self.config_map[argument]

    def argument_for(self, api_field: str) -> str | None:
        """Reverse lookup: which tool argument feeds this node field."""
        for argument, field in self.config_map.items():
            if field == api_field:
                return argument
        return None


def parse_node_binding(raw: object, node_id: str) -> NodeBinding:
    """Validate one `binding` block. Absent is allowed (v0.1 packages); malformed is not."""
    if not isinstance(raw, dict):
        raise ManifestError(f"requires.nodes[{node_id}].binding must be an object")
    tool = str(raw.get("tool") or "")
    if not tool:
        raise ManifestError(f"requires.nodes[{node_id}].binding.tool is required "
                            f"(the runtime tool that calls this node)")
    declared_node = str(raw.get("node_id") or node_id)
    if declared_node != node_id:
        raise ManifestError(f"requires.nodes[{node_id}].binding.node_id {declared_node!r} does not "
                            f"match the entry's node_id -- the binding must bind this node")
    config_map = raw.get("config_map")
    if not isinstance(config_map, dict) or not config_map:
        raise ManifestError(f"requires.nodes[{node_id}].binding.config_map must be a non-empty object "
                            f"mapping tool argument -> node input field")
    cleaned: dict[str, str] = {}
    for argument, field in config_map.items():
        argument, field = str(argument), str(field)
        if not FIELD_NAME_RE.match(argument) or not FIELD_NAME_RE.match(field):
            raise ManifestError(f"requires.nodes[{node_id}].binding.config_map entry "
                                f"{argument!r}: {field!r} must be snake_case identifiers")
        if argument in BINDING_CONTROL_ARGS:
            raise ManifestError(f"requires.nodes[{node_id}].binding.config_map maps the runtime control "
                                f"argument {argument!r}; control arguments ({', '.join(BINDING_CONTROL_ARGS)}) "
                                f"are not node fields")
        if field in cleaned.values():
            raise ManifestError(f"requires.nodes[{node_id}].binding.config_map maps two arguments to the "
                                f"same node field {field!r} -- the forward direction would be ambiguous")
        cleaned[argument] = field
    return NodeBinding(tool=tool, node_id=node_id, config_map=cleaned)


@dataclass
class NodeRequirement:
    """One node-definition dependency of a skill.

    ``node_id`` is the registry's node *definition* id (``generate:minimax-h3``);
    ``node_type`` is its derived workflow node type (``generate``), kept only so
    a registry entry can be cross-checked.
    """

    node_id: str
    version_range: str
    optional: bool = False
    fallback: list[str] = field(default_factory=list)
    legacy_field: bool = False
    binding: NodeBinding | None = None

    @property
    def node_type(self) -> str:
        return self.node_id.split(":", 1)[0]


def parse_node_requirement(raw: dict) -> NodeRequirement:
    present = [k for k in NODE_ID_KEYS if k in raw]
    if not present:
        raise ManifestError("requires.nodes[] entry missing node_id "
                            "(v0.1 packages may still declare it as node_type)")
    values = {k: str(raw[k]) for k in present}
    if len(set(values.values())) > 1:
        raise ManifestError(f"requires.nodes[] entry declares conflicting node_id/node_type: {values}")
    key = present[0]
    node_id = values[key]
    if not NODE_ID_RE.match(node_id):
        raise ManifestError(f"requires.nodes[].{key} {node_id!r} must be a node-definition id "
                            f"of the form <node_type>:<variant>, e.g. generate:minimax-h3")
    if "node_definition_version" not in raw:
        raise ManifestError("requires.nodes[] entry missing node_definition_version")
    fallback = raw.get("fallback") or []
    if not isinstance(fallback, list):
        raise ManifestError("requires.nodes[].fallback must be a list of node ids")
    for fb in fallback:
        if not NODE_ID_RE.match(str(fb)):
            raise ManifestError(f"requires.nodes[].fallback entry {fb!r} is not a node-definition id")
    return NodeRequirement(node_id=node_id,
                           version_range=str(raw["node_definition_version"]),
                           optional=bool(raw.get("optional", False)),
                           fallback=[str(f) for f in fallback],
                           legacy_field=(key == "node_type"),
                           binding=parse_node_binding(raw["binding"], node_id) if "binding" in raw else None)


@dataclass
class SkeletonNode:
    """One node slot in a v0.3 workflow_skeleton.

    ``node_id`` is a definition id or a wildcard family (``generate:*``);
    ``freedom`` is the tier the materializing agent gets: pinned (structure and
    binding untouchable), parameterized (config keys in ``config_open`` only),
    free (agent may add/remove/reconnect within its zone). ``role`` is the
    author's semantic label (e.g. hero_shot).
    """

    node_id: str
    freedom: str
    role: str = ""
    config_open: list[str] = field(default_factory=list)

    @property
    def node_type(self) -> str:
        return self.node_id.split(":", 1)[0]


@dataclass
class SkeletonFreeZone:
    """A region where the materializing agent may add nodes (v0.3 §3.2)."""

    name: str
    max_nodes: int
    allowed_node_types: list[str] = field(default_factory=list)


@dataclass
class WorkflowSkeleton:
    """The v0.3 manifest extension: author-drawn structure + freedom tiers.

    Parsed and validated at load time (structure-level, no registry needed);
    materialization-time comparison lives in skills.store preflight.
    """

    version: int
    nodes: list[SkeletonNode] = field(default_factory=list)
    free_zones: list[SkeletonFreeZone] = field(default_factory=list)
    reference_workflow: dict = field(default_factory=dict)

    def matching_node(self, node_id: str) -> SkeletonNode | None:
        """The most specific skeleton slot a materialized node matches."""
        exact = next((n for n in self.nodes if n.node_id == node_id), None)
        if exact:
            return exact
        family = node_id.split(":", 1)[0] + ":*"
        return next((n for n in self.nodes if n.node_id == family), None)


def parse_skeleton(raw: dict) -> WorkflowSkeleton:
    """Parse + validate the manifest's workflow_skeleton block.

    Raises ManifestError with an actionable message (iron rule 7: loud,
    structured, not prose) on any structural violation.
    """
    if not isinstance(raw, dict):
        raise ManifestError("workflow_skeleton must be an object")
    version = raw.get("version")
    if version != 1:
        raise ManifestError(f"workflow_skeleton.version must be 1 (v0.3), got {version!r}")

    nodes: list[SkeletonNode] = []
    seen_ids: set[str] = set()
    for i, n in enumerate(raw.get("nodes") or []):
        if not isinstance(n, dict):
            raise ManifestError(f"workflow_skeleton.nodes[{i}] must be an object")
        node_id = n.get("node_id")
        if not isinstance(node_id, str) or not SKELETON_NODE_ID_RE.match(node_id):
            raise ManifestError(f"workflow_skeleton.nodes[{i}].node_id {node_id!r} must be a node "
                                f"definition id or family wildcard, e.g. generate:minimax-h3 / generate:*")
        if node_id in seen_ids:
            raise ManifestError(f"workflow_skeleton.nodes[{i}].node_id {node_id!r} is declared twice")
        seen_ids.add(node_id)
        freedom = n.get("freedom")
        if freedom not in SKELETON_FREEDOMS:
            raise ManifestError(f"workflow_skeleton.nodes[{i}].freedom must be one of "
                                f"{'|'.join(SKELETON_FREEDOMS)}, got {freedom!r}")
        config_open = n.get("config_open") or []
        if freedom == "pinned" and config_open:
            raise ManifestError(f"workflow_skeleton.nodes[{i}] ({node_id}) is pinned but declares "
                                f"config_open {config_open} -- pinned means the config is NOT open")
        if freedom == "parameterized":
            if not config_open:
                raise ManifestError(f"workflow_skeleton.nodes[{i}] ({node_id}) is parameterized but "
                                    f"declares no config_open -- that is what parameterized means")
            for k in config_open:
                if not isinstance(k, str) or not FIELD_NAME_RE.match(k):
                    raise ManifestError(f"workflow_skeleton.nodes[{i}].config_open entry {k!r} must "
                                        f"be a snake_case config field name")
        role = n.get("role") or ""
        if not isinstance(role, str):
            raise ManifestError(f"workflow_skeleton.nodes[{i}].role must be a string label")
        nodes.append(SkeletonNode(node_id=node_id, freedom=freedom, role=role,
                                  config_open=[str(k) for k in config_open]))

    zones: list[SkeletonFreeZone] = []
    seen_zones: set[str] = set()
    for i, z in enumerate(raw.get("free_zones") or []):
        if not isinstance(z, dict):
            raise ManifestError(f"workflow_skeleton.free_zones[{i}] must be an object")
        name = z.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ManifestError(f"workflow_skeleton.free_zones[{i}].name {name!r} must be kebab-case")
        if name in seen_zones:
            raise ManifestError(f"workflow_skeleton.free_zones[{i}].name {name!r} is declared twice")
        seen_zones.add(name)
        max_nodes = z.get("max_nodes")
        if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or max_nodes < 0:
            raise ManifestError(f"workflow_skeleton.free_zones[{i}].max_nodes must be a "
                                f"non-negative integer, got {max_nodes!r}")
        allowed = z.get("allowed_node_types") or []
        for t in allowed:
            if not isinstance(t, str) or not NODE_ID_RE.match(t):
                raise ManifestError(f"workflow_skeleton.free_zones[{i}].allowed_node_types entry "
                                    f"{t!r} is not a node definition id, e.g. process:transcode")
        zones.append(SkeletonFreeZone(name=name, max_nodes=max_nodes,
                                      allowed_node_types=[str(t) for t in allowed]))

    ref = raw.get("reference_workflow") or {}
    if not isinstance(ref, dict):
        raise ManifestError("workflow_skeleton.reference_workflow must be an object "
                            "(snapshot_of + note)")

    return WorkflowSkeleton(version=int(version), nodes=nodes, free_zones=zones,
                            reference_workflow=ref)


def skeleton_of(manifest: dict) -> WorkflowSkeleton | None:
    """The parsed workflow_skeleton of a manifest, or None when undeclared."""
    raw = (manifest or {}).get("workflow_skeleton")
    if raw is None:
        return None
    return parse_skeleton(raw)


def node_requirements(manifest: dict) -> list[NodeRequirement]:
    return [parse_node_requirement(n) for n in ((manifest.get("requires") or {}).get("nodes") or [])]


def validate_manifest(m: dict) -> None:
    for key in REQUIRED_KEYS:
        if key not in m:
            raise ManifestError(f"manifest.{key} is required (upstream: null declares 'original', "
                                f"omitting the field is invalid)")
    if not NAME_RE.match(str(m["skill_id"]).split("/")[-1]):
        raise ManifestError(f"manifest.skill_id {m['skill_id']!r} short name must be kebab-case")
    if not SEMVER_RE.match(str(m["version"])):
        raise ManifestError(f"manifest.version {m['version']!r} must be SemVer 2.0.0")
    if m["kind"] not in KINDS:
        raise ManifestError(f"manifest.kind {m['kind']!r} not in {'|'.join(KINDS)}")
    if not DIGEST_RE.match(str(m["content_digest"])):
        raise ManifestError(f"manifest.content_digest {m['content_digest']!r} must be "
                            f"sha256:<64 hex> over the package content (manifest.json excluded)")

    perm = m["permission"]
    for k in ("egress", "filesystem", "exec", "secrets"):
        if k not in perm:
            raise ManifestError(f"manifest.permission.{k} is required")
    if perm["filesystem"] not in FS_MODES:
        raise ManifestError(f"permission.filesystem {perm['filesystem']!r} invalid")
    if not isinstance(perm["secrets"], bool):
        raise ManifestError("permission.secrets must be boolean")
    if isinstance(perm["exec"], str) and perm["exec"] not in EXEC_MODES:
        raise ManifestError(f"permission.exec {perm['exec']!r} invalid")
    if isinstance(perm["egress"], str) and perm["egress"] not in ("none",):
        raise ManifestError(f"permission.egress {perm['egress']!r} invalid (use `none` or a list of hosts)")

    nodes = ((m.get("requires") or {}).get("nodes")) or []
    if m["kind"] == "knowledge" and nodes:
        raise ManifestError("kind=knowledge must declare requires.nodes = []")
    if m["kind"] in ("methodology", "official-bundle") and "requires" not in m:
        raise ManifestError(f"kind={m['kind']} must declare requires (the node dependency block)")
    for n in nodes:
        parse_node_requirement(n)

    # v0.3 workflow_skeleton: declared means validated. kind=knowledge may not
    # carry one (it is the same node-facing surface requires.nodes = [] forbids).
    if "workflow_skeleton" in m:
        if m["kind"] == "knowledge":
            raise ManifestError("kind=knowledge must not declare workflow_skeleton "
                                "(same rule as requires.nodes = [])")
        parse_skeleton(m["workflow_skeleton"])

    up = m["upstream"]
    if up is not None:
        for k in ("skill_id", "version"):
            if k not in up:
                raise ManifestError(f"upstream non-null requires upstream.{k}")

    hot = (m.get("update") or {}).get("hot_reload")
    if not hot or "node-schema" not in (hot.get("exclusions") or []):
        raise ManifestError("update.hot_reload.exclusions must always contain \"node-schema\" "
                            "(node input_schema changes can never be hot-updated)")
    if (m.get("update") or {}).get("channel") not in CHANNELS:
        raise ManifestError("update.channel must be official|community")
    if (m.get("update") or {}).get("policy") not in POLICIES:
        raise ManifestError("update.policy must be auto|manual|pinned")

    locales = ((m.get("i18n") or {}).get("locales")) or []
    if "zh-CN" not in locales or "en" not in locales:
        raise ManifestError("i18n.locales must contain zh-CN and en (works-wall listing gate)")


def is_hot_update(old: dict | None, new: dict | None) -> tuple[bool, str]:
    """Machine-checkable hot-update rule (manifest draft 6.3 rule 2).

    Any change touching a node-schema surface -- the node ids or their
    node_definition_version ranges -- is a COLD update even when
    hot_reload.allowed=true, because the node input_schema cannot be hot-swapped.
    """
    if not old or not new:
        return False, "no previous manifest: cold"
    if not (new.get("update", {}).get("hot_reload", {}) or {}).get("allowed", False):
        return False, "manifest declares hot_reload.allowed=false"
    old_reqs = [(r.node_id, r.version_range) for r in node_requirements(old)]
    new_reqs = [(r.node_id, r.version_range) for r in node_requirements(new)]
    if old_reqs != new_reqs:
        return False, "requires.nodes[].node_id/node_definition_version changed -> node-schema exclusion forces cold update"
    if (old.get("permission") or {}) != (new.get("permission") or {}):
        return False, "permission declaration changed -> requires re-consent (cold)"
    return True, "instruction-content change within allowed hot-update surface"


def deprecation_warnings(m: dict) -> list[str]:
    """Legacy spellings accepted for already-published packages."""
    out = []
    for n in ((m.get("requires") or {}).get("nodes") or []):
        if "node_type" in n and "node_id" not in n:
            out.append(f"requires.nodes[].node_type is the v0.1 spelling of node_id "
                       f"({n['node_type']!r}) -- republish with node_id; it names the node "
                       f"definition, not the workflow node type")
    return out
