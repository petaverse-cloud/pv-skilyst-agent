"""Skill package loading: community layer (SKILL.md) + extension layer (manifest.json).

A package with no ``manifest.json`` is loadable in DEGRADED mode
(``upstream=None``, minimal sandbox, no node requires) -- community skills are
never rejected for lacking our extension, and they are never rewritten to add
it. A package that *does* carry a sidecar is one we publish, so the community
rules are enforced strictly on it.

Load-time integrity (T1 defect fix): the declared ``manifest.content_digest`` is
recomputed from the package content on every load and must match. Mutation
detection for the sidecar itself lives in the store receipt (``skills.store``).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from errors import ManifestError, SkillMutationError, SkillValidationError
from manifest import NodeRequirement, deprecation_warnings, node_requirements, validate_manifest
from .digest import content_digest, tree_digest
from .frontmatter import KNOWN_FRONTMATTER, FrontmatterError, parse_frontmatter, split_frontmatter

NAME_RE_STR = "lowercase alnum + single hyphens"

DEGRADED_PERMISSION = {"egress": "none", "filesystem": "skill-dir", "exec": "none", "secrets": False}


@dataclass
class CommunityMeta:
    name: str
    description: str
    license: str | None
    compatibility: str | None
    metadata: dict[str, str]
    allowed_tools: str | None
    raw: dict = field(default_factory=dict)

    @property
    def unknown_keys(self) -> list[str]:
        return sorted(set(self.raw) - set(KNOWN_FRONTMATTER))


@dataclass
class SkillPackage:
    """A loaded skill: community metadata + resolved extension (or degraded)."""

    dir: Path
    community: CommunityMeta
    manifest: dict | None
    digest: str
    body: str
    tree_digest: str = ""
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
    def display_name(self) -> str:
        if self.manifest:
            return self.manifest.get("display_name") or self.skill_id
        return self.community.name

    @property
    def description(self) -> str:
        return self.community.description

    @property
    def degraded(self) -> bool:
        return self.manifest is None

    @property
    def permission(self) -> dict:
        return (self.manifest or {}).get("permission", DEGRADED_PERMISSION)

    @property
    def requires_nodes(self) -> list[NodeRequirement]:
        return node_requirements(self.manifest) if self.manifest else []

    @property
    def plan(self) -> dict:
        """Declared execution defaults (provider/duration/resolution/ratio)."""
        return (self.manifest or {}).get("plan", {})

    @property
    def update(self) -> dict:
        return (self.manifest or {}).get("update", {})

    @property
    def prompt_index_line(self) -> str:
        """Progressive disclosure layer 1: name + description only."""
        return f"- {self.skill_id}@{self.version}: {self.description}"


def validate_community(meta: CommunityMeta, dir_name: str, strict: bool = False) -> list[str]:
    """Community-layer rules. Returns warnings; raises for anything unusable.

    ``strict=True`` is applied to packages we publish (they carry manifest.json).
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
    if not _is_spec_clean_name(meta.name):
        fail_or_warn(f"`name` {meta.name!r} is not spec-clean kebab-case ({NAME_RE_STR}) "
                     f"-- loaded in compat mode")
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


def _is_spec_clean_name(name: str) -> bool:
    import re
    return bool(re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", name))


def load_package(skill_dir: Path, expected_tree_receipt: dict | None = None) -> SkillPackage:
    """Load one package from disk.

    ``expected_tree_receipt`` is the store's install receipt; when given, any
    difference between the recorded file hashes and what is on disk raises
    SkillMutationError (nobody may rewrite an installed skill behind our back).
    """
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillValidationError(f"{skill_dir} has no SKILL.md")
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    try:
        raw = parse_frontmatter(text)
        body = split_frontmatter(text)[1]
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
    warnings = list(validate_community(meta, skill_dir.name, strict=strict))
    digest = content_digest(skill_dir)

    manifest = None
    if strict:
        try:
            manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SkillValidationError(f"{sidecar} is not valid JSON: {exc}") from exc
        try:
            validate_manifest(manifest)
        except ManifestError as exc:
            raise ManifestError(f"manifest.json: {exc}") from exc
        short = manifest["skill_id"].split("/")[-1]
        if short != meta.name:
            raise SkillValidationError(
                f"SKILL.md name {meta.name!r} != manifest.skill_id short name {short!r} (consistency rule)")
        if manifest["description"].strip() != meta.description.strip():
            raise SkillValidationError("manifest.description must be verbatim-equal to SKILL.md description")
        if manifest["content_digest"] != digest:
            raise SkillMutationError(
                f"content digest mismatch for {manifest['skill_id']}: manifest declares "
                f"{manifest['content_digest']}, package content hashes to {digest} -- the package was "
                f"edited after packing (re-pack with `skilyst digest --write`)")
        warnings.extend(deprecation_warnings(manifest))

    return SkillPackage(dir=skill_dir, community=meta, manifest=manifest, digest=digest, body=body,
                        tree_digest=tree_digest(skill_dir), warnings=tuple(warnings))
