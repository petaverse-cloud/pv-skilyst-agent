"""Resource resolution + offline behaviour (acceptance line 1's second half).

The spec's progressive-disclosure model means a skill's SKILL.md points at
in-package files (references/, scripts/, assets/) and -- in real packages --
sometimes at remote resources (CDNs, docs sites). The runtime must be able to
answer, for every reference, in a network-less environment:

    in-package  -> resolved from disk, no network needed
    remote      -> fetched only if permission.egress allows it, and a failure
                   must be LOUD (never silently substitute an empty document)
    missing     -> reported at load time (broken reference), not at use time
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BARE_FILE_RE = re.compile(r"`?([A-Za-z0-9_.-]+\.(?:md|py|json|txt|lock|sh|js|ts|csv|yaml|yml))`?")
INSTALL_HINT_RE = re.compile(r"\b(pip3? install|npm install|apt-get install|brew install|npx)\b")
NETWORK_HINT_RE = re.compile(r"\b(curl|wget|requests\.get|urllib\.request|fetch\(|https?://)\b")


@dataclass
class RefResolution:
    ref: str
    kind: str          # in-package | remote | missing
    detail: str


class OfflineError(RuntimeError):
    """Raised when a reference cannot be satisfied -- never swallowed."""


def refine_inventory(skill_dir: Path, body: str, remote: list[str]) -> dict:
    """Extend the coarse inventory with prose-referenced files and dependency hints."""
    root_files = sorted(p.name for p in skill_dir.iterdir() if p.is_file())
    mentioned = sorted({m for m in BARE_FILE_RE.findall(body) if m in root_files and m != "SKILL.md"})
    installs = sorted(set(INSTALL_HINT_RE.findall(body)))
    network = sorted(set(NETWORK_HINT_RE.findall(body)))
    scripts_dir = skill_dir / "scripts"
    scripts = sorted(p.name for p in scripts_dir.rglob("*") if p.is_file()) if scripts_dir.is_dir() else []
    network_scripts = []
    for script in (scripts_dir.rglob("*.py") if scripts_dir.is_dir() else []):
        text = script.read_text(encoding="utf-8", errors="replace")
        if NETWORK_HINT_RE.search(text):
            network_scripts.append(script.relative_to(skill_dir).as_posix())
    return {"root_files": root_files, "prose_file_refs": mentioned, "install_hints": installs,
            "network_hints": network, "scripts": scripts[:50], "scripts_total": len(scripts),
            "network_scripts": network_scripts, "remote_refs": remote,
            "egress_grant_needed": bool(remote)}


def resolve(skill_dir: Path, ref: str, egress_hosts: set[str] | None, timeout: int = 15) -> RefResolution:
    """Resolve one reference; remote fetches are gate-checked and failures are loud."""
    if ref.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        host = (urlparse(ref).hostname or "").lower()
        if host not in (egress_hosts or set()):
            return RefResolution(ref, "remote", f"refused before fetch: {host!r} not in permission.egress")
        try:
            with urllib.request.urlopen(ref, timeout=timeout) as resp:
                return RefResolution(ref, "remote", f"fetched HTTP {resp.status} ({resp.headers.get('content-length', '?')} bytes)")
        except urllib.error.URLError as exc:
            raise OfflineError(f"remote reference {ref} could not be fetched: {exc.reason} "
                               f"(no cached copy exists; the reference cannot be silently skipped)") from exc
    candidate = (skill_dir / ref).resolve()
    if skill_dir.resolve() not in candidate.parents and candidate != skill_dir.resolve():
        return RefResolution(ref, "missing", "path escapes the skill directory (refused)")
    if candidate.is_file():
        return RefResolution(ref, "in-package", f"read {len(candidate.read_bytes())} bytes from disk (no network needed)")
    for root_file in skill_dir.rglob(ref):
        if root_file.is_file():
            return RefResolution(ref, "in-package",
                                 f"read {len(root_file.read_bytes())} bytes from disk ({root_file.relative_to(skill_dir)})")
    return RefResolution(ref, "missing", "referenced file does not exist inside the package")
