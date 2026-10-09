"""Issue #37: the keychain item must hold the FULL AK\\0SK pair; the meta
file carries no secrets. Legacy layouts (keychain=AK only, SK plaintext in
the meta file) migrate in place on load(), idempotently."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auth import TokenRecord, TokenStore


class _FakeSecurity:
    """In-memory `security` CLI: records add-generic-password writes."""

    def __init__(self):
        self.items = {}  # (account, service) -> password

    def run(self, cmd, **kwargs):
        if "add-generic-password" in cmd:
            i = cmd.index("-a")
            account, service = cmd[i + 1], cmd[i + 3]
            w = cmd.index("-w")
            self.items[(account, service)] = cmd[w + 1]
            return mock.Mock(returncode=0, stdout="")
        if "find-generic-password" in cmd:
            i = cmd.index("-a")
            account, service = cmd[i + 1], cmd[i + 3]
            val = self.items.get((account, service))
            if val is None:
                return mock.Mock(returncode=44, stdout="")
            return mock.Mock(returncode=0, stdout=val + "\n")
        if "delete-generic-password" in cmd:
            i = cmd.index("-a")
            account, service = cmd[i + 1], cmd[i + 3]
            self.items.pop((account, service), None)
            return mock.Mock(returncode=0, stdout="")
        return mock.Mock(returncode=0, stdout="")


class _KeychainStore(TokenStore):
    """TokenStore with the keychain branch force-enabled and `security`
    stubbed — exercises the real save/load logic, only the OS call is faked."""

    def __init__(self, tmpdir: Path, fake: _FakeSecurity):
        super().__init__(home=tmpdir)
        self._fake = fake

    def _keychain_available(self) -> bool:
        return True


class KeychainPairTests(unittest.TestCase):
    def _record(self, ak="ak-1", sk="sk-1", uid="77"):
        return TokenRecord(
            access_token=ak, account_uid=uid, account_name="wes",
            scopes=["jobs:read"], expires_at=time.time() + 3600,
            storage="keychain", access_key=ak, secret_key=sk)

    def test_save_stores_full_pair_in_keychain_not_meta(self):
        with tempfile.TemporaryDirectory() as d:
            fake = _FakeSecurity()
            with mock.patch("subprocess.run", side_effect=fake.run):
                store = _KeychainStore(Path(d), fake)
                store.save(self._record())
                # the pair lives as TWO keychain items, nothing in the meta
                self.assertEqual(fake.items[("77", "skilyst-agent")], "ak-1")
                self.assertEqual(fake.items[("77", "skilyst-agent-secret")], "sk-1")
                # the meta file carries NO secret material
                m = json.loads((Path(d) / "credentials.meta").read_text())
                self.assertNotIn("secret_key", m)
                self.assertNotIn("access_key", m)
                # round-trip: load reassembles the pair
                rec = store.load()
                self.assertEqual((rec.access_key, rec.secret_key), ("ak-1", "sk-1"))

    def test_legacy_meta_migrates_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            fake = _FakeSecurity()
            fake.items[("77", "skilyst-agent")] = "ak-old"  # legacy: AK only
            meta = Path(d) / "credentials.meta"
            meta.write_text(json.dumps({
                "account_uid": "77", "account_name": "wes", "scopes": ["jobs:read"],
                "expires_at": time.time() + 3600, "storage": "keychain",
                "secret_key": "sk-old-plaintext"}))
            meta.chmod(0o600)
            with mock.patch("subprocess.run", side_effect=fake.run):
                store = _KeychainStore(Path(d), fake)
                rec = store.load()
                # pair reassembled correctly
                self.assertEqual((rec.access_key, rec.secret_key), ("ak-old", "sk-old-plaintext"))
                # SK promoted into its own keychain item
                self.assertEqual(fake.items[("77", "skilyst-agent-secret")], "sk-old-plaintext")
                # plaintext SK stripped from disk — the red line
                m = json.loads(meta.read_text())
                self.assertNotIn("secret_key", m)
                # idempotent: a second load finds the new layout, no rewrite
                rec2 = store.load()
                self.assertEqual((rec2.access_key, rec2.secret_key), ("ak-old", "sk-old-plaintext"))

    def test_legacy_meta_without_sk_degrades_loudly_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            fake = _FakeSecurity()
            fake.items[("77", "skilyst-agent")] = "ak-lone"
            meta = Path(d) / "credentials.meta"
            meta.write_text(json.dumps({
                "account_uid": "77", "scopes": [], "expires_at": time.time() + 3600,
                "storage": "keychain"}))
            with mock.patch("subprocess.run", side_effect=fake.run):
                store = _KeychainStore(Path(d), fake)
                rec = store.load()
                self.assertEqual(rec.access_key, "ak-lone")
                self.assertEqual(rec.secret_key, "")

    def test_skless_save_stale_item_cleanup(self):
        """#44 / review note (verify): saving without an SK must remove any
        stale SK item — mixed new-AK/old-SK pairs must be impossible by
        construction. The exact latent shape #44 describes: save full pair,
        then a half pair — load() must NOT reassemble new-AK + old-SK."""
        with tempfile.TemporaryDirectory() as d:
            fake = _FakeSecurity()
            with mock.patch("subprocess.run", side_effect=fake.run):
                store = _KeychainStore(Path(d), fake)
                store.save(self._record())
                self.assertEqual(fake.items[("77", "skilyst-agent-secret")], "sk-1")
                # a save without an SK (no call site does this today) must
                # not leave the old SK item behind
                store.save(self._record(ak="ak-2", sk="", uid="77"))
                self.assertNotIn(("77", "skilyst-agent-secret"), fake.items)
                rec = store.load()
                self.assertEqual((rec.access_key, rec.secret_key), ("ak-2", ""))


if __name__ == "__main__":
    unittest.main()
