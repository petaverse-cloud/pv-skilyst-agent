"""Content addressing for skill packages.

Two digests, because one is not enough:

  * ``content_digest`` -- sha256 over the package *content* (every file except
    ``manifest.json``), sorted by relative path, file bytes only (no mtimes, no
    permissions) so it is stable across machines and git checkouts. This is the
    value the manifest declares in ``content_digest``; excluding the sidecar is
    what makes the field writable at all (T1 defect: a digest that hashes its own
    carrier can never be recorded).
  * ``tree_digest`` -- sha256 over *every* file including ``manifest.json``.
    The store records this as an install receipt, which is what catches
    post-install mutation of the sidecar as well (T1 defect: nothing detected a
    skill being rewritten after install).

``diff_tree`` turns a receipt mismatch into the list of files that actually
changed, so the runtime can fail loudly with a usable message.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

IGNORED = {".DS_Store", "__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
SIDECAR = "manifest.json"


def iter_files(skill_dir: Path) -> list[Path]:
    return sorted(p for p in Path(skill_dir).rglob("*")
                  if p.is_file() and not any(part in IGNORED for part in p.parts))


def file_hashes(skill_dir: Path) -> dict[str, str]:
    root = Path(skill_dir)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in iter_files(root)}


def _digest_of(hashes: dict[str, str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(hashes):
        h.update(rel.encode())
        h.update(b"\0")
        h.update(bytes.fromhex(hashes[rel]))
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def content_digest(skill_dir: Path) -> str:
    """Digest of the package content (manifest.json excluded)."""
    return _digest_of({rel: h for rel, h in file_hashes(skill_dir).items() if rel != SIDECAR})


def tree_digest(skill_dir: Path) -> str:
    """Digest of the whole installed tree (manifest.json included)."""
    return _digest_of(file_hashes(skill_dir))


def diff_tree(skill_dir: Path, receipt: dict) -> list[str]:
    """Compare the tree against a receipt ({'tree_digest', 'files': {rel: sha256}}).

    Returns human-readable differences; empty means the tree is intact.
    """
    current = file_hashes(skill_dir)
    recorded = receipt.get("files") or {}
    out = []
    for rel in sorted(set(recorded) | set(current)):
        if rel not in current:
            out.append(f"removed: {rel}")
        elif rel not in recorded:
            out.append(f"added: {rel}")
        elif current[rel] != recorded[rel]:
            out.append(f"modified: {rel}")
    return out
