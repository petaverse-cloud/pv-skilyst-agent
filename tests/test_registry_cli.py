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


if __name__ == "__main__":
    unittest.main()
