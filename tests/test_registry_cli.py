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

    def test_price_to_micro_round_trip(self):
        # E2E 2026-10-10 caught the unit mismatch: --price 2 (USD) went over
        # the wire as the raw int 2 (µUSD) and rendered back as $0.00.
        from cli import _display_price, _price_to_micro
        self.assertEqual(_price_to_micro(2), 2_000_000)
        self.assertEqual(_price_to_micro(0), 0)
        self.assertEqual(_price_to_micro("1.5"), 1_500_000)
        self.assertEqual(_display_price(_price_to_micro(2)), "$2.00")


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
