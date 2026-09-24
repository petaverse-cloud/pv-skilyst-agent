"""Issue #8: login credentials (AgentScopes AK/SK from the auth store) take
precedence over dev env credentials in resolve()."""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config
from auth import AuthFlow, TokenRecord, TokenStore


class _TmpStore(TokenStore):
    def __init__(self, tmpdir: Path):
        super().__init__(home=tmpdir)
        self._keychain_ok = False

    def _keychain_available(self) -> bool:
        return self._keychain_ok


class LoginCredentialLayerTests(unittest.TestCase):
    def _login(self, tmpdir: Path) -> AuthFlow:
        store = _TmpStore(tmpdir)
        store.save(TokenRecord(
            access_token="ak-login-1", account_uid="77", account_name="wes",
            scopes=["jobs:read"], expires_at=time.time() + 3600,
            storage="dev-file", access_key="ak-login-1", secret_key="sk-login-1"))
        return AuthFlow(store=store, mock=True)

    def test_logged_in_key_wins_over_dev_env(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._login(tmp)
            # dev env carries a DIFFERENT key — the login must win
            env = tmp / "env"
            env.write_text("BEEHIVE_PLATFORM_AK=ak-dev-1\nBEEHIVE_PLATFORM_SK=sk-dev-1\n")
            os.environ["SKILYST_AUTH_STORE_HOME"] = str(tmp)
            try:
                cfg = config.resolve(env, require_llm=False, require_beehive=True)
                self.assertEqual(cfg.beehive.access_key, "ak-login-1")
                self.assertEqual(cfg.beehive.secret_key, "sk-login-1")
                self.assertIn("login", cfg.beehive.source)
            finally:
                os.environ.pop("SKILYST_AUTH_STORE_HOME", None)

    def test_not_logged_in_falls_back_to_dev_env(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            # no login record — dev env must be used as before
            env = tmp / "env"
            env.write_text("BEEHIVE_PLATFORM_AK=ak-dev-2\nBEEHIVE_PLATFORM_SK=sk-dev-2\n")
            os.environ["SKILYST_AUTH_STORE_HOME"] = str(tmp)
            try:
                cfg = config.resolve(env, require_llm=False, require_beehive=True)
                self.assertEqual(cfg.beehive.access_key, "ak-dev-2")
            finally:
                os.environ.pop("SKILYST_AUTH_STORE_HOME", None)


if __name__ == "__main__":
    unittest.main()
