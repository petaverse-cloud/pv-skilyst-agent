"""Local skill store: install / list / digest receipts / official preload / update channel.

Design points the PoC had to solve (each is a hidden cost of "just load SKILL.md"):

  * install is a receipt-backed copy: the store records the per-file hashes of
    what it installed, and every later load re-verifies them, so an installed
    skill that is edited on disk is caught loudly instead of being executed
    (T1 defect: nothing detected post-install mutation);
  * installing a community package must NOT rewrite it (zero-modification) --
    the loader adapts to the package, never the other way round;
  * preloaded `official-bundle` skills arrive from the runtime distribution and
    update on their own channel, decoupled from the runtime version; the preload
    reads the bundle manifest (`index.json`) and verifies every entry's declared
    content digest before installing, so a bundle edited after packing is refused;
  * requires.nodes is pre-flighted against the live node registry before a run,
    matching registry `id` (the node *definition* id) -- never registry
    `node_type`, which is the workflow node type and matches nothing.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from errors import SkillMutationError, SkillValidationError
from manifest import NodeRequirement
from .digest import IGNORED, content_digest, diff_tree, file_hashes, tree_digest
from .loader import SkillPackage, load_package

INDEX_VERSION = 2

# The fields that must agree between index.json (bundle manifest) and bundle.json
# (install entry point) -- the skill set and its content coordinates.
BUNDLE_COORDINATE_FIELDS = ("path", "version", "content_digest")


def _bundle_coordinates(spec: dict) -> list[tuple]:
    return sorted(tuple((e.get(f) for f in BUNDLE_COORDINATE_FIELDS))
                  for e in spec.get("skills") or [])


@dataclass
class InstalledSkill:
    package: SkillPackage
    source: str
    installed_at: float


@dataclass
class NodeProblem:
    """One pre-flight finding about a declared node dependency."""

    node_id: str
    severity: str          # blocking | degraded | info
    detail: str

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass
class PreflightReport:
    skill_id: str
    registry_available: bool
    problems: list[NodeProblem] = field(default_factory=list)
    # Registry entries for the nodes the skill declares. The tool registry types its
    # arguments from these (`input_schema`), so a caller never has to guess a field
    # name or which argument the node requires.
    node_schemas: dict = field(default_factory=dict)

    @property
    def blocking(self) -> list[NodeProblem]:
        return [p for p in self.problems if p.blocking]

    @property
    def runnable(self) -> bool:
        return self.registry_available and not self.blocking


class SkillStore:
    def __init__(self, root: Path, verify: bool = True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.verify = verify
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    # -- index ---------------------------------------------------------------
    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self.index, indent=2, sort_keys=True))

    def _receipt(self, skill_dir: Path) -> dict:
        return {"tree_digest": tree_digest(skill_dir), "files": file_hashes(skill_dir)}

    # -- install -------------------------------------------------------------
    def install(self, source_dir: Path, origin: str = "local") -> SkillPackage:
        source_dir = Path(source_dir).resolve()
        if not (source_dir / "SKILL.md").is_file():
            raise SkillValidationError(f"{source_dir} is not a skill package (no SKILL.md)")
        source_digest = content_digest(source_dir)
        source_receipt = self._receipt(source_dir)
        target = self.root / source_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source_dir, target, ignore=shutil.ignore_patterns(*IGNORED))
        if content_digest(target) != source_digest or self._receipt(target) != source_receipt:
            raise RuntimeError("install mutated the package (digest drift) -- loader must be read-only")

        package = self._load(target)
        self.index[package.skill_id] = {
            "version": package.version,
            "dir": target.name,
            "digest": package.digest,
            "receipt": source_receipt,
            "origin": origin,
            "degraded": package.degraded,
            "channel": (package.manifest or {}).get("update", {}).get("channel", "community"),
            "policy": (package.manifest or {}).get("update", {}).get("policy", "manual"),
            "installed_at": time.time(),
            "index_version": INDEX_VERSION,
        }
        self._save()
        return package

    def uninstall(self, skill_id: str) -> None:
        entry = self.index.pop(skill_id, None)
        if entry is None:
            raise KeyError(f"skill {skill_id!r} is not installed")
        target = self.root / entry["dir"]
        if target.exists():
            shutil.rmtree(target)
        self._save()

    # -- load ----------------------------------------------------------------
    def _entry(self, skill_id: str) -> dict:
        if skill_id not in self.index:
            raise KeyError(f"skill {skill_id!r} is not installed (installed: {sorted(self.index)})")
        return self.index[skill_id]

    def _load(self, skill_dir: Path, receipt: dict | None = None) -> SkillPackage:
        """Load + verify. A receipt mismatch is a hard failure, never a warning."""
        if receipt and self.verify:
            differences = diff_tree(skill_dir, receipt)
            if differences:
                raise SkillMutationError(
                    f"installed skill {skill_dir.name} was modified after install "
                    f"({len(differences)} file(s): {', '.join(differences[:5])}"
                    f"{'...' if len(differences) > 5 else ''}) -- refusing to load; "
                    f"re-install the package to accept the change")
        return load_package(skill_dir)

    def get(self, skill_id: str) -> SkillPackage:
        entry = self._entry(skill_id)
        return self._load(self.root / entry["dir"], entry.get("receipt"))

    def list(self) -> list[SkillPackage]:
        return [self.get(name) for name in sorted(self.index)]

    def list_partial(self) -> tuple[list[SkillPackage], list[dict]]:
        """Every installed skill that loads, plus the ones that did not.

        One unreadable package (edited behind our back, or invalid) must not take
        the whole runtime down: the healthy skills still load, and the broken ones
        are reported with their integrity difference so the operator sees exactly
        what to re-install. Anything that *uses* a broken skill still raises.
        """
        loaded: list[SkillPackage] = []
        broken: list[dict] = []
        for name in sorted(self.index):
            entry = self.index[name]
            skill_dir = self.root / entry["dir"]
            try:
                loaded.append(self._load(skill_dir, entry.get("receipt")))
            except (SkillMutationError, SkillValidationError) as exc:
                broken.append({"skill_id": name, "dir": entry["dir"], "error": str(exc),
                               "differences": diff_tree(skill_dir, entry["receipt"])
                               if entry.get("receipt") else []})
        return loaded, broken

    def verify_all(self) -> list[dict]:
        """Integrity report for every installed skill (no exception, for `doctor`)."""
        out = []
        for name in sorted(self.index):
            entry = self.index[name]
            skill_dir = self.root / entry["dir"]
            row = {"skill_id": name, "dir": entry["dir"], "version": entry["version"], "ok": True,
                   "differences": []}
            try:
                self._load(skill_dir, entry.get("receipt"))
            except (SkillMutationError, SkillValidationError) as exc:
                row["ok"] = False
                row["differences"] = ([f"{type(exc).__name__}: {exc}"] if not entry.get("receipt")
                                      else diff_tree(skill_dir, entry["receipt"]))
            out.append(row)
        return out

    # -- official bundle -----------------------------------------------------
    def preload_official_bundle(self, bundle_dir: Path) -> list[SkillPackage]:
        """Preload the platform-signed official bundle (manifest 6.1/6.4).

        The bundle manifest is ``index.json``: every entry carries the content
        digest of the package it names, so the preload verifies the *packed*
        content against the manifest before anything is installed. ``bundle.json``
        is the older install entry point and is still accepted on its own; when
        both files are present they must describe the same skills, otherwise the
        bundle is internally inconsistent and is refused rather than guessed at.
        """
        bundle_dir = Path(bundle_dir)
        bundle_manifest = bundle_dir / "index.json"
        legacy_manifest = bundle_dir / "bundle.json"
        if not bundle_manifest.is_file():
            bundle_manifest = legacy_manifest
        if not bundle_manifest.is_file():
            raise RuntimeError(f"official bundle {bundle_dir} has no index.json (or bundle.json)")
        spec = json.loads(bundle_manifest.read_text())
        if legacy_manifest.is_file() and bundle_manifest != legacy_manifest:
            legacy = json.loads(legacy_manifest.read_text())
            if _bundle_coordinates(spec) != _bundle_coordinates(legacy):
                raise RuntimeError(
                    f"official bundle {bundle_dir} is internally inconsistent: index.json and "
                    f"bundle.json disagree on the packed skills -- re-run "
                    f"tools/pack_official_bundle.py instead of trusting either file")
        loaded = []
        for entry in spec["skills"]:
            skill_dir = bundle_dir / entry["path"]
            if not (skill_dir / "manifest.json").is_file():
                raise RuntimeError(f"official bundle entry {entry['path']} lacks manifest.json "
                                   f"(bundles must be signed)")
            declared = entry.get("content_digest")
            if declared and content_digest(skill_dir) != declared:
                raise RuntimeError(
                    f"official bundle entry {entry['path']} does not match the bundle manifest: "
                    f"declared {declared}, package content hashes to {content_digest(skill_dir)} "
                    f"-- the bundle was edited after packing (re-run tools/pack_official_bundle.py)")
            package = self._load(skill_dir)     # digest + manifest validation before touching the store
            if package.manifest["kind"] != "official-bundle":
                raise RuntimeError(f"official bundle entry {entry['path']} kind="
                                   f"{package.manifest['kind']!r}, must be official-bundle")
            if not package.manifest["supply_chain"].get("signatures"):
                raise RuntimeError(f"official bundle entry {entry['path']} has no platform signature")
            loaded.append(self.install(skill_dir, origin="official-bundle"))
        return loaded

    # -- update channel ------------------------------------------------------
    def plan_update(self, new_dir: Path) -> dict:
        """Decide hot vs cold for an incoming version (manifest 6.3 rule 2)."""
        from manifest import is_hot_update
        new_dir = Path(new_dir)
        if not (new_dir / "SKILL.md").exists():
            raise RuntimeError(f"{new_dir} is not a skill package")
        candidate = self._load(new_dir)
        current = self.index.get(candidate.skill_id)
        old = self._load(self.root / current["dir"], current.get("receipt")).manifest if current else None
        hot, reason = is_hot_update(old, candidate.manifest)
        return {"skill_id": candidate.skill_id, "hot": hot, "reason": reason,
                "from": current and current["version"], "to": candidate.version,
                "content_changed": bool(current and current["digest"] != candidate.digest),
                "digest": candidate.digest}


def preflight_nodes(package: SkillPackage, client, allow_fallback: bool = False) -> PreflightReport:
    """Validate requires.nodes against the live node registry before running.

    Matching is on the registry's node *definition* ``id`` (``generate:minimax-h3``).
    Registry ``node_type`` is the workflow node type (``generate``) and is used
    only to cross-check that the id the skill asks for is internally consistent.

    ``allow_fallback`` decides what a missing *required* node means when the
    manifest declares a fallback chain that is available. Default is to refuse:
    a fallback is a different model with a different price and a different look,
    so switching to it silently is exactly the "quiet tier downgrade" the
    manifest forbids. The operator opts in per run.
    """
    report = PreflightReport(skill_id=package.skill_id, registry_available=False)
    status, resp = client.request("GET", "/api/v1/nodes")
    if status != 200:
        report.problems.append(NodeProblem(
            node_id="*", severity="blocking",
            detail=f"node registry unavailable (HTTP {status}) -- refusing to run a skill with node "
                   f"requirements rather than guessing at the cluster's capabilities"))
        return report
    report.registry_available = True
    payload = resp.get("payload", resp)
    nodes = payload.get("nodes", payload) if isinstance(payload, dict) else payload
    known = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id")}
    wanted_ids = {r.node_id for r in package.requires_nodes}
    plan_provider = (package.plan or {}).get("provider")
    if plan_provider:
        wanted_ids.add(f"generate:{plan_provider}")
    report.node_schemas = {nid: known[nid] for nid in sorted(wanted_ids) if nid in known}

    for req in package.requires_nodes:
        report.problems.extend(_check_requirement(req, known, allow_fallback))

    plan_provider = (package.plan or {}).get("provider")
    if plan_provider:
        wanted = f"generate:{plan_provider}"
        if wanted not in known:
            report.problems.append(NodeProblem(
                node_id=wanted, severity="info",
                detail=f"manifest.plan.provider {plan_provider!r} has no matching node definition "
                       f"{wanted!r} on this cluster"))
    return report


def _check_requirement(req: NodeRequirement, known: dict, allow_fallback: bool = False) -> list[NodeProblem]:
    node = known.get(req.node_id)
    if node is None:
        available = [f for f in req.fallback if f in known and known[f].get("enabled", True)]
        if req.optional:
            return [NodeProblem(req.node_id, "degraded",
                                f"optional node {req.node_id} is not registered; "
                                f"fallback chain: {', '.join(available) or 'none available'}")]
        if available and allow_fallback:
            return [NodeProblem(req.node_id, "degraded",
                                f"required node {req.node_id} is not registered; running on the declared "
                                f"fallback {available[0]} (allowed by --allow-fallback -- a different "
                                f"model, price and look)")]
        if available:
            return [NodeProblem(req.node_id, "blocking",
                                f"required node {req.node_id} is not registered on this cluster; the "
                                f"declared fallback {available[0]} is available -- re-run with "
                                f"--allow-fallback to accept that substitution")]
        return [NodeProblem(req.node_id, "blocking",
                            f"REQUIRED node {req.node_id} is not registered on this cluster "
                            f"(declared fallbacks: {', '.join(req.fallback) or 'none'})")]
    problems = []
    if not node.get("enabled", True):
        problems.append(NodeProblem(req.node_id, "blocking",
                                    f"node {req.node_id} is registered but disabled"))
    registry_type = node.get("node_type")
    if registry_type and registry_type != req.node_type:
        problems.append(NodeProblem(req.node_id, "blocking",
                                    f"manifest declares node_id {req.node_id} (node_type "
                                    f"{req.node_type!r}) but the registry entry has node_type "
                                    f"{registry_type!r} -- the ids do not describe the same node"))
    return problems


def inventory_report(package: SkillPackage) -> dict:
    from sandbox import classify_resources
    inv = classify_resources(package.dir, package.body)
    return {"skill_id": package.skill_id, "version": package.version, "degraded": package.degraded,
            "digest": package.digest, "in_package": inv.in_package, "remote": inv.remote,
            "missing_references": inv.missing, "permission": package.permission,
            "requires_nodes": [{"node_id": n.node_id, "node_type": n.node_type,
                                "version_range": n.version_range, "optional": n.optional,
                                "fallback": n.fallback} for n in package.requires_nodes],
            "plan": package.plan,
            "spec_warnings": list(package.warnings),
            "unknown_frontmatter_keys": package.community.unknown_keys}
