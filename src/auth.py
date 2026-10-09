"""Auth flow for Skilyst — device-code deep-link login (Figma-style).

State machine + PKCE + loopback listener (CLI) + mock mode.
Design: docs/auth-deep-link.md. Protocol endpoints (§五) land in beehive-core
separately; until then SKILYST_MOCK_AUTH=1 exercises the full flow locally.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
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
    # #37: the SK gets its own keychain item (execve argv cannot carry the
    # NUL byte a single "AK\0SK" item would need). Suffix keeps the legacy
    # AK item's (account, service) address untouched for upgrade-in-place.
    SECRET_SERVICE = "skilyst-agent-secret"

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
            # #37: the keychain must hold the FULL pair. NUL-separated
            # single-item storage is infeasible (execve argv cannot carry
            # NUL bytes — subprocess raises "embedded null byte"), so the
            # pair lives as two keychain items. Non-sensitive fields
            # (account/scopes/expiry) stay in the meta file so /auth/status
            # works without touching secrets.
            subprocess.run(
                ["security", "add-generic-password",
                 "-a", record.account_uid, "-s", self.SERVICE,
                 "-U", "-w", record.access_key or record.access_token or ""],
                check=True, capture_output=True)
            if record.secret_key:
                subprocess.run(
                    ["security", "add-generic-password",
                     "-a", record.account_uid, "-s", self.SECRET_SERVICE,
                     "-U", "-w", record.secret_key],
                    check=True, capture_output=True)
            else:
                # Review note (verify): an SK-less save must not leave a
                # stale SK item behind — a later load() would reassemble
                # new-AK + old-SK, a credential pair that never existed.
                # No call site saves a half pair today; this makes mixed
                # pairs impossible by construction anyway.
                subprocess.run(
                    ["security", "delete-generic-password",
                     "-a", record.account_uid, "-s", self.SECRET_SERVICE],
                    capture_output=True)
            meta = self.home / "credentials.meta"
            meta.write_text(json.dumps({
                "account_uid": record.account_uid, "account_name": record.account_name,
                "scopes": record.scopes, "expires_at": record.expires_at,
                "storage": "keychain"}))
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
            uid = m.get("account_uid", "")
            r = subprocess.run(
                ["security", "find-generic-password",
                 "-a", uid, "-s", self.SERVICE, "-w"],
                capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                ak = r.stdout.rstrip("\n")
                # SK lives in its own keychain item (#37). The legacy
                # layout kept it in PLAINTEXT in the meta file — promote
                # it into the keychain and strip it from disk, idempotent:
                # old versions transparently upgrade on first load.
                rs = subprocess.run(
                    ["security", "find-generic-password",
                     "-a", uid, "-s", self.SECRET_SERVICE, "-w"],
                    capture_output=True, text=True)
                sk = rs.stdout.rstrip("\n") if rs.returncode == 0 else ""
                legacy = m.get("secret_key", "")
                if legacy and not sk:
                    sk = legacy
                    subprocess.run(
                        ["security", "add-generic-password",
                         "-a", uid, "-s", self.SECRET_SERVICE,
                         "-U", "-w", sk],
                        check=True, capture_output=True)
                if legacy:
                    m.pop("secret_key", None)
                    meta.write_text(json.dumps(m))
                    meta.chmod(0o600)
                return TokenRecord(
                    access_token=ak, account_uid=m["account_uid"],
                    account_name=m.get("account_name", ""), scopes=m.get("scopes", []),
                    expires_at=m.get("expires_at", 0), storage="keychain",
                    access_key=ak, secret_key=sk)
        if self._dev_path.is_file():
            rec = TokenRecord.from_json(self._dev_path.read_text())
            if rec.storage == "dev-file":
                return rec
        return None

    def clear(self) -> None:
        meta = self.home / "credentials.meta"
        if meta.is_file():
            m = json.loads(meta.read_text())
            uid = m.get("account_uid", "")
            subprocess.run(
                ["security", "delete-generic-password",
                 "-a", uid, "-s", self.SERVICE],
                capture_output=True)
            # #37: the SK's own item goes with the AK's
            subprocess.run(
                ["security", "delete-generic-password",
                 "-a", uid, "-s", self.SECRET_SERVICE],
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
    """Device-code login with deep-link (GUI) or loopback (CLI) callback.

    Two hosts are involved and they are not the same one: the API endpoints
    (device-code create, token exchange) live on the beehive API host — the
    same ``BEEHIVE_API`` the rest of the runtime uses — while the user's
    browser is pointed at the web console host. Pointing the API calls at the
    console host answers 405 (verified live), so the two are resolved
    separately.
    """

    def __init__(self, store: Optional[TokenStore] = None,
                 base_url: str = "https://bee.verse4.pet",
                 api_url: Optional[str] = None,
                 mock: Optional[bool] = None):
        self.store = store or TokenStore()
        self.base_url = base_url.rstrip("/")            # web console (browser)
        self.api_url = (api_url or os.environ.get("BEEHIVE_API")
                        or "https://beehive-api.verse4.pet").rstrip("/")
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

    def refresh(self) -> TokenRecord:
        """Rotate the stored AK/SK pair server-side (core#681: POST
        /api/v1/auth/refresh, authenticated with the CURRENT pair, 72h
        overlap window on the old key). On success the new pair replaces
        the stored one atomically: save() overwrites the keychain entry and
        the meta file, and the old key is dead server-side after the overlap
        lapses — nothing to clean locally beyond the entry we just
        overwrote.

        Raises AuthError when no valid credential is stored or the server
        refuses (expired past the overlap, revoked). Loud by contract (#34):
        the desktop re-authorize UX depends on a refusal reaching the user.
        """
        rec = self.current()
        if rec is None:
            raise AuthError("no stored credential to refresh -- sign in first")
        if self.mock:
            # Mock mode: extend the existing record's life; no server to ask.
            rec.expires_at = time.time() + 86400
            self.store.save(rec)
            return rec
        ts = str(int(time.time()))
        sig = hmac.new(rec.secret_key.encode(),
                       (rec.access_key + ts).encode(), hashlib.sha256).hexdigest()
        import urllib.request
        req = urllib.request.Request(
            f"{self.api_url}/api/v1/auth/refresh", data=b"{}", method="POST",
            headers={"Content-Type": "application/json",
                     "X-Api-Key": rec.access_key,
                     "X-Api-Timestamp": ts,
                     "X-Api-Signature": sig})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            # 401 = the pair is past the overlap or revoked: the honest answer
            # is "re-authorize", surfaced as AuthError for the gate to show.
            raise AuthError(f"refresh refused (HTTP {exc.code}): "
                            f"re-authorization required") from exc
        payload = resp.get("payload", resp) if isinstance(resp, dict) else {}
        new = TokenRecord(
            access_token=payload.get("access_key", ""),
            account_uid=rec.account_uid,
            account_name=rec.account_name,
            scopes=payload.get("scopes") or rec.scopes,
            expires_at=time.time() + payload.get("expires_in", 86400),
            storage="keychain" if rec.storage == "keychain" else "dev-file",
            access_key=payload.get("access_key", ""),
            secret_key=payload.get("secret_key", ""))
        if not new.access_key or not new.secret_key:
            raise AuthError("refresh response missing the credential pair")
        self.store.save(new)
        self.state = AuthState.AUTHENTICATED
        return new

    # -- login --------------------------------------------------------------

    def begin_login(self, redirect_uri: str) -> str:
        """Start a login without waiting for the exchange, and return the
        browser URL the caller (the desktop shell) must open.

        `login()` bundles three concerns for the CLI: create the device
        session, open the browser, and block until the code comes back. The
        desktop shell needs them split — it opens the browser itself and the
        code arrives later through the deep-link handler → ``deliver_code``.
        The waiting half of ``login`` still applies: the runtime holds the
        PKCE verifier in this AuthFlow instance, so the /auth/deliver-code
        route must reach *this* flow (RuntimeAPI keeps it alive), never a
        fresh one."""
        verifier, challenge = make_pkce_pair()
        self._verifier = verifier

        if self.mock:
            self._device_code = "dc_mock_" + secrets.token_hex(8)
            launch = f"{self.base_url}/login?device_code={self._device_code}&mock=1"
        else:
            resp = _post_json(f"{self.api_url}/api/v1/auth/device-code", {
                "client": "skilyst-agent", "code_challenge": challenge,
                "redirect_uri": redirect_uri})
            self._device_code = resp["device_code"]
            launch = f"{self.base_url}/login?device_code={self._device_code}&client=skilyst-agent"
        self.state = AuthState.AWAITING_BROWSER
        # Arm the exchange wait so deliver_code (which may race the caller's
        # reaction to the returned URL) always finds a waiter to hand the
        # result to; a code that never arrives just times out on the poll.
        self._arm_exchange()
        return launch

    def _arm_exchange(self, poll_timeout: float = 300.0) -> None:
        self._exchange_event = threading.Event()
        self._exchange_error = None
        self._exchange_result = None
        self._poll_timeout = poll_timeout

    def login(self, redirect_uri: str, poll_timeout: float = 300.0) -> TokenRecord:
        """Start login. For GUI use redirect_uri='skilyst://callback' (deep link
        delivers the code separately); for CLI use 'loopback:<port>'."""
        verifier, challenge = make_pkce_pair()
        self._verifier = verifier

        if self.mock:
            self._device_code = "dc_mock_" + secrets.token_hex(8)
            launch = f"{self.base_url}/login?device_code={self._device_code}&mock=1"
        else:
            resp = _post_json(f"{self.api_url}/api/v1/auth/device-code", {
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
        if getattr(self, "_exchange_event", None) is None or self._exchange_event.is_set():
            # Not armed by begin_login (CLI path) or a previous exchange already
            # finished: arm fresh here so deliver_code has a waiter.
            self._arm_exchange(poll_timeout)
        else:
            self._poll_timeout = poll_timeout
        if not self._exchange_event.wait(timeout=self._poll_timeout):
            self.state = AuthState.UNAUTHENTICATED
            raise AuthError("login timed out waiting for browser callback")
        if self._exchange_error:
            self.state = AuthState.UNAUTHENTICATED
            raise AuthError(self._exchange_error)
        rec = self._exchange_result
        self.state = AuthState.AUTHENTICATED
        return rec

    def deliver_code(self, code: str) -> None:
        """Called by deep-link handler (GUI) or loopback listener (CLI) with
        the one-time code from the browser redirect.

        Raises AuthError when no login is pending — loudly (#24): this used
        to raise inside the try below, where ``except Exception`` swallowed
        it into ``_exchange_error``; the serve layer then never saw the
        error and answered with the misleading fallback. The no-pending-login
        refusal must propagate to the caller (which maps AuthError to a 400
        the shell can show)."""
        # Race guard: the login path sets state=AWAITING_BROWSER *before* it
        # arms the exchange wait (which creates _exchange_event). A caller
        # that reacts to the state can arrive first -- but ONLY in that
        # state is an arm imminent. Any other state means no login is
        # starting, so refuse immediately instead of spinning the 5s guard
        # for a waiter that will never exist (#24: the orphan probe used to
        # burn the full 5.01s before failing).
        if self.state != AuthState.AWAITING_BROWSER:
            self.state = AuthState.UNAUTHENTICATED
            raise AuthError(
                "no pending login to deliver a code to -- start one first "
                "(POST /auth/login), then deliver the code from the deep link")
        deadline = time.time() + 5
        while not hasattr(self, "_exchange_event") and time.time() < deadline:
            time.sleep(0.01)
        if not hasattr(self, "_exchange_event"):
            # AWAITING_BROWSER but the arm never landed (the login thread
            # died between the two steps). Refuse loudly -- BEFORE the try
            # block, so the AuthError escapes to the caller (#24) instead of
            # being captured into _exchange_error where nobody re-raises it.
            self.state = AuthState.UNAUTHENTICATED
            raise AuthError(
                "no pending login to deliver a code to -- start one first "
                "(POST /auth/login), then deliver the code from the deep link")
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
                resp = _post_json(f"{self.api_url}/api/v1/auth/token", {
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
            # Persist BEFORE setting the event: the desktop flow does not run
            # _await_exchange (nobody blocks), so the /auth/deliver-code route
            # reads the result from the store via current(). Saving first means
            # the event signals "record already on disk", never "in flight".
            self.store.save(self._exchange_result)
            self.state = AuthState.AUTHENTICATED
        except Exception as e:  # noqa: BLE001 — surface any failure to the waiter
            self._exchange_error = f"token exchange failed: {e}"
            self.state = AuthState.UNAUTHENTICATED
        # The waiter may legitimately not exist (no pending login): only a
        # flow that armed an exchange wait has an event to release.
        event = getattr(self, "_exchange_event", None)
        if event is not None:
            event.set()

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
        resp = json.loads(r.read())
    # The beehive API wraps successful bodies as {code, message, payload};
    # unwrap so the callers read the endpoint's own fields.
    if isinstance(resp, dict) and "payload" in resp and "code" in resp:
        return resp["payload"]
    return resp


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
