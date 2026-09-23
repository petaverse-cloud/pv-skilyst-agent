"""Sandbox permission enforcement + resource classification (manifest 4.4 / decision D2).

Declared `permission` is enforced by the runtime, not just documented:
  egress      -> outbound host allowlist (default none);
  filesystem  -> writes confined to the skill dir or the task workspace;
  exec        -> only files inside the skill's scripts/ may run;
  secrets     -> no user credential is ever handed to skill code unless declared.

The loader also classifies every resource a package references, so 'what happens
when the network is down' is answerable up-front instead of at runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BARE_PATH_RE = re.compile(r"(?<![\w/`.-])((?:references|scripts|assets|templates)/[A-Za-z0-9._/-]+)")
URL_RE = re.compile(r"https?://[^\s)\"'<>]+")


class SandboxViolation(RuntimeError):
    """Raised loudly; the runtime never degrades a refused operation silently."""


@dataclass
class ResourceInventory:
    in_package: list[str]
    remote: list[str]
    missing: list[str]


def classify_resources(skill_dir: Path, body: str) -> ResourceInventory:
    urls = sorted(set(URL_RE.findall(body)))
    refs = set(LINK_RE.findall(body)) | set(BARE_PATH_RE.findall(body))
    in_pkg, missing = [], []
    for ref in sorted(refs):
        if ref.startswith(("http://", "https://", "#", "mailto:")):
            continue
        if ref.startswith("<"):
            continue
        candidate = (skill_dir / ref.split("#")[0]).resolve()
        if candidate.exists():
            in_pkg.append(ref)
        else:
            missing.append(ref)
    return ResourceInventory(in_package=in_pkg, remote=urls, missing=missing)


class PermissionGate:
    """Enforces one skill's declared sandbox."""

    def __init__(self, skill_dir: Path, permission: dict, workspace: Path | None = None):
        self.skill_dir = Path(skill_dir).resolve()
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.permission = permission

    # -- egress -------------------------------------------------------------
    @property
    def egress_hosts(self) -> set[str] | None:
        egress = self.permission.get("egress", "none")
        if egress == "none" or egress is None:
            return set()
        return {h.lower() for h in egress}

    def check_egress(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        allowed = self.egress_hosts
        if not allowed:
            raise SandboxViolation("egress refused: skill declares permission.egress = none")
        if host not in allowed:
            raise SandboxViolation(
                f"egress refused: {host or url!r} is not in permission.egress {sorted(allowed)} "
                f"(declared by the skill's manifest)")

    # -- filesystem ---------------------------------------------------------
    def check_write(self, path: Path) -> None:
        mode = self.permission.get("filesystem", "skill-dir")
        target = Path(path).resolve()
        root = self.skill_dir if mode == "skill-dir" else (self.workspace if mode == "workspace" else None)
        if mode == "none" or root is None:
            raise SandboxViolation(f"filesystem refused: skill declares permission.filesystem = {mode!r}")
        if root not in target.parents and target != root:
            raise SandboxViolation(f"filesystem refused: {target} is outside {root} (permission.filesystem={mode})")

    def writable_roots(self) -> list[Path]:
        mode = self.permission.get("filesystem", "skill-dir")
        if mode == "workspace":
            return [self.workspace]
        if mode == "skill-dir":
            return [self.skill_dir]
        return []

    # -- exec ---------------------------------------------------------------
    def check_exec(self, script: Path) -> None:
        mode = self.permission.get("exec", "none")
        script = Path(script).resolve()
        if mode == "none":
            raise SandboxViolation("exec refused: skill declares permission.exec = none")
        if mode == "scripts-whitelist" and (self.skill_dir / "scripts") not in script.parents:
            raise SandboxViolation(f"exec refused: {script} is not inside the skill's scripts/ directory")

    # -- secrets ------------------------------------------------------------
    def secrets_allowed(self) -> bool:
        return bool(self.permission.get("secrets", False))

    def require_secrets(self, what: str = "user credential") -> None:
        if not self.secrets_allowed():
            raise SandboxViolation(
                f"{what} refused: skill declares permission.secrets = false "
                f"(installing this skill required an explicit user confirmation)")
