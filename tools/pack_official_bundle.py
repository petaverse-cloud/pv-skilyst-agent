#!/usr/bin/env python3
"""Pack or verify the official skills bundle (`skills/official`).

Two manifests describe one bundle, and this script is their only writer:

  * ``index.json`` -- the bundle manifest. One entry per skill with
    ``skill_id`` / ``version`` / ``kind`` / ``content_digest``, so a registry (or a
    reviewer) can see the exact content coordinates of what ships, without
    unpacking every package;
  * ``bundle.json`` -- the install entry point the runtime preloader reads
    (kept in the pre-existing shape, plus the digest of each entry). It is a
    derived view of ``index.json``; both are regenerated together so they cannot
    drift, and ``--check`` fails on any disagreement.

The packer also records the two content-derived fields inside each package's own
sidecar: ``content_digest`` (sha256 over everything except ``manifest.json``) and
``supply_chain.signatures[].digest`` (the value the platform signature signs).
The signature itself stays a placeholder until the key ceremony lands -- a fake
``sig`` would be worse than an obviously pending one.

Usage:
  python3 tools/pack_official_bundle.py [--bundle skills/official] [--check]

  (no flags)  write the derived fields and both bundle manifests
  --check     verify everything on disk, write nothing, exit 1 on drift
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from skills.digest import content_digest, tree_digest  # noqa: E402
from skills.loader import load_package  # noqa: E402

BUNDLE_ID = "skilyst-official"
BUNDLE_RUNTIME = "0.1.0"
SIGNING_KEY_ID = "skilyst-platform"
DERIVED_FIELDS = ("content_digest", "supply_chain")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def skill_dirs(bundle_dir: Path) -> list[Path]:
    return sorted(d for d in bundle_dir.iterdir()
                  if d.is_dir() and (d / "SKILL.md").is_file() and (d / "manifest.json").is_file())


def load_sidecar(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def entry_for(skill_dir: Path) -> dict[str, Any]:
    """The bundle entry for one package: identity + content coordinates."""
    manifest = load_sidecar(skill_dir / "manifest.json")
    digest = content_digest(skill_dir)
    return {
        "path": skill_dir.name,
        "skill_id": manifest["skill_id"],
        "version": manifest["version"],
        "kind": manifest["kind"],
        "content_digest": digest,
        "tree_digest": tree_digest(skill_dir),
    }


def bundle_payload(entries: list[dict[str, Any]], generated_at: str) -> dict[str, Any]:
    return {
        "manifest_version": "0.2",
        "bundle": BUNDLE_ID,
        "kind": "official-bundle",
        "runtime": BUNDLE_RUNTIME,
        "signed_by": SIGNING_KEY_ID,
        "signature": {"key_id": SIGNING_KEY_ID, "algo": "ed25519",
                      "sig": "platform-signature-pending",
                      "digest": "covers every skills[].content_digest (see packer)"},
        "generated_at": generated_at,
        "skills": entries,
    }


def install_view(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The preloader's entry point: same skills, installer-shaped, digest included."""
    return {
        "bundle": BUNDLE_ID,
        "runtime": BUNDLE_RUNTIME,
        "signed_by": SIGNING_KEY_ID,
        "index": "index.json",
        "skills": [{"path": e["path"], "version": e["version"],
                    "content_digest": e["content_digest"]} for e in entries],
    }


def apply_derived(skill_dir: Path, check: bool) -> list[str]:
    """Record content_digest + signature digest in the package sidecar."""
    drift: list[str] = []
    sidecar = skill_dir / "manifest.json"
    manifest = load_sidecar(sidecar)
    digest = content_digest(skill_dir)
    wanted_sig = {"key_id": SIGNING_KEY_ID, "algo": "ed25519",
                  "sig": manifest.get("supply_chain", {}).get("signatures", [{}])[0]
                  .get("sig", "platform-signature-pending"),
                  "digest": digest}
    if manifest.get("content_digest") != digest:
        drift.append(f"{skill_dir.name}/manifest.json content_digest "
                     f"{manifest.get('content_digest')} != {digest}")
        if not check:
            manifest["content_digest"] = digest
    sigs = manifest.get("supply_chain", {}).get("signatures") or []
    if not sigs:
        drift.append(f"{skill_dir.name}/manifest.json has no platform signature entry")
    elif sigs[0].get("digest") != digest:
        drift.append(f"{skill_dir.name}/manifest.json signatures[0].digest "
                     f"{sigs[0].get('digest')} != {digest}")
        if not check:
            sigs[0] = wanted_sig
    if not check and drift:
        if sigs:
            manifest.setdefault("supply_chain", {})["signatures"] = sigs
        write_json(sidecar, manifest)
    # load_package is the authority: strict community rules + manifest v0.2 rules
    package = load_package(skill_dir)
    if package.manifest["kind"] != "official-bundle":
        drift.append(f"{skill_dir.name} kind={package.manifest['kind']!r}, bundle entries must be "
                     f"official-bundle (the preloader refuses anything else)")
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="pack or verify the official skills bundle")
    parser.add_argument("--bundle", default=str(ROOT / "skills" / "official"))
    parser.add_argument("--check", action="store_true",
                        help="verify on-disk state without writing (exit 1 on drift)")
    args = parser.parse_args(argv)

    bundle_dir = Path(args.bundle).resolve()
    dirs = skill_dirs(bundle_dir)
    if not dirs:
        print(f"pack: no skill packages under {bundle_dir}", file=sys.stderr)
        return 1

    drift: list[str] = []
    for skill_dir in dirs:
        drift.extend(apply_derived(skill_dir, args.check))

    entries = [entry_for(d) for d in dirs]

    index_path = bundle_dir / "index.json"
    legacy_path = bundle_dir / "bundle.json"
    previous = load_sidecar(index_path) if index_path.is_file() else {}
    generated_at = _now()
    index_payload = bundle_payload(entries, generated_at)
    legacy_payload = install_view(entries)
    # Idempotence: a re-pack that changes nothing must change no file, so the
    # timestamp only moves when the skill set or a digest moved with it.
    if previous.get("skills") == entries and previous.get("generated_at"):
        index_payload["generated_at"] = previous["generated_at"]

    if args.check:
        for label, path, payload in (("index.json", index_path, index_payload),
                                     ("bundle.json", legacy_path, legacy_payload)):
            if not path.is_file():
                drift.append(f"{label} is missing (run tools/pack_official_bundle.py)")
                continue
            on_disk = load_sidecar(path)
            if on_disk != payload:
                drift.append(f"{label} is stale: {_first_difference(on_disk, payload)}")
        if drift:
            print("pack --check FAILED:", file=sys.stderr)
            for line in drift:
                print(f"  - {line}", file=sys.stderr)
            return 1
        print(f"pack --check OK: {len(entries)} skills, digests and both manifests agree")
        return 0

    if drift:
        print("pack: refreshed derived fields:", file=sys.stderr)
        for line in drift:
            print(f"  - {line}", file=sys.stderr)
    write_json(index_path, index_payload)
    write_json(legacy_path, legacy_payload)
    for e in entries:
        print(f"{e['skill_id']}@{e['version']}  {e['content_digest']}")
    print(f"packed {len(entries)} skills -> {index_path.relative_to(ROOT)} + "
          f"{legacy_path.relative_to(ROOT)}")
    return 0


def _first_difference(a: dict[str, Any], b: dict[str, Any]) -> str:
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            return f"{key}: on disk {a.get(key)!r} != packed {b.get(key)!r}"
    return "unknown difference"


def _digest_of_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
