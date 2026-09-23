"""T4 acceptance: the official skills bundle (skills/official) and its manifests.

What is asserted here is what the bundle promises to the installer and the
registry, not implementation detail:

  * every package loads in STRICT mode (it carries a sidecar, so no community
    deviation is tolerated) and its declared content_digest matches its content;
  * the bundle manifest (`index.json`) and the install view (`bundle.json`) agree
    with each other and with what is on disk -- enforced by the packer's --check
    mode, so a hand-edited bundle fails CI rather than shipping;
  * the preload refuses a bundle whose manifest digest does not match the packed
    content (the supply-chain check the manifest asks for);
  * every manifest carries the v0.2 blocks this milestone requires: upstream null,
    the four permission dimensions, requires.nodes with node_id + binding,
    i18n with zh-CN + en, update.channel=official with the node-schema exclusion,
    and a platform signature entry;
  * every declared reference resolves inside the package (no missing links),
    including the i18n glossaries and the license file.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manifest import node_requirements, validate_manifest                      # noqa: E402
from skills import content_digest, load_package                                 # noqa: E402
from skills.store import inventory_report                                       # noqa: E402

BUNDLE = ROOT / "skills" / "official"
INDEX = BUNDLE / "index.json"
INSTALL_VIEW = BUNDLE / "bundle.json"
PACKER = ROOT / "tools" / "pack_official_bundle.py"

# The five skills this bundle version ships, by content directory.
EXPECTED_DIRS = ["doctor", "embed-video", "lipsync-audio-refs", "prompt-craft", "video-15s"]
# Node definition ids the bundle is allowed to depend on, as the live registry
# names them (`GET /api/v1/nodes`). A typo here is a run-time blocking problem.
KNOWN_NODE_IDS = {
    "generate:minimax-h3", "generate:byteplus-seedance-2.0", "generate:drawnow",
    "generate:nb2-image", "generate:nb2-lite-image", "generate:gpt-image-2",
    "generate:tts-minimax-hd", "generate:tts-minimax-turbo", "generate:tts-deepgram",
}
PERMISSION_DIMENSIONS = ("egress", "filesystem", "exec", "secrets")


def load_index() -> dict:
    return json.loads(INDEX.read_text())


def skill_manifests() -> list[tuple[Path, dict]]:
    return [(d, json.loads((d / "manifest.json").read_text())) for d in sorted(BUNDLE.iterdir())
            if d.is_dir() and (d / "manifest.json").is_file()]


class BundleShapeTests(unittest.TestCase):
    def test_bundle_manifest_lists_exactly_the_packages_on_disk(self):
        index = load_index()
        self.assertEqual(index["bundle"], "skilyst-official")
        self.assertEqual(index["kind"], "official-bundle")
        self.assertEqual([e["path"] for e in index["skills"]], EXPECTED_DIRS)
        self.assertEqual([e["path"] for e in index["skills"]],
                         sorted(e["path"] for e in index["skills"]))
        self.assertTrue(index["signed_by"])

    def test_every_entry_has_identity_and_digest_that_matches_the_package(self):
        for entry in load_index()["skills"]:
            skill_dir = BUNDLE / entry["path"]
            manifest = json.loads((skill_dir / "manifest.json").read_text())
            digest = content_digest(skill_dir)
            self.assertEqual(entry["skill_id"], manifest["skill_id"], entry["path"])
            self.assertEqual(entry["version"], manifest["version"], entry["path"])
            self.assertEqual(entry["kind"], "official-bundle", entry["path"])
            self.assertEqual(entry["content_digest"], digest, entry["path"])
            self.assertEqual(manifest["content_digest"], digest, entry["path"])

    def test_install_view_agrees_with_the_bundle_manifest(self):
        index, install = load_index(), json.loads(INSTALL_VIEW.read_text())
        coordinates = lambda spec: sorted(  # noqa: E731 - one-line comparison key
            (e["path"], e["version"], e["content_digest"]) for e in spec["skills"])
        self.assertEqual(coordinates(index), coordinates(install))
        self.assertEqual(install["index"], "index.json")

    def test_packer_check_mode_is_clean(self):
        """The packer is the only writer; if it says clean, the tree is packed."""
        result = subprocess.run([sys.executable, str(PACKER), "--check"],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_packer_refuses_to_report_success_on_an_unsigned_package(self):
        """A missing platform signature is not a green run, even though the packer
        cannot invent the signature itself."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "official"
            shutil.copytree(BUNDLE, bundle)
            sidecar = bundle / "doctor" / "manifest.json"
            manifest = json.loads(sidecar.read_text())
            manifest["supply_chain"].pop("signatures")
            sidecar.write_text(json.dumps(manifest))
            result = subprocess.run([sys.executable, str(PACKER), "--check", "--bundle", str(bundle)],
                                    capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("no platform signature entry", result.stderr)

    def test_sidecar_edits_do_not_change_the_content_digest(self):
        """content_digest covers package content, manifest.json excluded -- so a
        metadata-only fix is a metadata-only change (the v0.2 self-reference fix)."""
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "official"
            shutil.copytree(BUNDLE, bundle)
            sidecar = bundle / "doctor" / "manifest.json"
            manifest = json.loads(sidecar.read_text())
            manifest["changelog"][0]["changes"] = "metadata-only edit"
            sidecar.write_text(json.dumps(manifest))
            self.assertEqual(content_digest(bundle / "doctor"),
                             content_digest(BUNDLE / "doctor"))


class ManifestV02Tests(unittest.TestCase):
    def test_every_manifest_validates_and_loads_strict(self):
        for skill_dir, manifest in skill_manifests():
            validate_manifest(manifest)                     # v0.2 field rules
            package = load_package(skill_dir)               # strict: raises on deviation
            self.assertEqual(package.warnings, (), skill_dir.name)
            self.assertFalse(package.degraded, skill_dir.name)

    def test_identity_blocks_are_official(self):
        for skill_dir, manifest in skill_manifests():
            self.assertIsNone(manifest["upstream"], skill_dir.name)      # original, not a fork
            self.assertEqual(manifest["kind"], "official-bundle", skill_dir.name)
            self.assertEqual(manifest["author"]["name"], "platform", skill_dir.name)
            self.assertTrue(manifest["supply_chain"]["signatures"], skill_dir.name)
            signature = manifest["supply_chain"]["signatures"][0]
            self.assertEqual(signature["key_id"], "skilyst-platform", skill_dir.name)
            self.assertEqual(signature["digest"], manifest["content_digest"], skill_dir.name)
            self.assertEqual(manifest["supply_chain"]["static_scan"]["status"], "passed")

    def test_permission_declares_all_four_dimensions_truthfully(self):
        for skill_dir, manifest in skill_manifests():
            permission = manifest["permission"]
            for dimension in PERMISSION_DIMENSIONS:
                self.assertIn(dimension, permission, skill_dir.name)
            self.assertIsInstance(permission["secrets"], bool, skill_dir.name)
            if permission["exec"] == "scripts-whitelist":
                scripts = skill_dir / "scripts"
                self.assertTrue(scripts.is_dir(), f"{skill_dir.name} declares exec but has no scripts/")
                self.assertTrue(any(p.is_file() for p in scripts.iterdir()), skill_dir.name)
            else:
                self.assertEqual(permission["exec"], "none", skill_dir.name)
            if permission["secrets"]:
                # a credential-taking skill must declare where it may go, and only there
                self.assertIsInstance(permission["egress"], list, skill_dir.name)
                self.assertTrue(permission["egress"], skill_dir.name)

    def test_node_requirements_use_node_ids_with_bindings(self):
        seen_bindings = 0
        for skill_dir, manifest in skill_manifests():
            for raw, requirement in zip(manifest["requires"]["nodes"],
                                        node_requirements(manifest), strict=True):
                self.assertIn(requirement.node_id, KNOWN_NODE_IDS,
                              f"{skill_dir.name}: {requirement.node_id} is not a live node definition id")
                self.assertNotIn("node_type", raw, f"{skill_dir.name}: v0.1 spelling republished")
                self.assertTrue(requirement.version_range, skill_dir.name)
                for fallback in requirement.fallback:
                    self.assertIn(fallback, KNOWN_NODE_IDS,
                                  f"{skill_dir.name}: fallback {fallback} is not a live node definition id")
                binding = raw.get("binding")
                self.assertIsInstance(binding, dict, f"{skill_dir.name}: {requirement.node_id} has no binding")
                self.assertEqual(binding["node_id"], requirement.node_id, skill_dir.name)
                self.assertTrue(binding["tool"], skill_dir.name)
                self.assertIsInstance(binding["config_map"], dict, skill_dir.name)
                seen_bindings += 1
        self.assertGreaterEqual(seen_bindings, len(EXPECTED_DIRS),
                                "every content skill declares at least one bound node")

    def test_knowledge_style_skills_declare_no_node_and_no_credential(self):
        by_dir = {d.name: m for d, m in skill_manifests()}
        for name in ("prompt-craft", "doctor"):
            manifest = by_dir[name]
            self.assertEqual(manifest["requires"]["nodes"], [], name)
        # prompt-craft is pure methodology: it must not be able to reach the network
        self.assertEqual(by_dir["prompt-craft"]["permission"]["egress"], "none")
        self.assertFalse(by_dir["prompt-craft"]["permission"]["secrets"])

    def test_i18n_meets_the_works_wall_gate_with_glossaries(self):
        for skill_dir, manifest in skill_manifests():
            i18n = manifest["i18n"]
            self.assertEqual(i18n["default_locale"], "zh-CN", skill_dir.name)
            self.assertIn("zh-CN", i18n["locales"], skill_dir.name)
            self.assertIn("en", i18n["locales"], skill_dir.name)
            term_keys = {}
            for locale, ref in i18n["glossary"].items():
                path = skill_dir / ref
                self.assertTrue(path.is_file(), f"{skill_dir.name}: {ref} missing")
                payload = json.loads(path.read_text())
                self.assertEqual(payload["locale"], locale, f"{skill_dir.name}/{ref}")
                terms = payload["terms"]
                self.assertTrue(terms, f"{skill_dir.name}/{ref} is empty")
                for term in terms:
                    for key in ("term_key", "source_term", "target_term", "note"):
                        self.assertIn(key, term, f"{skill_dir.name}/{ref}")
                term_keys[locale] = {t["term_key"] for t in terms}
            self.assertEqual(term_keys["zh-CN"], term_keys["en"],
                             f"{skill_dir.name}: the two glossaries cover different term keys")

    def test_update_channel_is_official_with_the_node_schema_exclusion(self):
        for skill_dir, manifest in skill_manifests():
            update = manifest["update"]
            self.assertEqual(update["channel"], "official", skill_dir.name)
            self.assertIn(update["policy"], ("auto", "manual", "pinned"), skill_dir.name)
            self.assertIn("node-schema", update["hot_reload"]["exclusions"], skill_dir.name)

    def test_license_file_exists_and_matches_the_declared_spdx(self):
        for skill_dir, manifest in skill_manifests():
            license_file = skill_dir / manifest["license"]["file"]
            self.assertTrue(license_file.is_file(), f"{skill_dir.name}: license file missing")
            text = license_file.read_text()
            self.assertIn(manifest["license"]["spdx"], text, skill_dir.name)

    def test_every_declared_reference_resolves_inside_the_package(self):
        for skill_dir, _manifest in skill_manifests():
            report = inventory_report(load_package(skill_dir))
            self.assertEqual(report["missing_references"], [], skill_dir.name)


class PreloadIntegrityTests(unittest.TestCase):
    def _copy_bundle(self, tmp: str) -> Path:
        target = Path(tmp) / "official"
        shutil.copytree(BUNDLE, target)
        return target

    def test_preload_verifies_the_bundle_manifest_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._copy_bundle(tmp)
            # consistency first, then the digest: tamper both manifests so the bundle
            # is self-consistent and only the packed content disagrees
            for name in ("index.json", "bundle.json"):
                payload = json.loads((bundle / name).read_text())
                payload["skills"][0]["content_digest"] = "sha256:" + "0" * 64
                (bundle / name).write_text(json.dumps(payload))
            from skills import SkillStore
            store = SkillStore(Path(tmp) / "store")
            with self.assertRaises(RuntimeError) as ctx:
                store.preload_official_bundle(bundle)
            self.assertIn("does not match the bundle manifest", str(ctx.exception))

    def test_preload_refuses_internally_inconsistent_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._copy_bundle(tmp)
            install = json.loads((bundle / "bundle.json").read_text())
            install["skills"][0]["version"] = "9.9.9"
            (bundle / "bundle.json").write_text(json.dumps(install))
            from skills import SkillStore
            store = SkillStore(Path(tmp) / "store")
            with self.assertRaises(RuntimeError) as ctx:
                store.preload_official_bundle(bundle)
            self.assertIn("internally inconsistent", str(ctx.exception))

    def test_preload_installs_every_bundle_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            from skills import SkillStore
            store = SkillStore(Path(tmp) / "store")
            loaded = store.preload_official_bundle(BUNDLE)
            self.assertEqual(sorted(p.skill_id for p in loaded),
                             sorted(f"skilyst/{name}" for name in EXPECTED_DIRS))
            self.assertTrue(all(row["ok"] for row in store.verify_all()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
