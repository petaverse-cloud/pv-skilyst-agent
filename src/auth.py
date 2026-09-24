"""Auth flow for Skilyst — device-code deep-link login (Figma-style).

State machine + PKCE + loopback listener (CLI) + mock mode.
Design: docs/auth-deep-link.md. Protocol endpoints (§五) land in beehive-core
separately; until then SKILYST_MOCK_AUTH=1 exercises the full flow locally.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# PKCE (RFC 7636)
# ---------------------------------------------------------------------------

def make_pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge_s256)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Token store — plaintext never hits disk; keychain in production, this store
# holds only mock/dev tokens under 0600 with an explicit plaintext marker so a
# real exchange that falls back to it is loud about it.
# ---------------------------------------------------------------------------

@dataclass
class TokenRecord:
    access_token: str
    account_uid: str
    account_name: str
    scopes: list
    expires_at: float
    storage: str = "keychain"  # or "dev-file" | "mock"
    # A2 auth final contract (beehive-core #596): the exchange product is an
    # AgentScopes AK/SK pair, not a JWT — a JWT would bypass ScopeGuard (D3).
    access_key: str = ""
    secret_key: str = ""

    def to_json(self) -> str:
        return json.dumps(self.__dict__)

    @classmethod
    def from_json(cls, raw: str) -> "TokenRecord":
        return cls(**json.loads(raw))


class TokenStore:
    """OS keychain via `security` (macOS) in production; dev-file fallback."""

    SERVICE = "skilyst-agent"

    def __init__(self, home: Optional[Path] = None):
        # SKILYST_AUTH_STORE_HOME lets tests (and multi-install setups) point
        # the store somewhere other than the default ~/.skilyst.
        default = os.environ.get("SKILYST_AUTH_STORE_HOME") or Path.home() / ".skilyst"
        self.home = Path(home or default)
        self.home.mkdir(parents=True, exist_ok=True)
        self._dev_path = self.home / "credentials"

    # -- keychain (macOS first-class; Windows/Linux fall back with a loud marker)

    def _keychain_available(self) -> bool:
        if os.uname().sysname != "Darwin":
            return False
        return subprocess.run(["which", "security"], capture_output=True).returncode == 0

    def save(self, record: TokenRecord) -> None:
        if record.storage == "keychain" and self._keychain_available():
            subprocess.run(
                ["security", "add-generic-password",
                 "-a", record.account_uid, "-s", self.SERVICE,
                 "-U", "-w", record.access_key or record.access_token],
                check=True, capture_output=True)
            meta = self.home / "credentials.meta"
            meta.write_text(json.dumps({
                "account_uid": record.account_uid, "account_name": record.account_name,
                "scopes": record.scopes, "expires_at": record.expires_at,
                "storage": "keychain", "secret_key": record.secret_key}))
            meta.chmod(0o600)
            return
        # fallback: 0600 dev file, explicitly marked
        record.storage = "dev-file"
        self._dev_path.write_text(record.to_json())
        self._dev_path.chmod(0o600)

    def load(self) -> Optional[TokenRecord]:
        meta = self.home / "credentials.meta"
        if meta.is_file():
            m = json.loads(meta.read_text())
            r = subprocess.run(
                ["security", "find-generic-password",
                 "-a", m.get("account_uid", ""), "-s", self.SERVICE, "-w"],
                capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return TokenRecord(
                    access_token=r.stdout.strip(), account_uid=m["account_uid"],
                    account_name=m.get("account_name", ""), scopes=m.get("scopes", []),
                    expires_at=m.get("expires_at", 0), storage="keychain",
                    access_key=r.stdout.strip(), secret_key=m.get("secret_key", ""))
        if self._dev_path.is_file():
            rec = TokenRecord.from_json(self._dev_path.read_text())
            if rec.storage == "dev-file":
                return rec
        return None

    def clear(self) -> None:
        meta = self.home / "credentials.meta"
        if meta.is_file():
            m = json.loads(meta.read_text())
            subprocess.run(
                ["security", "delete-generic-password",
                 "-a", m.get("account_uid", ""), "-s", self.SERVICE],
                capture_output=True)
            meta.unlink(missing_ok=True)
        self._dev_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class AuthState:
    UNAUTHENTICATED = "unauthenticated"
    AWAITING_BROWSER = "awaiting_browser"
    EXCHANGING = "exchanging"
    AUTHENTICATED = "authenticated"


class AuthError(Exception):
    pass


class AuthFlow:
    """Device-code login with deep-link (GUI) or loopback (CLI) callback."""

    def __init__(self, store: Optional[TokenStore] = None,
                 base_url: str = "https://bee.verse4.pet",
                 mock: Optional[bool] = None):
        self.store = store or TokenStore()
        self.base_url = base_url.rstrip("/")
        self.mock = (os.environ.get("SKILYST_MOCK_AUTH") == "1") if mock is None else mock
        self.state = AuthState.UNAUTHENTICATED
        self._verifier: Optional[str] = None
        self._device_code: Optional[str] = None
        self._headless = os.environ.get("SKILYST_AUTH_HEADLESS") == "1"

    # -- current identity ---------------------------------------------------

    def current(self) -> Optional[TokenRecord]:
        rec = self.store.load()
        if rec and rec.expires_at > time.time():
            self.state = AuthState.AUTHENTICATED
            return rec
        if rec:
            self.store.clear()  # expired
        self.state = AuthState.UNAUTHENTICATED
        return None

    # -- login --------------------------------------------------------------

    def login(self, redirect_uri: str, poll_timeout: float = 300.0) -> TokenRecord:
        """Start login. For GUI use redirect_uri='skilyst://callback' (deep link
        delivers the code separately); for CLI use 'loopback:<port>'."""
        verifier, challenge = make_pkce_pair()
        self._verifier = verifier

        if self.mock:
            self._device_code = "dc_mock_" + secrets.token_hex(8)
            launch = f"{self.base_url}/login?device_code={self._device_code}&mock=1"
        else:
            resp = _post_json(f"{self.base_url}/api/v1/auth/device-code", {
                "client": "skilyst-agent", "code_challenge": challenge,
                "redirect_uri": redirect_uri})
            self._device_code = resp["device_code"]
            launch = f"{self.base_url}/login?device_code={self._device_code}&client=skilyst-agent"
        self.state = AuthState.AWAITING_BROWSER
        if not self._headless:
            webbrowser.open(launch)
        return self._await_exchange(poll_timeout)

    def _await_exchange(self, poll_timeout: float) -> TokenRecord:
        """CLI loopback: block until the local listener got the code and we
        exchanged it. GUI: deep-link handler calls deliver_code() separately;
        the same wait applies."""
        self._exchange_event = threading.Event()
        self._exchange_error = None
        self._exchange_result = None
        if not self._exchange_event.wait(timeout=poll_timeout):
            self.state = AuthState.UNAUTHENTICATED
            raise AuthError("login timed out waiting for browser callback")
        if self._exchange_error:
            self.state = AuthState.UNAUTHENTICATED
            raise AuthError(self._exchange_error)
        rec = self._exchange_result
        self.store.save(rec)
        self.state = AuthState.AUTHENTICATED
        return rec

    def deliver_code(self, code: str) -> None:
        """Called by deep-link handler (GUI) or loopback listener (CLI) with
        the one-time code from the browser redirect."""
        self.state = AuthState.EXCHANGING
        try:
            if self.mock:
                _ak = "ak-mock-" + secrets.token_hex(8)
                _sk = "sk-mock-" + secrets.token_hex(16)
                resp = {
                    "access_key": _ak, "secret_key": _sk,
                    "account": {"uid": "0", "name": "mock-user"},
                    "scopes": ["jobs:read", "jobs:write", "assets:read",
                               "assets:write", "workflows:read", "workflows:write"],
                    "expires_in": 86400,
                }
            else:
                resp = _post_json(f"{self.base_url}/api/v1/auth/token", {
                    "device_code": self._device_code, "code": code,
                    "code_verifier": self._verifier})
            acct = resp.get("account") or {}
            self._exchange_result = TokenRecord(
                access_token=resp.get("access_key", ""),  # AK/SK contract: the pair IS the token
                account_uid=str(acct.get("uid", "")),
                account_name=acct.get("name", ""),
                scopes=resp.get("scopes", []),
                expires_at=time.time() + resp.get("expires_in", 86400),
                storage="mock" if self.mock else "keychain",
                access_key=resp.get("access_key", ""),
                secret_key=resp.get("secret_key", ""))
            self._exchange_error = None
        except Exception as e:  # noqa: BLE001 — surface any failure to the waiter
            self._exchange_error = f"token exchange failed: {e}"
        getattr(self, "_exchange_event").set()

    # -- logout -------------------------------------------------------------

    def logout(self) -> None:
        self.store.clear()
        self.state = AuthState.UNAUTHENTICATED


def _post_json(url: str, payload: dict) -> dict:
    import urllib.request
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# CLI loopback listener
# ---------------------------------------------------------------------------

def run_cli_login(flow: Optional[AuthFlow] = None) -> TokenRecord:
    """`skilyst login` — loopback listener + browser + exchange."""
    import http.server

    flow = flow or AuthFlow()
    got: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            if self.path.startswith("/callback"):
                got["code"] = q.get("code", [""])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Skilyst login received - you can close this tab.</h2>")
                done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    result: dict = {}

    def waiter():
        if done.wait(timeout=300):
            flow.deliver_code(got["code"])
        result["done"] = True

    threading.Thread(target=waiter, daemon=True).start()
    try:
        rec = flow.login(redirect_uri=f"loopback:{port}")
    finally:
        server.shutdown()
    return rec
