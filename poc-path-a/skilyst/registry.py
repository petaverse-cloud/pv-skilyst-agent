"""Local skill store: install / list / digest / preload official bundle / update channel.

Design points the PoC had to solve (each is a hidden cost of "just load SKILL.md"):
  * content_digest must be stable across machines -> hashed over sorted relative
    paths + file bytes, with mtimes/permissions excluded;
  * installing a community package must NOT rewrite it (zero-modification) --
    the loader adapts to the package, never the other way round;
  * preloaded `official-bundle` skills arrive from the runtime distribution and
    update on their own channel, decoupled from the runtime version;
  * requires.nodes is pre-flighted against the live node registry before a run.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .skill import SkillPackage, validate_manifest
from .permission import classify_resources

IGNORED = {".DS_Store", "__pycache__", ".git"}


def content_digest(skill_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file() and not any(x in p.parts for x in IGNORED)):
        h.update(path.relative_to(skill_dir).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


@dataclass
class InstalledSkill:
    package: SkillPackage
    source: str
    installed_at: float


class SkillStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.index = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    def _save(self) -> None:
        self.index_path.write_text(json.dumps(self.index, indent=2, sort_keys=True))

    def install(self, source_dir: Path, origin: str = "local") -> SkillPackage:
        source_dir = Path(source_dir).resolve()
        digest = content_digest(source_dir)
        target = self.root / source_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source_dir, target, ignore=shutil.ignore_patterns(*IGNORED))
        if content_digest(target) != digest:
            raise RuntimeError("install mutated the package (digest drift) -- loader must be read-only")
        package = self._load(target)
        self.index[package.skill_id] = {
            "version": package.version,
            "dir": target.name,
            "digest": digest,
            "origin": origin,
            "degraded": package.degraded,
            "channel": (package.manifest or {}).get("update", {}).get("channel", "community"),
            "policy": (package.manifest or {}).get("update", {}).get("policy", "manual"),
        }
        self._save()
        return package

    def _load(self, skill_dir: Path) -> SkillPackage:
        from .skill import load_package
        return load_package(skill_dir, content_digest(skill_dir))

    def list(self) -> list[SkillPackage]:
        out = []
        for name in sorted(self.index):
            entry = self.index[name]
            out.append(self._load(self.root / entry["dir"]))
        return out

    def get(self, skill_id: str) -> SkillPackage:
        if skill_id not in self.index:
            raise KeyError(f"skill {skill_id!r} is not installed (installed: {sorted(self.index)})")
        return self._load(self.root / self.index[skill_id]["dir"])

    def preload_official_bundle(self, bundle_dir: Path) -> list[SkillPackage]:
        """Preload the platform-signed official bundle (manifest 6.1/6.4)."""
        bundle_manifest = bundle_dir / "bundle.json"
        if not bundle_manifest.is_file():
            raise RuntimeError(f"official bundle {bundle_dir} has no bundle.json")
        spec = json.loads(bundle_manifest.read_text())
        loaded = []
        for entry in spec["skills"]:
            skill_dir = bundle_dir / entry["path"]
            package = self._load(skill_dir) if (skill_dir / "manifest.json").exists() else None
            if package is None:
                raise RuntimeError(f"official bundle entry {entry['path']} lacks manifest.json (bundles must be signed)")
            if package.manifest["kind"] != "official-bundle":
                raise RuntimeError(f"official bundle entry {entry['path']} kind="
                                   f"{package.manifest['kind']!r}, must be official-bundle")
            if not package.manifest["supply_chain"].get("signatures"):
                raise RuntimeError(f"official bundle entry {entry['path']} has no platform signature")
            self.install(skill_dir, origin="official-bundle")
            loaded.append(package)
        return loaded

    def plan_update(self, new_dir: Path) -> dict:
        """Decide hot vs cold for an incoming version (manifest 6.3 rule 2)."""
        from .skill import is_hot_update
        candidate = self._load(new_dir) if (new_dir / "SKILL.md").exists() else None
        if candidate is None:
            raise RuntimeError(f"{new_dir} is not a skill package")
        current = self.index.get(candidate.skill_id)
        old = None
        if current:
            old = self._load(self.root / current["dir"]).manifest
        hot, reason = is_hot_update(old, candidate.manifest)
        return {"skill_id": candidate.skill_id, "hot": hot, "reason": reason,
                "from": current and current["version"], "to": candidate.version,
                "digest": candidate.digest}


def preflight_nodes(package: SkillPackage, client) -> list[str]:
    """Validate requires.nodes against the live node registry before running."""
    status, resp = client.request("GET", "/api/v1/nodes")
    if status != 200:
        return [f"node registry unavailable (HTTP {status}) -- refusing to run a skill with node requirements"]
    payload = resp.get("payload", resp)
    nodes = payload.get("nodes", payload) if isinstance(payload, dict) else payload
    known = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    problems = []
    for req in package.requires_nodes:
        node = known.get(req.node_type)
        if node is None:
            if req.optional:
                fallbacks = ", ".join(req.fallback) or "none declared"
                problems.append(f"optional node {req.node_type} missing -> fallback chain: {fallbacks}")
            else:
                problems.append(f"REQUIRED node {req.node_type} is not registered on this cluster")
        elif not node.get("enabled", True):
            problems.append(f"node {req.node_type} is registered but disabled")
    return problems


def inventory_report(package: SkillPackage) -> dict:
    inv = classify_resources(package.dir, package.body)
    return {"skill_id": package.skill_id, "version": package.version, "degraded": package.degraded,
            "digest": package.digest, "in_package": inv.in_package, "remote": inv.remote,
            "missing_references": inv.missing, "permission": package.permission,
            "requires_nodes": [n.node_type for n in package.requires_nodes],
            "spec_warnings": list(package.warnings),
            "unknown_frontmatter_keys": sorted(set(package.community.raw) - {
                "name", "description", "license", "compatibility", "metadata", "allowed-tools"})}
