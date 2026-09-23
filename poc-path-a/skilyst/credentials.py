"""Credential handling: restricted tokens (decision D3) + Beehive dev API client.

Token model (what the PoC proves):
  * the runtime's long-lived credential is the account's AK/SK pair (revocable,
    never the password);
  * every *skill* run gets a short-lived token carrying an explicit scope set
    (default: jobs:write, jobs:read, assets:read -- never billing/admin);
  * the scope gate refuses out-of-scope requests CLIENT-SIDE, before any HTTP
    call, so an agent that goes off the rails cannot even attempt a charge.
  * server-side RBAC is the second layer: a non-admin caller hitting an
    admin-only route gets 403.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_SCOPE = ("jobs:write", "jobs:read", "assets:read")

# (method, path-prefix) -> scope required. Anything not listed is refused.
SCOPE_RULES = [
    ("POST", "/api/v1/jobs", "jobs:write"),
    ("GET", "/api/v1/jobs", "jobs:read"),
    ("DELETE", "/api/v1/jobs", "jobs:write"),
    ("POST", "/api/v1/jobs/", "jobs:write"),
    ("GET", "/api/v1/jobs/", "jobs:read"),
    ("GET", "/api/v1/assets", "assets:read"),
    ("GET", "/api/v1/nodes", "jobs:read"),
    ("POST", "/api/v1/billing/quote", "jobs:write"),
]
DENIED_PREFIXES = ("/api/v1/admin/", "/api/v1/billing/wallet", "/api/v1/billing/nodes",
                   "/api/v1/billing/history", "/api/v1/auth/api-keys")


class ScopeRefusal(PermissionError):
    """The runtime refused to make the call -- before any credential was used."""


@dataclass
class RestrictedToken:
    access_key: str
    secret_key: str
    scope: tuple[str, ...] = DEFAULT_SCOPE
    account: str = ""
    issued_at: float = field(default_factory=time.time)

    def allows(self, method: str, path: str) -> tuple[bool, str]:
        for prefix in DENIED_PREFIXES:
            if path.startswith(prefix):
                return False, f"path {prefix} is outside every granted scope (billing/admin are never granted to a skill)"
        for m, prefix, scope in SCOPE_RULES:
            if path.startswith(prefix) and method.upper() == m:
                if scope in self.scope:
                    return True, scope
                return False, f"scope {scope} not granted"
        return False, f"no scope rule grants {method.upper()} {path}"


class BeehiveClient:
    """Thin HTTP client for the Beehive core dev API."""

    def __init__(self, base_url: str, token: RestrictedToken | None = None,
                 bearer: str | None = None, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.token = token
        self.bearer = bearer
        self.timeout = timeout

    def _headers(self, method: str, path: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token is not None:
            allowed, why = self.token.allows(method, path)
            if not allowed:
                raise ScopeRefusal(f"{method.upper()} {path} refused client-side: {why}")
            ts = str(int(time.time()))
            sig = hmac.new(self.token.secret_key.encode(),
                           (self.token.access_key + ts).encode(), hashlib.sha256).hexdigest()
            headers.update({"X-Api-Key": self.token.access_key, "X-Api-Timestamp": ts, "X-Api-Signature": sig})
        elif self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        return headers

    def request(self, method: str, path: str, body: dict | None = None, raw: bool = False):
        headers = self._headers(method, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            payload, status = exc.read(), exc.code
        text = payload.decode("utf-8", errors="replace")
        if raw:
            return status, text
        try:
            return status, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return status, {"raw": text[:300]}

    # -- convenience --------------------------------------------------------
    def submit_job(self, nodes: list[dict], workflow_id: str = "") -> dict:
        body = {"nodes": nodes}
        if workflow_id:
            body["workflow_id"] = workflow_id
        status, resp = self.request("POST", "/api/v1/jobs", body)
        if status not in (200, 201):
            raise RuntimeError(f"job submit failed: HTTP {status} {json.dumps(resp)[:300]}")
        return resp.get("payload", resp)

    def get_job(self, job_id: str) -> dict:
        status, resp = self.request("GET", f"/api/v1/jobs/{job_id}")
        if status != 200:
            raise RuntimeError(f"job poll failed: HTTP {status} {json.dumps(resp)[:200]}")
        return resp.get("payload", resp)

    def wait_for_job(self, job_id: str, timeout_s: int = 900, interval_s: int = 10, on_tick=None) -> dict:
        deadline = time.time() + timeout_s
        last = {}
        while time.time() < deadline:
            last = self.get_job(job_id)
            status = last.get("status")
            if on_tick:
                on_tick(last)
            if status in ("completed", "failed", "cancelled"):
                return last
            time.sleep(interval_s)
        raise TimeoutError(f"job {job_id} still {last.get('status')!r} after {timeout_s}s")


def artifact_url(job: dict) -> str | None:
    out = job.get("output") or {}
    for key in ("dest_video_url", "dest_image_url", "dest_hls_url"):
        if out.get(key):
            return out[key]
    for node in job.get("nodes") or []:
        node_out = node.get("output") or {}
        for key in ("dest_video_url", "dest_image_url"):
            if node_out.get(key):
                return node_out[key]
    return None


def login(base_url: str, username: str, password: str) -> str:
    status, resp = BeehiveClient(base_url).request(
        "POST", "/api/v1/auth/login", {"username": username, "password": password})
    if status != 200:
        raise RuntimeError(f"login failed: HTTP {status}")
    return resp.get("payload", {}).get("token", "")
