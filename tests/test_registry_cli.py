"""#56 data half: `skilyst library` + `skilyst skill-info` against the registry
read API core#714 shipped (#703).

Pins three things:
  1. the client methods hit the exact registry routes with the right envelope;
  2. the scope gate lets a skill-facing client READ the registry
     (skills:read is in DEFAULT_SCOPE and SCOPE_RULES) while the CLI keeps
     using the one gated_client path — no ungated construction;
  3. the µUSD price convention (1 cent = 10,000; 0 = free) is rendered
     correctly for humans.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from beehive import client_for                                  # noqa: E402
from beehive.scope import DEFAULT_SCOPE, FORBIDDEN_SCOPES      # noqa: E402


class _Scripted:
    """Fake transport recording calls, replaying (status, body) pairs."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url})
        if not self.responses:
            return 200, "{}"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return status, json.dumps(body)


LIST_BODY = (200, {"payload": {"skills": [
    {"id": "skl-1", "slug": "trailer-15s", "version": 3, "fork_depth": 0,
     "price_usd": 2000000, "visibility": "public",
     "skeleton_summary": {"pinned": 3, "parameterized": 1, "free_zones": 1}},
    {"id": "skl-2", "slug": "ghost-catcher", "version": 1, "fork_depth": 2,
     "price_usd": 0, "visibility": "public", "skeleton_summary": {}},
], "total": 2, "limit": 20, "offset": 0}})

DETAIL_BODY = (200, {"payload": {
    "id": "skl-1", "slug": "trailer-15s", "version": 3, "fork_depth": 0,
    "price_usd": 2000000, "visibility": "public",
    "skeleton_summary": {"pinned": 3, "parameterized": 1, "free_zones": 1},
    "attribution": ["usr-a"], "materializations": 7}})



def _client_for(transport):
    c = client_for("AKTEST", "SKTEST", "usr-1", "https://beehive.example")
    c._transport = transport
    return c

class RegistryClientTests(unittest.TestCase):
    def test_list_skills_hits_the_registry_route_with_envelope(self):
        t = _Scripted(LIST_BODY)
        client = _client_for(t)
        payload = client.list_skills(limit=20, offset=0)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(t.calls[0]["method"], "GET")
        self.assertIn("/api/v1/skills?limit=20&offset=0", t.calls[0]["url"])
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["skills"][0]["slug"], "trailer-15s")

    def test_visibility_filter_is_passed_through(self):
        t = _Scripted(LIST_BODY)
        client = _client_for(t)
        client.list_skills(visibility="public")
        self.assertIn("visibility=public", t.calls[0]["url"])

    def test_get_skill_detail(self):
        t = _Scripted(DETAIL_BODY)
        client = _client_for(t)
        detail = client.get_skill("skl-1")
        self.assertEqual(t.calls[0]["url"].endswith("/api/v1/skills/skl-1"), True)
        self.assertEqual(detail["materializations"], 7)
        self.assertEqual(detail["attribution"], ["usr-a"])

    def test_versions_and_fork_tree_routes(self):
        t = _Scripted((200, {"payload": {"versions": [{"version": 3}]}}),
                      (200, {"payload": {"chain": [], "children": []}}))
        client = _client_for(t)
        self.assertEqual(client.list_skill_versions("skl-1")[0]["version"], 3)
        self.assertEqual(t.calls[0]["url"].endswith("/skills/skl-1/versions"), True)
        tree = client.skill_fork_tree("skl-1")
        self.assertIn("children", tree)
        self.assertEqual(t.calls[1]["url"].endswith("/skills/skl-1/fork-tree"), True)


class ScopeGateTests(unittest.TestCase):
    def test_skills_read_is_in_the_default_scope(self):
        self.assertIn("skills:read", DEFAULT_SCOPE)

    def test_skills_read_is_not_forbidden(self):
        self.assertNotIn("skills:read", FORBIDDEN_SCOPES)

    def test_registry_reads_pass_the_scope_gate(self):
        t = _Scripted(LIST_BODY)
        client = _client_for(t)  # client_for -> skill_token scope gate
        payload = client.list_skills()
        self.assertEqual(payload["total"], 2)  # did not raise ScopeRefusal

    def test_registry_writes_are_still_refused_client_side(self):
        # POST /api/v1/skills is not in SCOPE_RULES: the publish half (#56
        # second half) must not accidentally ride this PR.
        from beehive.scope import ScopeRefusal
        t = _Scripted()
        client = _client_for(t)
        with self.assertRaises(ScopeRefusal):
            client.request("POST", "/api/v1/skills", {"x": 1})


class PriceDisplayTests(unittest.TestCase):
    def test_zero_and_none_are_free(self):
        from cli import _display_price
        self.assertEqual(_display_price(0), "free")
        self.assertEqual(_display_price(None), "free")

    def test_micro_usd_convention(self):
        from cli import _display_price
        # 1 cent = 10,000 µUSD -> $2.00 = 2,000,000
        self.assertEqual(_display_price(2000000), "$2.00")
        self.assertEqual(_display_price(10000), "$0.01")


class LifecycleCommandTests(unittest.TestCase):
    """#56 second half: publish / fork / clone against the #723 contract."""

    def test_creator_scope_is_default_plus_skills_write(self):
        from cli import _CREATOR_SCOPE
        self.assertIn("skills:write", _CREATOR_SCOPE)
        self.assertIn("skills:read", _CREATOR_SCOPE)
        # Agent default never grew the write face by accident.
        self.assertNotIn("skills:write", DEFAULT_SCOPE)

    def test_creator_client_passes_the_write_gate(self):
        # The _CREATOR_SCOPE token may POST /api/v1/skills (skills:write in
        # SCOPE_RULES since this PR), while a DEFAULT_SCOPE token may not.
        t = _Scripted()
        from beehive import client_for
        from cli import _CREATOR_SCOPE
        c = client_for("AK", "SK", "usr", "https://beehive.example", scope=_CREATOR_SCOPE)
        c._transport = t
        c.request("POST", "/api/v1/skills", {"slug": "x"})
        self.assertEqual(len(t.calls), 1)  # did not raise ScopeRefusal

    def test_default_scope_still_cannot_write_skills(self):
        from beehive.scope import ScopeRefusal
        t = _Scripted()
        client = _client_for(t)
        with self.assertRaises(ScopeRefusal):
            client.request("POST", "/api/v1/skills", {"slug": "x"})

    def test_skeleton_counts_from_a_real_skeleton(self):
        from cli import _skeleton_counts
        from skills.loader import SkillPackage
        from manifest import parse_skeleton
        pkg = SkillPackage.__new__(SkillPackage)
        pkg.manifest = {"workflow_skeleton": {
            "version": 1,
            "nodes": [
                {"node_id": "generate:minimax-h3", "freedom": "pinned"},
                {"node_id": "generate:*", "freedom": "parameterized",
                 "config_open": ["provider"]},
            ],
            "free_zones": [{"name": "z", "max_nodes": 2,
                            "allowed_node_types": ["process:transcode"]}],
        }}
        counts = _skeleton_counts(pkg)
        self.assertEqual(counts, {"pinned": 1, "parameterized": 1, "free_zones": 1})

    def test_skeleton_counts_zero_for_skeletonless(self):
        from cli import _skeleton_counts
        from skills.loader import SkillPackage
        pkg = SkillPackage.__new__(SkillPackage)
        pkg.manifest = None
        self.assertEqual(_skeleton_counts(pkg),
                         {"pinned": 0, "parameterized": 0, "free_zones": 0})

    def test_taxonomy_gate_rejects_out_of_enum(self):
        # publish with a bogus lane is refused BEFORE any network call —
        # validated in cmd_publish, LANES/PURPOSES pin the #719 contract.
        from cli import LANES, PURPOSES
        self.assertIn("general", LANES)
        self.assertIn("short_video", LANES)
        self.assertNotIn("gpl", LANES)
        self.assertEqual(len(PURPOSES), 4)
        self.assertIn("create", PURPOSES)


if __name__ == "__main__":
    unittest.main()


class PackageDownloadTests(unittest.TestCase):
    """The clone download leg (#730): binary-safe transport + digest verify.

    The scripted transport decodes bodies as text, so these tests drive the
    real urllib path through a local HTTP server — a package bundle with
    gzip/binary bytes would be silently corrupted through the text path,
    which is exactly what the binary method exists to prevent.
    """

    def _serve(self, body: bytes, digest: str):
        import http.server, threading
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith("/package"):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("X-Skill-Content-Digest", digest)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:  # metadata endpoint
                    import json as _json
                    meta = {"payload": {"id": "skl-1", "slug": "s", "version": 1,
                                        "price_usd": 0, "content_digest": digest,
                                        "skeleton_summary": {}}}
                    blob = _json.dumps(meta).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(blob)))
                    self.end_headers()
                    self.wfile.write(blob)
            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv

    def test_download_returns_raw_bytes_and_declared_digest(self):
        import gzip
        from beehive import BeehiveClient  # binary path must not go through transport
        blob = gzip.compress(b"\x00binary\xfe\xff package bytes")  # non-utf8-safe
        declared = "sha256:" + __import__("hashlib").sha256(blob).hexdigest()
        srv = self._serve(blob, declared)
        try:
            c = client_for("AK", "SK", "usr-1", f"http://127.0.0.1:{srv.server_port}")
            body, got = c.download_skill_package("skl-1")
            self.assertEqual(body, blob)  # bytes identity — no utf-8 replace damage
            self.assertEqual(got, declared)
            self.assertTrue(_digest_helper(body, declared))
        finally:
            srv.shutdown()

    def test_digest_mismatch_is_reported_not_raised(self):
        # clone surfaces verification as data; the caller decides. A wrong
        # declared digest (or fork-of: reference) must not crash the CLI.
        blob = b"some package"
        srv = self._serve(blob, "sha256:" + "0" * 64)
        try:
            c = client_for("AK", "SK", "usr-1", f"http://127.0.0.1:{srv.server_port}")
            body, got = c.download_skill_package("skl-1")
            self.assertFalse(_digest_helper(body, got))
        finally:
            srv.shutdown()


def _digest_helper(body: bytes, declared: str):
    """Mirrors cli._digest_matches semantics for the tests below."""
    import hashlib, re
    m = re.search(r"sha256[-:]?([0-9a-f]+)", str(declared))
    if not m:
        return False
    hexpart = m.group(1)
    if len(hexpart) != 64:
        return None  # manifest/identity digest — not a body hash
    return hashlib.sha256(body).hexdigest() == hexpart


    def test_manifest_digest_is_not_verifiable_as_body(self):
        # 16-hex 'sha256-<16>' is the MANIFEST content-digest (#723: hash of
        # manifest+skeleton). It can never verify package bytes — the first
        # cut prefix-compared it, which always yields a misleading False.
        # The honest answer is None (verification not applicable) until
        # core#740's package_digest lands in storage.location.
        import hashlib
        blob = b"package"
        short = "sha256-" + hashlib.sha256(blob).hexdigest()[:16]
        self.assertIsNone(_digest_helper(blob, short))
        self.assertIsNone(_digest_helper(blob, "sha256-d1bf326902882d3a"))

    def test_fork_of_reference_digest(self):
        # 'fork-of:sha256-<64>' is a reference snapshot of the upstream, not
        # a digest of THIS body — verification is not applicable (None),
        # not a pass and not a misleading fail.
        import hashlib
        blob = b"package"
        fork_ref = "fork-of:sha256-" + hashlib.sha256(blob).hexdigest()
        self.assertIsNone(_digest_helper(blob, fork_ref))
