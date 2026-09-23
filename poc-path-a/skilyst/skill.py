"""Community-spec validation + sidecar manifest (`manifest.json`) handling.

Two load layers, per the Skilyst manifest draft v0.1:
  * community layer -- SKILL.md frontmatter rules from agentskills.io;
  * extension layer  -- sidecar manifest.json with the structured fields
    (identity/lineage/supply-chain/permission/requires/i18n/attribution/
    compatibility/update).

A package with no manifest.json is loadable in DEGRADED mode
(upstream=null, minimal sandbox, no node requires) -- community skills are
never rejected for lacking our extension.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .frontmatter import FrontmatterError, parse_frontmatter

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
KNOWN_FRONTMATTER = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}

DEGRADED_PERMISSION = {"egress": "none", "filesystem": "skill-dir", "exec": "none", "secrets": False}


class SkillValidationError(ValueError):
    """Raised for anything that must NOT be silently accepted."""


@dataclass
class CommunityMeta:
    name: str
    description: str
    license: str | None
    compatibility: str | None
    metadata: dict[str, str]
    allowed_tools: str | None
    raw: dict = field(default_factory=dict)


@dataclass
class NodeRequirement:
    provider: str
    node_type: str
    version_range: str
    optional: bool = False
    fallback: list[str] = field(default_factory=list)


@dataclass
class SkillPackage:
    """A loaded skill: community metadata + resolved extension (or degraded)."""

    dir: Path
    community: CommunityMeta
    manifest: dict | None
    digest: str
    body: str
    warnings: tuple[str, ...] = ()

    @property
    def skill_id(self) -> str:
        if self.manifest:
            return self.manifest["skill_id"]
        return self.community.name

    @property
    def version(self) -> str:
        if self.manifest:
            return self.manifest["version"]
        return self.community.metadata.get("skilyst.version", "0.0.0+community")

    @property
    def degraded(self) -> bool:
        return self.manifest is None

    @property
    def permission(self) -> dict:
        return (self.manifest or {}).get("permission", DEGRADED_PERMISSION)

    @property
    def requires_nodes(self) -> list[NodeRequirement]:
        nodes = ((self.manifest or {}).get("requires") or {}).get("nodes") or []
        return [NodeRequirement(n["provider"], n["node_type"], n["node_definition_version"],
                                bool(n.get("optional", False)), list(n.get("fallback") or [])) for n in nodes]

    @property
    def update(self) -> dict:
        return (self.manifest or {}).get("update", {})


def validate_community(meta: CommunityMeta, dir_name: str, strict: bool = False) -> list[str]:
    """Community-layer rules. Returns warnings; raises for anything unusable.

    `strict=True` is applied to packages we publish (they carry manifest.json).
    Plain community packages are loaded in COMPAT mode: the hard constraint is
    that a community package must load WITHOUT MODIFICATION, and the community
    reality already deviates from the spec on day one -- e.g. the repo
    Emily2040/seedance-2.0 ships `name: seedance-20` inside a directory named
    `seedance-2.0` (dots are not even legal in `name`). Rejecting it would force
    us to edit third-party files, which is exactly what the format decision
    forbids. So spec deviations become recorded warnings, not load failures.
    """
    warnings: list[str] = []

    def fail_or_warn(msg: str) -> None:
        if strict:
            raise SkillValidationError(msg)
        warnings.append(msg)

    if not meta.name:
        raise SkillValidationError("frontmatter `name` is required")
    if not NAME_RE.match(meta.name):
        fail_or_warn(f"`name` {meta.name!r} is not spec-clean kebab-case "
                     f"(lowercase alnum + single hyphens) -- loaded in compat mode")
    if len(meta.name) > 64:
        fail_or_warn(f"`name` must be <=64 chars, got {len(meta.name)}")
    if meta.name != dir_name:
        fail_or_warn(f"`name` {meta.name!r} != directory name {dir_name!r} (spec requires equality) "
                     f"-- skill_id falls back to the directory name")
    if not meta.description:
        raise SkillValidationError("frontmatter `description` is required")
    if len(meta.description) > 1024:
        fail_or_warn(f"`description` must be <=1024 chars, got {len(meta.description)}")
    if meta.compatibility is not None and len(meta.compatibility) > 500:
        fail_or_warn("`compatibility` must be <=500 chars")
    for k, v in meta.metadata.items():
        if not isinstance(v, str):
            raise SkillValidationError(f"`metadata.{k}` must be a string (spec: string->string map); "
                                       f"got {type(v).__name__} -- structured data belongs in manifest.json")
    return warnings


def validate_manifest(m: dict) -> None:
    required = ["skill_id", "version", "description", "kind", "content_digest", "upstream",
                "supply_chain", "permission", "i18n", "author", "license", "compatibility", "update"]
    for key in required:
        if key not in m:
            raise SkillValidationError(f"manifest.{key} is required (upstream: null declares 'original', "
                                       f"omitting the field is invalid)")
    if not NAME_RE.match(m["skill_id"].split("/")[-1]):
        raise SkillValidationError(f"manifest.skill_id {m['skill_id']!r} short name must be kebab-case")
    if not SEMVER_RE.match(m["version"]):
        raise SkillValidationError(f"manifest.version {m['version']!r} must be SemVer 2.0.0")
    if m["kind"] not in ("knowledge", "methodology", "official-bundle"):
        raise SkillValidationError(f"manifest.kind {m['kind']!r} not in knowledge|methodology|official-bundle")

    perm = m["permission"]
    for k in ("egress", "filesystem", "exec", "secrets"):
        if k not in perm:
            raise SkillValidationError(f"manifest.permission.{k} is required")
    if perm["filesystem"] not in ("none", "skill-dir", "workspace"):
        raise SkillValidationError(f"permission.filesystem {perm['filesystem']!r} invalid")
    if not isinstance(perm["secrets"], bool):
        raise SkillValidationError("permission.secrets must be boolean")
    if isinstance(perm["exec"], str) and perm["exec"] not in ("none", "scripts-whitelist"):
        raise SkillValidationError(f"permission.exec {perm['exec']!r} invalid")
    if isinstance(perm["egress"], str) and perm["egress"] not in ("none",):
        raise SkillValidationError(f"permission.egress {perm['egress']!r} invalid (use `none` or a list of hosts)")

    nodes = ((m.get("requires") or {}).get("nodes")) or []
    if m["kind"] == "knowledge" and nodes:
        raise SkillValidationError("kind=knowledge must declare requires.nodes = []")
    if m["kind"] in ("methodology", "official-bundle") and "requires" not in m:
        raise SkillValidationError(f"kind={m['kind']} must declare requires (the node dependency block)")
    for n in nodes:
        for k in ("provider", "node_type", "node_definition_version"):
            if k not in n:
                raise SkillValidationError(f"requires.nodes[] entry missing {k}")

    up = m["upstream"]
    if up is not None:
        for k in ("skill_id", "version"):
            if k not in up:
                raise SkillValidationError(f"upstream non-null requires upstream.{k}")

    hot = (m.get("update") or {}).get("hot_reload")
    if not hot or "node-schema" not in (hot.get("exclusions") or []):
        raise SkillValidationError("update.hot_reload.exclusions must always contain \"node-schema\" "
                                   "(node input_schema changes can never be hot-updated)")
    if (m.get("update") or {}).get("channel") not in ("official", "community"):
        raise SkillValidationError("update.channel must be official|community")
    if (m.get("update") or {}).get("policy") not in ("auto", "manual", "pinned"):
        raise SkillValidationError("update.policy must be auto|manual|pinned")

    locales = ((m.get("i18n") or {}).get("locales")) or []
    if "zh-CN" not in locales or "en" not in locales:
        raise SkillValidationError("i18n.locales must contain zh-CN and en (works-wall listing gate)")


def is_hot_update(old: dict | None, new: dict | None) -> tuple[bool, str]:
    """Machine-checkable hot-update rule (manifest draft 6.3 rule 2).

    Any change touching requires.nodes[].node_definition_version -- or any other
    node-schema surface -- is a COLD update even when hot_reload.allowed=true.
    """
    if not old or not new:
        return False, "no previous manifest: cold"
    if not (new.get("update", {}).get("hot_reload", {}) or {}).get("allowed", False):
        return False, "manifest declares hot_reload.allowed=false"
    old_reqs = [(n["node_type"], n["node_definition_version"]) for n in (old.get("requires") or {}).get("nodes", [])]
    new_reqs = [(n["node_type"], n["node_definition_version"]) for n in (new.get("requires") or {}).get("nodes", [])]
    if old_reqs != new_reqs:
        return False, "requires.nodes[].node_definition_version changed -> node-schema exclusion forces cold update"
    if (old.get("permission") or {}) != (new.get("permission") or {}):
        return False, "permission declaration changed -> requires re-consent (cold)"
    return True, "instruction-content change within allowed hot-update surface"


def load_package(skill_dir: Path, digest: str) -> SkillPackage:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillValidationError(f"{skill_dir} has no SKILL.md")
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    try:
        raw = parse_frontmatter(text)
    except FrontmatterError as exc:
        raise SkillValidationError(f"{skill_dir}/SKILL.md frontmatter: {exc}") from exc

    known = {k: v for k, v in raw.items() if k in KNOWN_FRONTMATTER}
    meta_raw = known.get("metadata") or {}
    if not isinstance(meta_raw, dict):
        raise SkillValidationError("frontmatter `metadata` must be a mapping")
    meta = CommunityMeta(
        name=str(known.get("name") or "").strip(),
        description=str(known.get("description") or "").strip(),
        license=known.get("license"),
        compatibility=known.get("compatibility"),
        metadata={k: str(v) for k, v in meta_raw.items()},
        allowed_tools=known.get("allowed-tools"),
        raw=raw,
    )
    sidecar = skill_dir / "manifest.json"
    strict = sidecar.is_file()          # our own published skills must be spec-clean
    warnings = tuple(validate_community(meta, skill_dir.name, strict=strict))

    body = text.split("\n---", 1)[1] if "\n---" in text else ""
    manifest = None
    if strict:
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        validate_manifest(manifest)
        short = manifest["skill_id"].split("/")[-1]
        if short != meta.name:
            raise SkillValidationError(
                f"SKILL.md name {meta.name!r} != manifest.skill_id short name {short!r} (consistency rule)")
        if manifest["description"].strip() != meta.description.strip():
            raise SkillValidationError("manifest.description must be verbatim-equal to SKILL.md description")
    return SkillPackage(dir=skill_dir, community=meta, manifest=manifest, digest=digest, body=body,
                        warnings=warnings)
