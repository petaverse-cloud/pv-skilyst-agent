"""#36 runtime beehive proxy — route table + scope-gate contract tests.

The proxy is the USER's data plane (webview → runtime → beehive, signed with
the keychain platform credential). These tests pin three properties:

1. route-table dispatch: whitelisted (method, path) pairs forward with
   forwarded query params; anything else is a loud 404, never a fallback.
2. the trusted-token carve-out is exactly one route: an exact GET on the
   wallet. Sub-paths, writes, and every other DENIED_PREFIX stay walled.
3. skill-facing tokens are untouched: skill_token() still refuses billing
   (D3 — agents never touch money), including via construction tricks.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import serve  # noqa: E402
from beehive.scope import (  # noqa: E402
    DESKTOP_PROXY_SCOPE,
    RestrictedToken,
    ScopeRefusal,
    skill_token,
)


class ProxyRouteTableTests(unittest.TestCase):
    """The (subpath, method, upstream) table — single source of truth."""

    def test_table_covers_the_shipped_routes(self):
        self.assertEqual(
            serve.RuntimeAPI.BEEHIVE_PROXY_ROUTES,
            (
                ("workflows", "GET", "/api/v1/workflows"),
                ("workflows/{}", "GET", "/api/v1/workflows/{}"),
                ("billing/wallet", "GET", "/api/v1/billing/wallet"),
            ),
        )

    def test_dispatch_matches_list_detail_and_wallet(self):
        api = serve.RuntimeAPI.__new__(serve.RuntimeAPI)  # no __init__ side effects
        calls = []

        def fake_get(upstream, params):
            calls.append((upstream, params))
            return {"stub": True}

        api._beehive_get = fake_get
        self.assertEqual(api.beehive_proxy("workflows", {"limit": "2"}),
                         {"stub": True})
        self.assertEqual(calls, [
            ("/api/v1/workflows", {"limit": "2"}),   # params forwarded for the caller to encode
        ])
        api.beehive_proxy("workflows/wf-123", {})
        self.assertEqual(calls[-1], ("/api/v1/workflows/wf-123", {}))
        api.beehive_proxy("billing/wallet", {})
        self.assertEqual(calls[-1], ("/api/v1/billing/wallet", {}))

    def test_id_segment_is_url_quoted_not_slash_stuffed(self):
        api = serve.RuntimeAPI.__new__(serve.RuntimeAPI)
        seen = []
        api._beehive_get = lambda upstream, params: seen.append(upstream)
        with self.assertRaises(serve.NotFound):
            api.beehive_proxy("workflows/a b/c", {})   # multi-segment "id" is not an id
        api.beehive_proxy("workflows/a b", {})
        self.assertEqual(seen, ["/api/v1/workflows/a%20b"])   # quoted, never raw

    def test_off_table_routes_fail_loud_404(self):
        api = serve.RuntimeAPI.__new__(serve.RuntimeAPI)
        for bad in ("jobs", "admin/users", "billing/wallet/history",
                    "auth/api-keys", "workflows/wf/x/lock"):
            with self.assertRaises(serve.NotFound, msg=bad):
                api.beehive_proxy(bad, {})


class TrustedTokenTests(unittest.TestCase):
    """The carve-out is exactly one route — everything else stays walled."""

    def test_desktop_proxy_scope_reads_wallet_exactly(self):
        tok = RestrictedToken("a", "b", scope=DESKTOP_PROXY_SCOPE, trusted=True)
        self.assertEqual(tok.allows("GET", "/api/v1/billing/wallet"), (True, "billing:read"))
        self.assertFalse(tok.allows("GET", "/api/v1/billing/wallet/history")[0])
        self.assertFalse(tok.allows("POST", "/api/v1/billing/wallet")[0])
        self.assertFalse(tok.allows("GET", "/api/v1/billing/nodes")[0])
        self.assertFalse(tok.allows("GET", "/api/v1/admin/users")[0])
        self.assertFalse(tok.allows("GET", "/api/v1/auth/api-keys")[0])

    def test_trusted_still_refuses_every_forbidden_write(self):
        with self.assertRaises(ScopeRefusal):
            RestrictedToken("a", "b", scope=("billing:read", "billing:write"), trusted=True)
        with self.assertRaises(ScopeRefusal):
            RestrictedToken("a", "b", scope=("admin:read",), trusted=True)

    def test_untrusted_tokens_never_get_billing_read(self):
        with self.assertRaises(ScopeRefusal):
            RestrictedToken("a", "b", scope=("billing:read",))
        # even the full preset minus `trusted` cannot be constructed —
        # billing:read in a token without the runtime's own mint is refused
        with self.assertRaises(ScopeRefusal):
            RestrictedToken("a", "b", scope=DESKTOP_PROXY_SCOPE)


class SkillFaceUnchangedTests(unittest.TestCase):
    """D3: skill_token() keeps refusing billing regardless of the proxy."""

    def test_skill_token_cannot_read_wallet(self):
        tok = skill_token("ak", "sk", account="u")
        self.assertFalse(tok.allows("GET", "/api/v1/billing/wallet")[0])
        self.assertFalse(tok.allows("GET", "/api/v1/billing/wallet/history")[0])

    def test_skill_token_scope_has_no_billing(self):
        tok = skill_token("ak", "sk", account="u")
        self.assertNotIn("billing:read", tok.scope)
        self.assertNotIn("billing:write", tok.scope)


if __name__ == "__main__":
    unittest.main()
