"""Acceptance line 1 re-run after the migration: community skills load zero-modification,
every reference resolves with the declared egress policy, and the offline failure is loud.

Evidence only -- this file is not part of the runtime.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(ROOT / "src" / "cli.py")]
sys.path.insert(0, str(ROOT / "src"))

from sandbox import OfflineError, PermissionGate, resolve as resolve_ref  # noqa: E402
from skills import content_digest, load_package, tree_digest              # noqa: E402

FIXTURES = {
    "anthropics_pdf": Path.home() / "t1-poc/fixtures/anthropics-skills/skills/pdf",
    "seedance_20": Path.home() / "t1-poc/fixtures/seedance-2.0",
}
OFFICIAL = ROOT / "skills" / "official" / "video-15s"


def run(*args):
    proc = subprocess.run([*CLI, *args], capture_output=True, text=True, cwd=ROOT)
    return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr.strip()[:400]}


def file_hashes(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def main() -> int:
    report = {"line": "1 -- format ingestion + offline behaviour (post-migration re-run)", "skills": {}}

    for name, path in FIXTURES.items():
        if not (path / "SKILL.md").is_file():
            report["skills"][name] = {"skipped": f"fixture absent at {path}"}
            continue
        before = file_hashes(path)
        inventory = run("inventory", str(path))
        loaded = run("load", str(path))
        after = file_hashes(path)
        package = load_package(path)
        report["skills"][name] = {
            "path": str(path),
            "files": len(before),
            "zero_modification": before == after,
            "changed_files": sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)),
            "load_mode": "degraded (community, no manifest.json)" if package.degraded else "full",
            "skill_id": package.skill_id,
            "version": package.version,
            "digest": package.digest,
            "spec_warnings": list(package.warnings)[:4],
            "inventory_exit": inventory["exit"],
            "inventory_summary": json.loads(inventory["stdout"]) if inventory["exit"] == 0 else inventory,
            "load_exit": loaded["exit"],
        }

    # reference resolution: in-package reads from disk, remote refused by egress, loud offline failure
    pdf = FIXTURES["anthropics_pdf"]
    seedance = FIXTURES["seedance_20"]
    resolutions = []
    cases = [(pdf, "reference.md"), (pdf, "scripts/extract_form_field_info.py"),
             (seedance, "references/directing-engine.md"),
             (pdf, "https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.9.0/p5.min.js"),
             (pdf, "does/not/exist.md")]
    for skill_dir, ref in cases:
        skill_gate = PermissionGate(skill_dir, load_package(skill_dir).permission)
        try:
            result = resolve_ref(skill_dir, ref, skill_gate.egress_hosts).__dict__
        except OfflineError as exc:
            result = {"ref": ref, "kind": "remote", "detail": f"LOUD FAILURE: {exc}"}
        result["skill"] = skill_dir.name
        resolutions.append(result)

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "video-15s"
        shutil.copytree(OFFICIAL, staged)
        manifest = json.loads((staged / "manifest.json").read_text())
        manifest["permission"]["egress"] = ["unreachable.invalid"]
        manifest["content_digest"] = content_digest(staged)
        (staged / "manifest.json").write_text(json.dumps(manifest))
        loud_gate = PermissionGate(staged, load_package(staged).permission)
        try:
            resolve_ref(staged, "https://unreachable.invalid/method.md", loud_gate.egress_hosts, timeout=5)
            loud = "NOT LOUD (bug)"
        except OfflineError as exc:
            loud = f"LOUD FAILURE: {exc}"

    report["reference_resolution"] = {"skill": "anthropics_pdf", "egress": load_package(pdf).permission["egress"],
                                      "resolutions": resolutions,
                                      "unreachable_remote_with_egress_granted": loud}

    # the four sandbox dimensions, exercised through the migrated gate
    from sandbox import SandboxViolation
    official = load_package(OFFICIAL)
    official_gate = PermissionGate(official.dir, official.permission, workspace=Path.home() / ".skilyst/workspace")
    degraded_gate = PermissionGate(pdf, load_package(pdf).permission)
    sandbox = []
    for label, fn in [
        ("exec=none -> refuse running a skill script", lambda: official_gate.check_exec(official.dir / "SKILL.md")),
        ("exec=none (community) -> refuse a package script", lambda: degraded_gate.check_exec(pdf / "scripts/extract_form_field_info.py")),
        ("filesystem=workspace -> refuse a write outside the workspace", lambda: official_gate.check_write("/tmp/escape.md")),
        ("filesystem=workspace -> allow a write inside the workspace", lambda: official_gate.check_write(Path.home() / ".skilyst/workspace/notes.md")),
        ("secrets=false (community) -> refuse a credential", lambda: degraded_gate.require_secrets()),
        ("egress -> refuse an unknown host", lambda: official_gate.check_egress("https://exfiltrate.example.com/x")),
    ]:
        try:
            fn()
            sandbox.append({"case": label, "outcome": "allowed"})
        except SandboxViolation as exc:
            sandbox.append({"case": label, "outcome": f"LOUD REFUSAL: {exc}"})
    report["sandbox_contract"] = sandbox

    # the official package keeps its own digest stable across a load (defect 1 fix)
    report["official_package"] = {"digest": content_digest(OFFICIAL), "tree_digest": tree_digest(OFFICIAL),
                                  "declared": json.loads((OFFICIAL / "manifest.json").read_text())["content_digest"]}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
