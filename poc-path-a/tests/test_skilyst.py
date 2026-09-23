"""Path A unit tests -- loader, validation, permission, scope gate, digest stability."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skilyst.credentials import BeehiveClient, RestrictedToken, ScopeRefusal
from skilyst.frontmatter import FrontmatterError, parse_yaml_subset
from skilyst.permission import PermissionGate, SandboxViolation, classify_resources
from skilyst.registry import content_digest
from skilyst.skill import SkillValidationError, is_hot_update, load_package, validate_manifest

POC = Path(__file__).resolve().parents[1]
OFFICIAL = POC / "bundle/official/video-15s"


class FrontmatterTests(unittest.TestCase):
    def test_plain_and_quoted(self):
        out = parse_yaml_subset('name: pdf\ndescription: "do PDF things"\nlicense: Proprietary. LICENSE.txt')
        self.assertEqual(out["name"], "pdf")
        self.assertEqual(out["description"], "do PDF things")
        self.assertTrue(out["license"].startswith("Proprietary"))

    def test_folded_block_and_nested_metadata(self):
        out = parse_yaml_subset("name: x\ndescription: >\n  line one\n  line two\nmetadata:\n  author: me\n  version: \"1.0\"")
        self.assertEqual(out["description"], "line one line two")
        self.assertEqual(out["metadata"], {"author": "me", "version": "1.0"})

    def test_inline_list_and_escaped_quotes(self):
        out = parse_yaml_subset('tags: [a, b]\ndescription: "he said \\"hi\\""')
        self.assertEqual(out["tags"], ["a", "b"])
        self.assertEqual(out["description"], 'he said "hi"')

    def test_missing_frontmatter_is_loud(self):
        with self.assertRaises(Exception):
            load_package_from_text("# no frontmatter")


def load_package_from_text(text):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "skilyst-random-name"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(text)
        return load_package(d, "sha256:x")


class ValidationTests(unittest.TestCase):
    def test_official_bundle_validates(self):
        package = load_package(OFFICIAL, content_digest(OFFICIAL))
        self.assertEqual(package.skill_id, "skilyst/video-15s")
        self.assertFalse(package.degraded)
        self.assertEqual(package.requires_nodes[0].node_type, "generate:minimax-h3")

    def test_name_must_match_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "pdf"
            d.mkdir()
            (d / "SKILL.md").write_text("---\nname: not-pdf\ndescription: x\n---\nbody\n")
            # community package (no manifest.json): loads in compat mode WITH a recorded warning
            package = load_package(d, "sha256:x")
            self.assertTrue(any("!= directory name" in w for w in package.warnings))
            # our own published package (manifest.json present): must be spec-clean -> hard error
            manifest = json.loads((OFFICIAL / "manifest.json").read_text())
            manifest["skill_id"] = "skilyst/not-pdf"
            manifest["description"] = "x"
            (d / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(SkillValidationError):
                load_package(d, "sha256:x")

    def test_real_community_package_loads_with_warnings(self):
        """Emily2040/seedance-2.0 ships name='seedance-20' in a dir named 'seedance-2.0'."""
        fixture = Path.home() / "t1-poc/fixtures/seedance-2.0"
        if not (fixture / "SKILL.md").is_file():
            self.skipTest("seedance fixture not present")
        package = load_package(fixture, "sha256:x")
        self.assertEqual(package.community.name, "seedance-20")
        self.assertTrue(package.degraded)
        self.assertTrue(package.warnings)
        self.assertEqual(package.community.metadata.get("version"), "6.7.0")

    def test_non_string_metadata_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "s"
            d.mkdir()
            (d / "SKILL.md").write_text("---\nname: s\ndescription: x\nmetadata:\n  author: me\n---\nbody\n")
            manifest = json.loads((OFFICIAL / "manifest.json").read_text())
            manifest["description"] = "x"
            manifest["skill_id"] = "skilyst/s"
            (d / "manifest.json").write_text(json.dumps(manifest))
            package = load_package(d, "sha256:x")
            self.assertEqual(package.community.metadata["author"], "me")

    def test_missing_upstream_field_is_rejected(self):
        manifest = json.loads((OFFICIAL / "manifest.json").read_text())
        del manifest["upstream"]
        with self.assertRaises(SkillValidationError):
            validate_manifest(manifest)

    def test_hot_reload_exclusion_enforced(self):
        manifest = json.loads((OFFICIAL / "manifest.json").read_text())
        manifest["update"]["hot_reload"]["exclusions"] = []
        with self.assertRaises(SkillValidationError):
            validate_manifest(manifest)

    def test_node_schema_change_forces_cold(self):
        old = json.loads((OFFICIAL / "manifest.json").read_text())
        new = json.loads((OFFICIAL / "manifest.json").read_text())
        new["requires"]["nodes"][0]["node_definition_version"] = ">=2.0.0"
        hot, reason = is_hot_update(old, new)
        self.assertFalse(hot)
        self.assertIn("cold", reason)

    def test_instruction_only_change_is_hot(self):
        old = json.loads((OFFICIAL / "manifest.json").read_text())
        new = json.loads((OFFICIAL / "manifest.json").read_text())
        new["version"] = "1.0.1"
        hot, _ = is_hot_update(old, new)
        self.assertTrue(hot)


class PermissionTests(unittest.TestCase):
    def test_egress_allowlist_blocks_unknown_host(self):
        gate = PermissionGate(OFFICIAL, {"egress": ["beehive-api.verse4.pet"], "filesystem": "workspace",
                                        "exec": "none", "secrets": True})
        gate.check_egress("https://beehive-api.verse4.pet/api/v1/jobs")
        with self.assertRaises(SandboxViolation):
            gate.check_egress("https://exfiltrate.example.com/steal")

    def test_exec_requires_scripts_dir(self):
        gate = PermissionGate(OFFICIAL, {"egress": "none", "filesystem": "skill-dir", "exec": "none", "secrets": False})
        with self.assertRaises(SandboxViolation):
            gate.check_exec(OFFICIAL / "SKILL.md")

    def test_secrets_gate_default_deny(self):
        gate = PermissionGate(OFFICIAL, {"egress": "none", "filesystem": "skill-dir", "exec": "none", "secrets": False})
        with self.assertRaises(SandboxViolation):
            gate.require_secrets()

    def test_resource_classification(self):
        d = POC / "bundle/official/video-15s"
        inv = classify_resources(d, "see references/i18n/glossary.en.json and https://beehive-api.verse4.pet/x")
        self.assertIn("https://beehive-api.verse4.pet/x", inv.remote)
        self.assertIn("references/i18n/glossary.en.json", inv.missing)


class ScopeGateTests(unittest.TestCase):
    def test_billing_and_admin_refused_client_side(self):
        token = RestrictedToken("ak-x", "sk-x")
        for method, path in [("GET", "/api/v1/admin/users"), ("GET", "/api/v1/billing/wallet"),
                             ("GET", "/api/v1/billing/history"), ("PUT", "/api/v1/billing/nodes/x/pricing"),
                             ("POST", "/api/v1/auth/api-keys")]:
            allowed, why = token.allows(method, path)
            self.assertFalse(allowed, path)
        self.assertTrue(token.allows("POST", "/api/v1/jobs")[0])
        self.assertTrue(token.allows("GET", "/api/v1/jobs/abc")[0])
        self.assertTrue(token.allows("GET", "/api/v1/assets")[0])

    def test_client_refuses_before_any_http(self):
        client = BeehiveClient("https://beehive-api.verse4.pet", token=RestrictedToken("ak-x", "sk-x"))
        with self.assertRaises(ScopeRefusal):
            client.request("GET", "/api/v1/admin/users")


class DigestTests(unittest.TestCase):
    def test_digest_is_stable_and_content_addressed(self):
        first = content_digest(OFFICIAL)
        self.assertEqual(first, content_digest(OFFICIAL))
        self.assertTrue(first.startswith("sha256:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
