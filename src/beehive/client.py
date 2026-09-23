"""Beehive core API client: job submit / poll / assets, artifact hand-off, auth.

Everything the runtime does against the platform goes through here, and every
request carries the scope gate (`beehive.scope`) -- there is no bypass path.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .scope import RestrictedToken, ScopeRefusal, skill_token

DEFAULT_BASE = "https://beehive-api.verse4.pet"
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
ARTIFACT_KEYS = ("dest_video_url", "dest_image_url", "dest_hls_url", "dest_audio_url")


class BeehiveError(RuntimeError):
    pass


@dataclass
class JobHandle:
    job_id: str
    status: str = ""
    progress: float | None = None
    error: str | None = None
    artifact_url: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and bool(self.artifact_url)


def job_handle(job: dict) -> JobHandle:
    return JobHandle(job_id=str(job.get("id") or job.get("job_id") or ""),
                     status=str(job.get("status") or ""),
                     progress=job.get("progress"),
                     error=job.get("error_msg") or job.get("error"),
                     artifact_url=artifact_url(job),
                     raw=job)


def _urllib_transport(method: str, url: str, headers: dict, data: bytes | None,
                      timeout: int) -> tuple[int, str]:
    """Default transport: returns (status, body) and never raises for HTTP errors."""
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise BeehiveError(f"{method.upper()} {url} could not be reached: {exc.reason}") from exc


class BeehiveClient:
    """Thin HTTP client for the Beehive core dev API."""

    def __init__(self, base_url: str = DEFAULT_BASE, token: RestrictedToken | None = None,
                 bearer: str | None = None, timeout: int = 60, scope: RestrictedToken | None = None,
                 bypass_scope: bool = False, transport=None):
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self.token = token
        self.bearer = bearer
        self.timeout = timeout
        # `scope` lets a bearer-authenticated client (password/JWT login path) stay
        # subject to the same client-side gate as an AK/SK token: there is no
        # configuration in which a skill-facing client skips the gate.
        self.scope = scope
        # `bypass_scope` exists for one purpose only: measuring what the *platform*
        # enforces with a restricted key (`skilyst authz-probe --bypass-gate`).
        # No tool or skill path can construct a client with this set.
        self.bypass_scope = bypass_scope
        self._transport = transport or _urllib_transport
        self.requests: list[dict] = []

    # -- transport ----------------------------------------------------------
    def _headers(self, method: str, path: str) -> dict:
        headers = {"Content-Type": "application/json"}
        gate_token = self.token or self.scope
        if gate_token is not None and not self.bypass_scope:
            allowed, why = gate_token.allows(method, path)
            if not allowed:
                raise ScopeRefusal(f"{method.upper()} {path} refused client-side: {why}")
        if self.token is not None:
            headers.update(self.token.sign())
        elif self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        return headers

    def request(self, method: str, path: str, body: dict | None = None, raw: bool = False):
        headers = self._headers(method, path)
        data = json.dumps(body).encode() if body is not None else None
        self.requests.append({"method": method.upper(), "path": path, "headers": dict(headers)})
        status, text = self._transport(method, self.base + path, headers, data, self.timeout)
        if raw:
            return status, text
        try:
            return status, json.loads(text) if text else {}
        except json.JSONDecodeError:
            return status, {"raw": text[:300]}

    def _payload(self, method: str, path: str, body: dict | None = None, ok=(200, 201)) -> dict:
        status, resp = self.request(method, path, body)
        if status not in ok:
            raise BeehiveError(f"{method.upper()} {path} failed: HTTP {status} "
                               f"{json.dumps(resp, ensure_ascii=False)[:300]}")
        return resp.get("payload", resp) if isinstance(resp, dict) else resp

    # -- jobs ---------------------------------------------------------------
    def submit_job(self, nodes: list[dict], workflow_id: str = "") -> dict:
        body: dict = {"nodes": nodes}
        if workflow_id:
            body["workflow_id"] = workflow_id
        return self._payload("POST", "/api/v1/jobs", body)

    def get_job(self, job_id: str) -> dict:
        return self._payload("GET", f"/api/v1/jobs/{job_id}")

    def list_jobs(self, limit: int = 20) -> list[dict]:
        payload = self._payload("GET", f"/api/v1/jobs?limit={int(limit)}")
        return payload.get("jobs", payload) if isinstance(payload, dict) else payload

    def wait_for_job(self, job_id: str, timeout_s: int = 900, interval_s: int = 10, on_tick=None) -> dict:
        deadline = time.time() + timeout_s
        last: dict = {}
        while time.time() < deadline:
            last = self.get_job(job_id)
            if on_tick:
                on_tick(last)
            if str(last.get("status")) in TERMINAL_STATUSES:
                return last
            time.sleep(interval_s)
        raise TimeoutError(f"job {job_id} still {last.get('status')!r} after {timeout_s}s")

    # -- registry / assets --------------------------------------------------
    def list_nodes(self) -> list[dict]:
        payload = self._payload("GET", "/api/v1/nodes")
        return payload.get("nodes", payload) if isinstance(payload, dict) else payload

    def list_assets(self, limit: int = 20) -> list[dict]:
        payload = self._payload("GET", f"/api/v1/assets?limit={int(limit)}")
        return payload.get("assets", payload) if isinstance(payload, dict) else payload

    # -- auth ---------------------------------------------------------------
    def login(self, username: str, password: str) -> str:
        status, resp = BeehiveClient(self.base).request(
            "POST", "/api/v1/auth/login", {"username": username, "password": password})
        if status != 200:
            raise BeehiveError(f"login failed: HTTP {status}")
        return (resp.get("payload", {}) or {}).get("token", "")


def artifact_url(job: dict) -> str | None:
    """The finished artifact URL of a job, wherever the core put it."""
    out = job.get("output") or {}
    for key in ARTIFACT_KEYS:
        if out.get(key):
            return out[key]
    for node in job.get("nodes") or []:
        node_out = node.get("output") or {}
        for key in ARTIFACT_KEYS:
            if node_out.get(key):
                return node_out[key]
    return None


@dataclass
class ArtifactCheck:
    url: str
    reachable: bool
    status: int | None
    content_type: str | None
    content_length: int | None
    detail: str


def verify_artifact(url: str, timeout: int = 30) -> ArtifactCheck:
    """Confirm the artifact really exists before reporting success (HEAD request).

    A job reporting `completed` is not evidence that the file is downloadable;
    the hand-off contract is a playable URL.
    """
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("content-length")
            return ArtifactCheck(url, True, resp.status, resp.headers.get("content-type"),
                                 int(length) if length and length.isdigit() else None,
                                 f"HTTP {resp.status}, {length or '?'} bytes")
    except urllib.error.HTTPError as exc:
        return ArtifactCheck(url, False, exc.code, None, None, f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        return ArtifactCheck(url, False, None, None, None, f"unreachable: {exc.reason}")


def client_for(access_key: str, secret_key: str, account: str = "", base_url: str = DEFAULT_BASE,
               scope=None) -> BeehiveClient:
    """Build the scope-gated client a skill run is allowed to use."""
    token = skill_token(access_key, secret_key, account, scope) if scope else \
        skill_token(access_key, secret_key, account)
    return BeehiveClient(base_url, token=token)


def client_from_bearer(base_url: str, bearer: str, account: str = "") -> BeehiveClient:
    """JWT-authenticated client that is *still* subject to the scope gate."""
    return BeehiveClient(base_url, bearer=bearer,
                         scope=RestrictedToken(access_key="", secret_key="", account=account))
