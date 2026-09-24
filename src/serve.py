"""`skilyst serve` -- the loopback control plane the desktop shell drives.

Why a sidecar HTTP server instead of embedding the runtime in the shell: the
runtime is Python and the shell is Rust/JS, so a child process keeps the two
lifecycles independent (the window reloads without restarting a run; a runtime
crash does not take the UI with it) and avoids committing phase 1 to a PyO3 or
PyInstaller ABI. The shell spawns `skilyst serve`, reads exactly one ready line
from stdout (port + token), then speaks the contract below -- the same code paths
the CLI uses, because both go through `agent.runner.open_run`.

Security posture:

  * bind 127.0.0.1 only: there is no remote surface at all;
  * every route requires the bearer token, including ``/health`` -- there is no
    unauthenticated endpoint to probe. The token is a capability that can spend
    money, so it is generated per process unless the operator pins one;
  * CORS preflight is answered only for the known webview/dev origins;
  * paid work is off by default: the server runs dry-run unless started with
    ``--live``, so a stray click in the GUI cannot submit a job.

Routes (JSON in, JSON out, all authenticated):

    GET    /health                liveness + what this runtime is configured to do
    GET    /config                resolved configuration, secrets redacted
    GET    /doctor[?skill=<id>]   integrity + live node pre-flight
    GET    /sessions              session list (same rows as `skilyst sessions`)
    POST   /sessions              create an empty session
    GET    /session/<id>          metadata + messages + artifacts + trace
    DELETE /session/<id>          remove a session directory
    POST   /message               one user turn -> agent loop -> run summary
    POST   /shutdown              graceful stop (the shell's stop button)

``POST /message`` with ``{"stream": true}`` answers ``text/event-stream`` with
``delta`` (model text), ``note`` (progress lines), ``done`` (the same summary the
non-streaming call returns) and ``error`` events.

Status codes: transport and refusal failures carry an HTTP status (401 no token,
400 malformed request, 403 sandbox/scope refusal, 409 missing credential or failed
pre-flight, 404 unknown route/session, 502 platform/model transport error). A run
that *executed* but did not complete answers 200 with ``ok: false`` plus
``stop_reason`` and ``error`` -- the same shape the CLI prints, because the caller
still needs the session id to keep the conversation.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import sys
import threading
import time
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from agent.runner import gated_client, load_skill, open_run, preflight_payload, skill_store
from beehive import BeehiveError, ScopeRefusal
from config import ConfigError, RuntimeConfig, resolve
from errors import SkillValidationError
from llm import LLMError
from sandbox import SandboxViolation
from session import META, SessionStore
from skills.store import preflight_nodes

API_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOKEN_HEADER = "X-Skilyst-Token"
PARENT_POLL_S = 2.0

# The shell's webview origins. Tauri serves the window from `tauri://localhost`
# on macOS and `http://tauri.localhost` on Windows/Linux; 1420 is the vite dev
# server used by `npm run dev` in the browser.
WEBVIEW_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                   "http://localhost:1420", "http://127.0.0.1:1420")


# ---------------------------------------------------------------------------
# errors: one mapping from runtime exception to HTTP status, used everywhere
# ---------------------------------------------------------------------------
class APIError(Exception):
    status = 500
    kind = "Internal"

    def __init__(self, message: str, detail: object | None = None):
        super().__init__(message)
        self.detail = detail


class BadRequest(APIError):
    status, kind = 400, "BadRequest"


class NotFound(APIError):
    status, kind = 404, "NotFound"


class Unauthorised(APIError):
    status, kind = 401, "Unauthorised"


class Refused(APIError):
    status, kind = 403, "Refused"


class NotRunnable(APIError):
    status, kind = 409, "PreflightRefused"


def error_status(exc: BaseException) -> tuple[int, str]:
    if isinstance(exc, APIError):
        return exc.status, exc.kind
    if isinstance(exc, ConfigError):
        return 409, "ConfigError"
    if isinstance(exc, FileNotFoundError):
        return 404, "FileNotFoundError"
    if isinstance(exc, SkillValidationError):
        return 422, "SkillValidationError"
    if isinstance(exc, (ScopeRefusal, SandboxViolation)):
        return 403, type(exc).__name__
    if isinstance(exc, (BeehiveError, LLMError)):
        return 502, type(exc).__name__
    if isinstance(exc, TimeoutError):
        return 504, "TimeoutError"
    return 500, type(exc).__name__


def error_payload(exc: BaseException) -> dict:
    status, kind = error_status(exc)
    payload = {"ok": False, "error": {"kind": kind, "message": str(exc), "status": status}}
    detail = getattr(exc, "detail", None)
    if detail is not None:
        payload["error"]["detail"] = detail
    return payload


# ---------------------------------------------------------------------------
# the API: everything the shell needs, with no HTTP in it (so it is testable)
# ---------------------------------------------------------------------------
@dataclass
class ServeOptions:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str = ""
    token_file: str | None = None
    skill: str | None = None
    dry_run: bool = True
    max_turns: int = 8
    # None = resolve from configuration (SKILYST_MAX_JOBS, else the interactive default):
    # the shell starts the server without a flag, so the default has to come from config.
    max_jobs: int | None = None
    allow_fallback: bool = False
    orphan_guard: bool = False


class RuntimeAPI:
    """The local runtime, exposed as plain methods.

    ``resolve_kwargs`` is the CLI's global configuration (env file, store,
    workspace, sessions, llm config, model). Routes resolve it at three
    strictness levels: ``/config`` and ``/sessions`` need nothing, ``/doctor``
    needs the platform only when a skill is named, and ``/message`` needs the
    model always and the platform when a skill is active. A missing credential
    therefore fails on the route that actually needs it, loudly, with the file
    and variable it looked at.
    """

    def __init__(self, cfg: RuntimeConfig, *, resolve_kwargs: dict | None = None,
                 skill: str | None = None, dry_run: bool = True, max_turns: int = 8,
                 max_jobs: int | None = None, allow_fallback: bool = False,
                 client_factory: Callable | None = None, opener: Callable = open_run,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.cfg = cfg
        self.resolve_kwargs = dict(resolve_kwargs or {})
        self.skill = skill
        self.dry_run = dry_run
        self.max_turns = max_turns
        # A GUI client cannot pass --max-jobs, so the server's budget comes from the
        # operator's configuration (SKILYST_MAX_JOBS) or the interactive default.
        self.max_jobs = max_jobs if max_jobs is not None else cfg.job_budget(interactive=True)
        self.allow_fallback = allow_fallback
        self.client_factory = client_factory
        self.opener = opener
        self.host = host
        self.port = port
        self.started_at = time.time()

    # -- configuration ------------------------------------------------------
    def _resolve(self, *, llm: bool, beehive: bool) -> RuntimeConfig:
        return resolve(**self.resolve_kwargs, require_llm=llm, require_beehive=beehive)

    def health(self) -> dict:
        return {"ok": True, "api_version": API_VERSION, "pid": os.getpid(),
                "started_at": self.started_at, "uptime_s": round(time.time() - self.started_at, 1),
                "host": self.host, "port": self.port, "dry_run": self.dry_run,
                "skill": self.skill, "max_turns": self.max_turns, "max_jobs": self.max_jobs,
                "paths": {"store": str(self.cfg.store_dir), "workspace": str(self.cfg.workspace_dir),
                          "sessions": str(self.cfg.session_dir)},
                "env_file": str(self.cfg.env_file) if self.cfg.env_file else None}

    def config(self) -> dict:
        return self._resolve(llm=False, beehive=False).redacted()

    def doctor(self, skill_id: str | None = None) -> dict:
        cfg = self._resolve(llm=False, beehive=bool(skill_id))
        store = skill_store(cfg)
        payload: dict = {"store": str(cfg.store_dir),
                         "env_file": str(cfg.env_file) if cfg.env_file else None,
                         "integrity": store.verify_all(), "config": cfg.redacted()}
        if skill_id:
            package = load_skill(store, skill_id)
            client = gated_client(cfg)
            report = preflight_nodes(package, client, allow_fallback=self.allow_fallback)
            payload["skill"] = {"skill_id": package.skill_id, "version": package.version,
                                "digest": package.digest, "permission": package.permission,
                                "plan": package.plan, "warnings": list(package.warnings)}
            payload["preflight"] = preflight_payload(report)
            payload["runnable"] = report.runnable
        else:
            payload["skills"] = [{"skill_id": p.skill_id, "version": p.version, "degraded": p.degraded,
                                  "description": p.description}
                                 for p in store.list_partial()[0]]
            payload["runnable"] = all(row["ok"] for row in payload["integrity"])
        return payload

    # -- sessions -----------------------------------------------------------
    def _sessions(self) -> SessionStore:
        return SessionStore(self.cfg.session_dir)

    def _session_path(self, session_id: str) -> Path:
        # Percent-decoded first: a client that encodes the separator must not slip a
        # path through the "no slash" check.
        session_id = unquote(session_id)
        if not session_id or session_id.startswith(".") or "/" in session_id or "\\" in session_id:
            raise BadRequest(f"invalid session id {session_id!r}")
        path = (self.cfg.session_dir / session_id).resolve()
        if path.parent != Path(self.cfg.session_dir).resolve():
            raise BadRequest(f"invalid session id {session_id!r}")
        if not (path / META).is_file():
            raise NotFound(f"session {session_id!r} not found under {self.cfg.session_dir}")
        return path

    def sessions(self) -> dict:
        return {"sessions": self._sessions().list(), "root": str(self.cfg.session_dir)}

    def session(self, session_id: str) -> dict:
        path = self._session_path(session_id)
        session = self._sessions().open(session_id)
        return {"session_id": session_id, "meta": session.meta, "messages": session.messages,
                "artifacts": session.artifacts, "trace": session.trace, "path": str(path)}

    def create_session(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        session = self._sessions().create(title=str(payload.get("title") or "untitled"),
                                          model=str(payload.get("model") or ""),
                                          workspace=str(self.cfg.workspace_dir))
        return session.summary()

    def delete_session(self, session_id: str) -> dict:
        path = self._session_path(session_id)
        shutil.rmtree(path)
        return {"deleted": session_id}

    # -- one user turn ------------------------------------------------------
    def _open(self, cfg: RuntimeConfig, **kwargs):
        return self.opener(cfg, client_factory=self.client_factory, **kwargs)

    def message(self, payload: dict, emit: Callable[[str, dict], None] | None = None) -> dict:
        if not isinstance(payload, dict):
            raise BadRequest("body must be a JSON object")
        text = str(payload.get("message") or "").strip()
        if not text:
            raise BadRequest("message is required")
        skill_id = payload.get("skill") or self.skill
        session_id = payload.get("session_id") or None
        requested_dry_run = payload.get("dry_run")
        # A server started in dry-run mode cannot be talked out of it per request: the
        # operator's `--live` decision is the gate, and the message flag can only be
        # *more* cautious than the server.
        dry_run = self.dry_run or (self.dry_run if requested_dry_run is None
                                   else bool(requested_dry_run))
        max_turns = int(payload.get("max_turns") or self.max_turns)
        model = str(payload.get("model") or "").strip()
        # `stream` decides how the *model* is called too: a caller that did not ask for
        # SSE gets a plain completion, so a provider without streaming support is not a
        # hard failure for the non-streaming path.
        stream = bool(payload.get("stream"))
        cfg = self._resolve(llm=True, beehive=bool(skill_id))
        if model:
            # A per-message override, never a stored default: an experiment must not
            # silently become what every later session runs on.
            cfg = replace(cfg, llm=replace(cfg.llm, model=model))

        notes: list[str] = []

        def on_event(message: str) -> None:
            notes.append(message)
            if emit:
                emit("note", {"text": message})

        def on_delta(chunk: str) -> None:
            if emit:
                emit("delta", {"text": chunk})

        ctx = self._open(cfg, skill_id=skill_id, session_id=session_id, title=text[:60],
                         dry_run=dry_run, max_turns=max_turns,
                         allow_fallback=self.allow_fallback, max_jobs=self.max_jobs,
                         stream=stream, on_event=on_event, on_delta=on_delta)
        if ctx.preflight is not None and not ctx.preflight.runnable:
            raise NotRunnable("node pre-flight failed -- the skill's required nodes are not runnable "
                              "here", preflight_payload(ctx.preflight))
        result = ctx.loop.run(text)
        summary = ctx.result_payload(result)
        summary.update({"ok": result.ok, "dry_run": dry_run, "notes": notes})
        return summary


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
class RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(api: RuntimeAPI, token: str, allowed_origins: tuple[str, ...],
                 note: Callable[[str], None]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "skilyst-serve/1.0"
        protocol_version = "HTTP/1.1"

        # -- plumbing -------------------------------------------------------
        def log_message(self, fmt: str, *args) -> None:
            note(f"http {self.address_string()} {fmt % args}")

        def _cors_headers(self) -> dict:
            origin = self.headers.get("Origin")
            headers = {"Access-Control-Allow-Headers": f"authorization, content-type, {TOKEN_HEADER}",
                       "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS"}
            if origin and origin in allowed_origins:
                headers["Access-Control-Allow-Origin"] = origin
                headers["Vary"] = "Origin"
            return headers

        def _write_headers(self, status: int, content_type: str, length: int | None,
                           extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            for key, value in self._cors_headers().items():
                self.send_header(key, value)
            self.end_headers()

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._write_headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _ok(self, data) -> None:
            self._json(200, {"ok": True, "data": data})

        def _fail(self, exc: BaseException) -> None:
            status, _kind = error_status(exc)
            self._json(status, error_payload(exc))

        def _authorised(self) -> bool:
            header = self.headers.get("Authorization") or ""
            supplied = (header[7:].strip() if header.lower().startswith("bearer ")
                        else (self.headers.get(TOKEN_HEADER) or ""))
            return bool(supplied) and secrets.compare_digest(supplied, token)

        def _read_body(self) -> bytes:
            """Read the whole body up front, even on a route that ignores it.

            An unread body stays in the socket and is parsed as the *next* request
            line, which turns a keep-alive connection into a 400 (observed with
            `curl -d '{}' .../shutdown`).
            """
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        @staticmethod
        def _parse_json(raw: bytes) -> dict:
            if not raw:
                return {}
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BadRequest(f"body is not valid JSON: {exc}") from exc
            if not isinstance(body, dict):
                raise BadRequest("body must be a JSON object")
            return body

        def _split(self) -> tuple[str, dict]:
            path, _, query = self.path.partition("?")
            params = {}
            for pair in query.split("&"):
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    params[key] = value
            return path.rstrip("/") or "/", params

        # -- SSE ------------------------------------------------------------
        def _sse_start(self) -> None:
            # Chunked, not close-delimited: the shell keeps one connection per run and a
            # missing Transfer-Encoding header makes every client wait for a close that
            # never comes (the framing below would then be read as body text).
            self._write_headers(200, "text/event-stream", None,
                                {"Connection": "keep-alive", "Transfer-Encoding": "chunked"})

        def _sse_send(self, event: str, data: dict) -> None:
            chunk = (f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                     ).encode("utf-8")
            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
            self.wfile.flush()

        def _sse_end(self) -> None:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        # -- verbs ----------------------------------------------------------
        def do_OPTIONS(self) -> None:                       # noqa: N802 -- http.server API
            self._write_headers(204, "text/plain; charset=utf-8", 0)

        def do_GET(self) -> None:                           # noqa: N802
            path, params = self._split()
            if not self._authorised():
                self._fail(Unauthorised("missing or invalid runtime token"))
                return
            try:
                if path == "/health":
                    self._ok(api.health())
                elif path == "/config":
                    self._ok(api.config())
                elif path == "/doctor":
                    self._ok(api.doctor(params.get("skill")))
                elif path == "/sessions":
                    self._ok(api.sessions())
                elif path.startswith("/session/"):
                    self._ok(api.session(path.split("/", 2)[2]))
                else:
                    raise NotFound(f"no route for GET {path}")
            except Exception as exc:                        # noqa: BLE001 -- mapped, never hidden
                self._fail(exc)

        def do_POST(self) -> None:                          # noqa: N802
            path, _params = self._split()
            raw = self._read_body()
            if not self._authorised():
                self._fail(Unauthorised("missing or invalid runtime token"))
                return
            try:
                if path == "/message":
                    body = self._parse_json(raw)
                    if body.get("stream"):
                        self._sse_start()
                        try:
                            result = api.message(body, emit=self._sse_send)
                        except Exception as exc:            # noqa: BLE001
                            self._sse_send("error", error_payload(exc))
                            self._sse_end()
                            return
                        self._sse_send("done", {"ok": True, "data": result})
                        self._sse_end()
                        return
                    self._ok(api.message(body))
                elif path == "/sessions":
                    self._ok(api.create_session(self._parse_json(raw)))
                elif path == "/shutdown":
                    self._ok({"stopping": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    raise NotFound(f"no route for POST {path}")
            except Exception as exc:                        # noqa: BLE001
                self._fail(exc)

        def do_DELETE(self) -> None:                        # noqa: N802
            path, _params = self._split()
            if not self._authorised():
                self._fail(Unauthorised("missing or invalid runtime token"))
                return
            try:
                if path.startswith("/session/"):
                    self._ok(api.delete_session(path.split("/", 2)[2]))
                else:
                    raise NotFound(f"no route for DELETE {path}")
            except Exception as exc:                        # noqa: BLE001
                self._fail(exc)

    return Handler


def parent_gone(recorded_pid: int) -> bool:
    """True when the process that started us is no longer our parent (POSIX)."""
    return os.name == "posix" and os.getppid() != recorded_pid


def ready_line(port: int, token: str, options: ServeOptions, cfg: RuntimeConfig) -> dict:
    """The single stdout line the shell parses to learn where the runtime is."""
    return {"event": "ready", "api_version": API_VERSION, "host": options.host, "port": port,
            "token": token, "pid": os.getpid(), "dry_run": options.dry_run, "skill": options.skill,
            "sessions_dir": str(cfg.session_dir), "store_dir": str(cfg.store_dir),
            "workspace_dir": str(cfg.workspace_dir), "started_at": time.time(),
            "orphan_guard": options.orphan_guard}


def serve(cfg: RuntimeConfig, options: ServeOptions, *, resolve_kwargs: dict | None = None,
          on_ready: Callable[[dict], None] | None = None, note: Callable[[str], None] | None = None,
          client_factory: Callable | None = None, allowed_origins: tuple[str, ...] = WEBVIEW_ORIGINS,
          server_factory: Callable = RuntimeHTTPServer, stdin=None) -> int:
    """Start the server and block until it is asked to stop. Returns 0 on a clean stop."""
    sink = note or (lambda _message: None)

    def log(message: str) -> None:
        """Logging must never be able to break behaviour.

        The moment the shell dies, its end of our stderr pipe closes, so every later
        print raises BrokenPipeError -- including the ones inside the shutdown path.
        That is how an orphan guard silently failed to stop an orphaned runtime: the
        guard logged "the shell is gone" and died on the broken pipe before it could
        call shutdown.
        """
        try:
            sink(message)
        except (OSError, ValueError):
            pass

    token = options.token or secrets.token_urlsafe(32)
    api = RuntimeAPI(cfg, resolve_kwargs=resolve_kwargs, skill=options.skill, dry_run=options.dry_run,
                     max_turns=options.max_turns, max_jobs=options.max_jobs,
                     allow_fallback=options.allow_fallback, client_factory=client_factory,
                     host=options.host, port=options.port)
    httpd = server_factory((options.host, options.port),
                           make_handler(api, token, allowed_origins, log))
    api.port = httpd.server_address[1]
    if options.token_file:
        path = Path(options.token_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)

    def stop(_signum=None, _frame=None) -> None:
        # Shutdown first, announce second: the announcement can fail (see `log`), and a
        # failure to print must never be a failure to stop.
        threading.Thread(target=httpd.shutdown, daemon=True).start()
        log(f"stopping (signal {_signum})")

    if options.orphan_guard:
        # The shell that started us owns our life: if it dies without running its
        # cleanup (SIGKILL, a crash), nothing will ever talk to this server again and
        # an orphaned runtime would sit on a port holding a credential.
        #
        # Two signals, because neither is sufficient alone:
        #   * stdin EOF -- the parent holds the write end of our stdin. This is the
        #     portable one (it is what covers Windows) and it fires instantly.
        #   * parent pid change -- in the real shell something in the process tree
        #     keeps the pipe's write end open (verified: the guard armed, the shell
        #     was SIGTERMed, no EOF arrived), so a poll of os.getppid() is the
        #     backstop on POSIX. Reparenting to pid 1 is what a dead parent looks
        #     like. Windows keeps the old value, where the stdin signal still works.
        def watch_stdin() -> None:
            stream = stdin if stdin is not None else sys.stdin
            log("orphan guard: watching stdin for EOF")
            try:
                while stream.read(1):
                    pass
            except (OSError, ValueError) as exc:
                log(f"orphan guard: stdin read ended: {type(exc).__name__}: {exc}")
            log("stdin closed -- the shell is gone, stopping")
            stop("stdin-eof")

        def watch_parent(parent_pid: int) -> None:
            while True:
                time.sleep(PARENT_POLL_S)
                if parent_gone(parent_pid):
                    log(f"parent {parent_pid} is gone (reparented to {os.getppid()}) -- stopping")
                    stop("orphaned")
                    return

        threading.Thread(target=watch_stdin, daemon=True).start()
        if hasattr(os, "getppid") and os.name == "posix":
            threading.Thread(target=watch_parent, args=(os.getppid(),), daemon=True).start()

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, stop)
        except ValueError:                                  # not the main thread (tests)
            pass
    if on_ready:
        on_ready(ready_line(api.port, token, options, cfg))
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        stop()
    finally:
        httpd.server_close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


__all__ = ["API_VERSION", "DEFAULT_HOST", "DEFAULT_PORT", "RuntimeAPI", "RuntimeHTTPServer",
           "ServeOptions", "WEBVIEW_ORIGINS", "error_payload", "error_status", "make_handler",
           "ready_line", "serve"]
