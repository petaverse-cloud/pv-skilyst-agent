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
                           legacy_field=(key == "node_type"))


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
