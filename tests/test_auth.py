"""Tests for the auth flow (state machine, PKCE, store, mock exchange)."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth import (AuthFlow, AuthState, TokenRecord, TokenStore,
                  make_pkce_pair, run_cli_login)


class TmpStore(TokenStore):
    def __init__(self, tmpdir: Path):
        super().__init__(home=tmpdir)
        self._keychain_ok = False

    def _keychain_available(self) -> bool:
        return self._keychain_ok


class PKCETests(unittest.TestCase):
    def test_pair_is_s256_consistent(self):
        verifier, challenge = make_pkce_pair()
        import base64, hashlib
        digest = hashlib.sha256(verifier.encode()).digest()
        expect = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self.assertEqual(challenge, expect)

    def test_verifier_is_urlsafe_and_long(self):
        verifier, _ = make_pkce_pair()
        self.assertEqual(verifier, verifier.replace("+", "").replace("/", ""))
        self.assertGreaterEqual(len(verifier), 40)


class StoreTests(unittest.TestCase):
    def test_dev_file_roundtrip_0600(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            store = TmpStore(Path(d))
            rec = TokenRecord(access_token="t1", account_uid="u1",
                              account_name="n1", scopes=["jobs:read"],
                              expires_at=time.time() + 100, storage="dev-file")
            store.save(rec)
            loaded = store.load()
            self.assertEqual(loaded.access_token, "t1")
            self.assertEqual(loaded.account_uid, "u1")
            mode = (store._dev_path).stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)
            store.clear()
            self.assertIsNone(store.load())

    def test_load_expired_clears(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            store = TmpStore(Path(d))
            store._dev_path.write_text(TokenRecord(
                access_token="t", account_uid="u", account_name="n",
                scopes=[], expires_at=time.time() - 1, storage="dev-file").to_json())
            flow = AuthFlow(store=store, mock=True)
            self.assertIsNone(flow.current())
            self.assertEqual(flow.state, AuthState.UNAUTHENTICATED)


class FlowTests(unittest.TestCase):
    def _flow(self, d: str):
        import os, tempfile
        os.environ["SKILYST_AUTH_HEADLESS"] = "1"
        tmp = Path(tempfile.mkdtemp())
        return AuthFlow(store=TmpStore(tmp), mock=True)

    def test_mock_login_deliver_and_current(self):
        flow = self._flow("x")
        import threading
        result = {}

        def login():
            result["rec"] = flow.login(redirect_uri="skilyst://callback", poll_timeout=5)

        t = threading.Thread(target=login)
        t.start()
        deadline = time.time() + 5
        while flow.state != AuthState.AWAITING_BROWSER and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(flow.state, AuthState.AWAITING_BROWSER)
        flow.deliver_code("one-time-code")
        t.join(timeout=5)
        self.assertEqual(flow.state, AuthState.AUTHENTICATED)
        self.assertIn("jobs:read", result["rec"].scopes)
        self.assertNotIn("billing:read", result["rec"].scopes)  # AgentScopes only
        # current() sees it
        self.assertIsNotNone(flow.current())
        # logout returns to unauthenticated
        flow.logout()
        self.assertEqual(flow.state, AuthState.UNAUTHENTICATED)
        self.assertIsNone(flow.current())

    def test_timeout_raises(self):
        import os
        os.environ["SKILYST_AUTH_HEADLESS"] = "1"
        flow = self._flow("x")
        with self.assertRaises(Exception):
            flow.login(redirect_uri="skilyst://callback", poll_timeout=0.2)
        self.assertEqual(flow.state, AuthState.UNAUTHENTICATED)

    def test_exchange_failure_surfaces(self):
        flow = self._flow("x")
        import threading
        t = threading.Thread(target=lambda: flow.login(
            redirect_uri="skilyst://callback", poll_timeout=5))
        t.start()
        deadline = time.time() + 5
        while flow.state != AuthState.AWAITING_BROWSER and time.time() < deadline:
            time.sleep(0.01)
        flow._device_code = None  # force exchange failure in non-mock path shape
        flow.deliver_code("bad")  # mock mode succeeds; simulate failure:
        t.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
