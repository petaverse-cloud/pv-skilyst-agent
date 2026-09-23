"""Serve-layer tests: the loopback control plane the desktop shell drives.

Everything here is real HTTP over 127.0.0.1 against a real `RuntimeAPI` on a real
session store -- only the model and the platform are stubbed (the chat client's
transport is injected, exactly as in the other suites). That matters for this
layer: the shell's contract is the wire format, so a test that called the API
methods directly would prove nothing about what the shell receives.

Covered: token enforcement on every route, redaction, session listing/creation/
deletion, one message turn end-to-end (including what lands in messages.jsonl),
the SSE stream, the credential failure path, path-traversal refusal on session
ids, CORS for webview origins only, dry-run by default, and the ready line plus
graceful shutdown that the Rust supervisor depends on.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import resolve                                                                  # noqa: E402
from llm import ChatClient, LLMError, LLMConfig                                             # noqa: E402
from serve import (API_VERSION, RuntimeAPI, RuntimeHTTPServer, ServeOptions, WEBVIEW_ORIGINS,  # noqa: E402
                   make_handler, parent_gone, ready_line, serve)
from session import SessionStore                                                            # noqa: E402
from skills import SkillStore                                                               # noqa: E402

BUNDLE = ROOT / "skills" / "official"
OFFICIAL_SKILL = "skilyst/video-15s"
MODEL = "test/model"
TOKEN = "test-token-0123456789"
ENV_KEYS = ("SKILYST_ENV_FILE", "SKILYST_DEV_PROFILE", "SKILYST_LLM_BASE_URL", "SKILYST_LLM_API_KEY",
            "SKILYST_LLM_MODEL", "SKILYST_LLM_CONFIG", "SKILYST_LLM_FALLBACKS", "BEEHIVE_API",
            "BEEHIVE_PLATFORM_AK", "BEEHIVE_PLATFORM_SK", "BEEHIVE_PLATFORM_USER",
            "BEEHIVE_PLATFORM_PASS", "BEEHIVE_PLATFORM_UID")


def chat_response(content="", tool_calls=None, finish_reason="stop", usage=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": call.get("id", f"call_{i}"), "type": "function",
             "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}))}}
            for i, call in enumerate(tool_calls)]
        finish_reason = "tool_calls"
    return {"model": MODEL, "choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class ScriptedChat:
    """Stands in for the model: scripted completions plus scripted stream frames."""

    def __init__(self, responses=None, frame_batches=None):
        self.responses = list(responses or [])
        self.frame_batches = list(frame_batches or [])
        self.requests = []

    def post(self, url, body, headers, timeout):
        self.requests.append(body)
        if not self.responses:
            raise AssertionError("the loop made more LLM calls than the script provides")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post_stream(self, url, body, headers, timeout):
        self.requests.append(body)
        if not self.frame_batches:
            raise AssertionError("the loop asked to stream but no frames are scripted")
        return iter(self.frame_batches.pop(0))


class ServeFixture:
    """A real server on an ephemeral port, a real store, a scripted model."""

    def __init__(self, *, credentials=True, dry_run=True, responses=None, frame_batches=None):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.store = SkillStore(root / "store")
        self.store.preload_official_bundle(BUNDLE)
        self.sessions = SessionStore(root / "sessions")
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        lines = ["SKILYST_LLM_BASE_URL=https://llm.invalid/v1", "SKILYST_LLM_API_KEY=test-key",
                 f"SKILYST_LLM_MODEL={MODEL}"]
        if credentials:
            lines += ["BEEHIVE_PLATFORM_AK=AKTEST", "BEEHIVE_PLATFORM_SK=SKTEST"]
        self.env_file = root / "env"
        self.env_file.write_text("\n".join(lines) + "\n")
        self.env_file.chmod(0o600)

        self.chat = ScriptedChat(responses=responses, frame_batches=frame_batches)
        kwargs = {"env_file": self.env_file, "store_dir": self.store.root,
                  "workspace_dir": self.workspace, "session_dir": self.sessions.root}
        self.resolve_kwargs = kwargs
        cfg = resolve(**kwargs, require_llm=False, require_beehive=False)
        api = RuntimeAPI(cfg, resolve_kwargs=kwargs, dry_run=dry_run,
                         client_factory=lambda c: ChatClient(c, post=self.chat.post,
                                                             post_stream=self.chat.post_stream))
        self.httpd = RuntimeHTTPServer(("127.0.0.1", 0),
                                       make_handler(api, TOKEN, WEBVIEW_ORIGINS, lambda _m: None))
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()

    def cleanup(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    # -- HTTP helper --------------------------------------------------------
    def request(self, method, path, body=None, token=TOKEN, origin=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        if origin:
            req.add_header("Origin", origin)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                status = resp.status
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8")
            headers = {k.lower(): v for k, v in exc.headers.items()}
            status = exc.code
        if raw:
            return status, headers, text
        return status, headers, (json.loads(text) if text.strip() else {})


class EnvIsolation(unittest.TestCase):
    """A stray SKILYST_* in the developer's shell must not decide what these tests prove."""

    def setUp(self):
        self.saved = {key: os.environ.pop(key, None) for key in ENV_KEYS}
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self.saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value


class AuthTests(EnvIsolation):
    def setUp(self):
        super().setUp()
        self.fx = ServeFixture()
        self.addCleanup(self.fx.cleanup)

    def test_health_needs_the_token_like_every_other_route(self):
        status, _headers, body = self.fx.request("GET", "/health", token=None)
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"]["kind"], "Unauthorised")

    def test_a_wrong_token_is_refused(self):
        status, _headers, body = self.fx.request("GET", "/health", token="not-the-token")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["kind"], "Unauthorised")

    def test_the_token_is_the_capability_the_shell_gets_from_the_ready_line(self):
        status, _headers, body = self.fx.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["ok"])
        self.assertEqual(body["data"]["api_version"], API_VERSION)

    def test_an_unauthenticated_message_never_reaches_the_loop(self):
        status, _headers, _body = self.fx.request("POST", "/message", {"message": "hi"}, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.fx.chat.requests, [])
        self.assertEqual(self.fx.sessions.list(), [])

    def test_an_unknown_route_is_404_and_a_bad_body_is_400(self):
        self.assertEqual(self.fx.request("GET", "/nope")[0], 404)
        self.assertEqual(self.fx.request("POST", "/nope", {})[0], 404)
        self.assertEqual(self.fx.request("POST", "/message", {"nope": 1})[0], 400)


class ConfigSurfaceTests(EnvIsolation):
    def setUp(self):
        super().setUp()
        self.fx = ServeFixture()
        self.addCleanup(self.fx.cleanup)

    def test_config_is_redacted_over_the_wire(self):
        status, _headers, body = self.fx.request("GET", "/config")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["llm"]["api_key"], "set")
        self.assertEqual(body["data"]["beehive"]["secret_key"], "set")
        self.assertNotIn("test-key", json.dumps(body))

    def test_paid_work_is_off_by_default(self):
        body = self.fx.request("GET", "/health")[2]
        self.assertTrue(body["data"]["dry_run"])

    def test_live_mode_is_an_explicit_choice(self):
        fx = ServeFixture(dry_run=False)
        self.addCleanup(fx.cleanup)
        self.assertFalse(fx.request("GET", "/health")[2]["data"]["dry_run"])

    def test_doctor_reports_integrity_without_needing_a_credential(self):
        status, _headers, body = self.fx.request("GET", "/doctor")
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["runnable"])
        self.assertTrue(all(row["ok"] for row in body["data"]["integrity"]))
        self.assertEqual([s["skill_id"] for s in body["data"]["skills"]], [OFFICIAL_SKILL])

    def test_doctor_with_a_skill_needs_the_platform_credential(self):
        fx = ServeFixture(credentials=False)
        self.addCleanup(fx.cleanup)
        status, _headers, body = fx.request("GET", f"/doctor?skill={OFFICIAL_SKILL}")
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["kind"], "ConfigError")
        self.assertIn("BEEHIVE_PLATFORM_AK", body["error"]["message"])


class SessionRouteTests(EnvIsolation):
    def setUp(self):
        super().setUp()
        self.fx = ServeFixture()
        self.addCleanup(self.fx.cleanup)

    def test_sessions_can_be_created_listed_read_and_deleted(self):
        status, _headers, body = self.fx.request("POST", "/sessions", {"title": "a shell session"})
        self.assertEqual(status, 200)
        session_id = body["data"]["session_id"]

        listed = self.fx.request("GET", "/sessions")[2]["data"]
        self.assertEqual([row["session_id"] for row in listed["sessions"]], [session_id])

        detail = self.fx.request("GET", f"/session/{session_id}")[2]["data"]
        self.assertEqual(detail["meta"]["title"], "a shell session")
        self.assertEqual(detail["messages"], [])
        self.assertEqual(detail["artifacts"], [])

        self.assertEqual(self.fx.request("DELETE", f"/session/{session_id}")[2]["data"]["deleted"],
                         session_id)
        self.assertEqual(self.fx.request("GET", f"/session/{session_id}")[0], 404)
        self.assertFalse((self.fx.sessions.root / session_id).exists())

    def test_an_unknown_session_is_404(self):
        self.assertEqual(self.fx.request("GET", "/session/20260101-000000-abcdef")[0], 404)

    def test_a_session_id_cannot_escape_the_sessions_directory(self):
        for bad in ("..%2F..%2Fetc", "%2e%2e", "nested%2Fchild", ".hidden"):
            status, _headers, _body = self.fx.request("GET", f"/session/{bad}")
            self.assertEqual(status, 400, bad)
            self.assertEqual(self.fx.request("DELETE", f"/session/{bad}")[0], 400, bad)


class MessageTests(EnvIsolation):
    def setUp(self):
        super().setUp()

    def test_a_message_runs_the_loop_and_persists_the_turn(self):
        fx = ServeFixture(responses=[chat_response(content="One skill is installed.")])
        self.addCleanup(fx.cleanup)
        status, _headers, body = fx.request("POST", "/message",
                                            {"message": "what skills do you have?"})
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["answer"], "One skill is installed.")
        self.assertEqual(data["stop_reason"], "completed")
        self.assertEqual(data["turns"], 1)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["skill"], None)
        self.assertEqual(data["artifacts"], [])

        # the transcript is on disk under the id the shell was given
        messages = SessionStore(fx.sessions.root).open(data["session_id"]).messages
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "what skills do you have?")
        # and the model saw the skill index (progressive disclosure layer 1)
        self.assertIn(OFFICIAL_SKILL, json.dumps(fx.chat.requests[0]["messages"]))

    def test_a_second_message_continues_the_same_session(self):
        fx = ServeFixture(responses=[chat_response(content="first"), chat_response(content="second")])
        self.addCleanup(fx.cleanup)
        session_id = fx.request("POST", "/message", {"message": "one"})[2]["data"]["session_id"]
        second = fx.request("POST", "/message", {"message": "two", "session_id": session_id})[2]["data"]
        self.assertEqual(second["session_id"], session_id)
        self.assertEqual([m["content"] for m in
                          SessionStore(fx.sessions.root).open(session_id).messages],
                         ["one", "first", "two", "second"])
        # the second call carried the earlier turns into the prompt
        self.assertEqual(len(fx.chat.requests[1]["messages"]), 4)     # system + 2 prior + new user

    def test_tool_calls_are_reported_and_notes_carry_progress(self):
        fx = ServeFixture(responses=[chat_response(tool_calls=[{"name": "list_skills",
                                                               "arguments": {}}]),
                                     chat_response(content="listed")])
        self.addCleanup(fx.cleanup)
        data = fx.request("POST", "/message", {"message": "list your skills"})[2]["data"]
        self.assertEqual(data["tool_calls"][0]["tool"], "list_skills")
        self.assertEqual(data["turns"], 2)
        self.assertTrue(any("list_skills" in line for line in data["notes"]))
        self.assertIn(OFFICIAL_SKILL, json.dumps(data["tool_calls"][0]["result"]))

    def test_a_model_failure_is_reported_as_a_failed_run_not_a_silent_success(self):
        """The run executed, so the status is 200 -- but `ok` is false and the error is verbatim."""
        fx = ServeFixture(responses=[LLMError("LLM HTTP 401: bad key", status=401)])
        self.addCleanup(fx.cleanup)
        status, _headers, body = fx.request("POST", "/message", {"message": "hi"})
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertFalse(data["ok"])
        self.assertEqual(data["stop_reason"], "llm_error")
        self.assertIn("401", data["error"])
        self.assertEqual(data["answer"], "")
        # the failed turn is still on disk (the loop breaks before writing an assistant
        # message on an llm_error, and records the failure on the trace)
        session = SessionStore(fx.sessions.root).open(data["session_id"])
        self.assertEqual([m["role"] for m in session.messages], ["user"])
        self.assertEqual([row["kind"] for row in session.trace], ["llm_error"])

    def test_a_skill_without_a_credential_fails_loudly(self):
        fx = ServeFixture(credentials=False)
        self.addCleanup(fx.cleanup)
        status, _headers, body = fx.request("POST", "/message",
                                            {"message": "make a video", "skill": OFFICIAL_SKILL})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"]["kind"], "ConfigError")
        self.assertEqual(fx.chat.requests, [])

    def test_a_missing_message_is_refused(self):
        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        self.assertEqual(fx.request("POST", "/message", {"message": "   "})[0], 400)
        self.assertEqual(fx.request("POST", "/message", None)[0], 400)

    def test_a_model_override_applies_to_that_message_only(self):
        fx = ServeFixture(responses=[chat_response(content="one"), chat_response(content="two")])
        self.addCleanup(fx.cleanup)
        fx.request("POST", "/message", {"message": "hi", "model": "override/model"})
        self.assertEqual(fx.chat.requests[0]["model"], "override/model")
        fx.request("POST", "/message", {"message": "again"})
        self.assertEqual(fx.chat.requests[1]["model"], MODEL)          # the default is untouched

    def test_a_dry_run_server_cannot_be_talked_into_spending_per_message(self):
        fx = ServeFixture(responses=[chat_response(content="ok")])
        self.addCleanup(fx.cleanup)
        data = fx.request("POST", "/message", {"message": "hi", "dry_run": False})[2]["data"]
        self.assertTrue(data["dry_run"])

    def test_a_live_server_honours_a_per_message_dry_run_request(self):
        fx = ServeFixture(dry_run=False, responses=[chat_response(content="ok"),
                                                   chat_response(content="ok")])
        self.addCleanup(fx.cleanup)
        cautious = fx.request("POST", "/message", {"message": "hi", "dry_run": True})[2]["data"]
        self.assertTrue(cautious["dry_run"])
        live = fx.request("POST", "/message", {"message": "hi", "dry_run": False})[2]["data"]
        self.assertFalse(live["dry_run"])

    def test_streaming_emits_delta_and_done_events(self):
        frames = ['data: {"model": "test/model", "choices": [{"delta": {"content": "Hel"}}]}',
                  'data: {"choices": [{"delta": {"content": "lo"}}]}',
                  'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], '
                  '"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}',
                  "data: [DONE]"]
        fx = ServeFixture(frame_batches=[frames])
        self.addCleanup(fx.cleanup)
        status, headers, text = fx.request("POST", "/message",
                                           {"message": "say hello", "stream": True}, raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/event-stream"))
        events = parse_sse(text)
        self.assertEqual([name for name, _ in events], ["delta", "delta", "done"])
        self.assertEqual("".join(data["text"] for name, data in events if name == "delta"), "Hello")
        done = [data for name, data in events if name == "done"][0]["data"]
        self.assertEqual(done["answer"], "Hello")
        self.assertTrue(done["ok"])

    def test_a_stream_that_dies_mid_flight_ends_with_a_failed_done_event(self):
        """The loop owns model failures: the stream still ends with `done`, and `ok` is false."""
        def broken(_url, _body, _headers, _timeout):
            def gen():
                yield 'data: {"choices": [{"delta": {"content": "half"}}]}'
                raise LLMError("connection reset", retryable=True)
            return gen()

        fx = ServeFixture(frame_batches=[])
        self.addCleanup(fx.cleanup)
        fx.chat.post_stream = broken
        status, _headers, text = fx.request("POST", "/message",
                                            {"message": "hi", "stream": True}, raw=True)
        self.assertEqual(status, 200)
        events = parse_sse(text)
        self.assertEqual([name for name, _ in events], ["delta", "done"])
        self.assertEqual(events[0][1]["text"], "half")
        done = events[1][1]["data"]
        self.assertFalse(done["ok"])
        self.assertEqual(done["stop_reason"], "llm_error")
        self.assertIn("connection reset", done["error"])

    def test_a_refused_stream_emits_an_error_event(self):
        """A refusal happens before the loop exists, so the caller gets an `error` event."""
        fx = ServeFixture(credentials=False)
        self.addCleanup(fx.cleanup)
        status, _headers, text = fx.request("POST", "/message",
                                            {"message": "make a video", "skill": OFFICIAL_SKILL,
                                             "stream": True}, raw=True)
        self.assertEqual(status, 200)
        events = parse_sse(text)
        self.assertEqual([name for name, _ in events], ["error"])
        self.assertEqual(events[0][1]["error"]["kind"], "ConfigError")
        self.assertEqual(fx.chat.requests, [])


class ConnectionTests(EnvIsolation):
    def test_a_request_body_is_consumed_so_the_connection_can_be_reused(self):
        """A route that ignores its body must still drain it, or the next request 400s."""
        fx = ServeFixture(responses=[chat_response(content="ok")])
        self.addCleanup(fx.cleanup)
        conn = HTTPConnection("127.0.0.1", fx.port, timeout=10)
        self.addCleanup(conn.close)
        auth = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

        conn.request("POST", "/message", body=json.dumps({"message": "hi"}), headers=auth)
        first = conn.getresponse()
        self.assertEqual(first.status, 200)
        first.read()

        conn.request("GET", "/health", headers=auth)
        second = conn.getresponse()
        self.assertEqual(second.status, 200)
        self.assertTrue(json.loads(second.read())["data"]["ok"])


class CorsTests(EnvIsolation):
    def setUp(self):
        super().setUp()
        self.fx = ServeFixture()
        self.addCleanup(self.fx.cleanup)

    def test_preflight_is_answered_for_a_webview_origin(self):
        status, headers, _text = self.fx.request("OPTIONS", "/message", origin="tauri://localhost")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("access-control-allow-origin"), "tauri://localhost")
        self.assertIn("authorization", headers.get("access-control-allow-headers", ""))

    def test_a_foreign_origin_is_not_granted_cors(self):
        _status, headers, _text = self.fx.request("OPTIONS", "/message", origin="https://evil.example")
        self.assertNotIn("access-control-allow-origin", headers)
        _status, headers, _text = self.fx.request("GET", "/health", origin="https://evil.example")
        self.assertNotIn("access-control-allow-origin", headers)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name and data is not None:
            events.append((name, data))
    return events


class LifecycleTests(EnvIsolation):
    """The two things the Rust supervisor relies on: the ready line, and a clean stop."""

    def test_ready_line_reports_the_ephemeral_port_and_the_token(self):
        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        line = ready_line(fx.port, TOKEN, ServeOptions(), resolve(**fx.resolve_kwargs,
                                                                  require_llm=False,
                                                                  require_beehive=False))
        self.assertEqual(line["event"], "ready")
        self.assertEqual(line["port"], fx.port)
        self.assertEqual(line["token"], TOKEN)
        self.assertEqual(line["api_version"], API_VERSION)
        self.assertTrue(line["dry_run"])

    def test_serve_starts_on_a_free_port_and_stops_when_asked(self):
        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        cfg = resolve(**fx.resolve_kwargs, require_llm=False, require_beehive=False)
        captured = {}
        thread = threading.Thread(target=serve,
                                  kwargs={"cfg": cfg, "options": ServeOptions(port=0, token=TOKEN),
                                          "resolve_kwargs": fx.resolve_kwargs,
                                          "on_ready": captured.update, "note": lambda _m: None},
                                  daemon=True)
        thread.start()
        for _ in range(100):                     # wait for the ready line
            if captured:
                break
            threading.Event().wait(0.05)
        self.assertTrue(captured, "serve() never reported itself ready")
        self.assertEqual(captured["event"], "ready")
        self.assertGreater(captured["port"], 0)

        url = f"http://127.0.0.1:{captured['port']}/health"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)

        stop = urllib.request.Request(f"http://127.0.0.1:{captured['port']}/shutdown", data=b"{}",
                                      method="POST", headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(stop, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "POST /shutdown did not stop the server")

    def test_the_runtime_stops_when_its_stdin_closes(self):
        """Orphan guard: the shell holds our stdin, so EOF means the shell is gone."""
        class Stdin:
            def __init__(self):
                self.reads = 0

            def read(self, _size):
                self.reads += 1
                return "" if self.reads > 1 else "x"

        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        cfg = resolve(**fx.resolve_kwargs, require_llm=False, require_beehive=False)
        captured = {}
        thread = threading.Thread(target=serve,
                                  kwargs={"cfg": cfg,
                                          "options": ServeOptions(port=0, token=TOKEN,
                                                                  orphan_guard=True),
                                          "resolve_kwargs": fx.resolve_kwargs,
                                          "on_ready": captured.update, "note": lambda _m: None,
                                          "stdin": Stdin()},
                                  daemon=True)
        thread.start()
        for _ in range(100):
            if captured:
                break
            threading.Event().wait(0.05)
        self.assertTrue(captured, "serve() never reported itself ready")
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "stdin EOF did not stop the server")

    def test_the_guard_stops_the_server_when_the_parent_disappears(self):
        """stdin EOF alone was not enough in the real shell, so the parent pid is watched too."""
        class BlockingStdin:
            def read(self, _size):
                threading.Event().wait(0.05)
                return "x"                      # never EOF: only the parent check can stop this

        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        cfg = resolve(**fx.resolve_kwargs, require_llm=False, require_beehive=False)
        captured = {}
        thread = threading.Thread(target=serve,
                                  kwargs={"cfg": cfg,
                                          "options": ServeOptions(port=0, token=TOKEN,
                                                                  orphan_guard=True),
                                          "resolve_kwargs": fx.resolve_kwargs,
                                          "on_ready": captured.update, "note": lambda _m: None,
                                          "stdin": BlockingStdin()},
                                  daemon=True)
        calls = {"n": 0}

        def fake_getppid():
            # The first call records the parent at startup; every later call reports a
            # process that has been reparented to pid 1.
            calls["n"] += 1
            return 4242 if calls["n"] == 1 else 1

        with mock.patch("serve.PARENT_POLL_S", 0.05), \
                mock.patch("serve.os.getppid", side_effect=fake_getppid):
            thread.start()
            for _ in range(100):
                if captured:
                    break
                threading.Event().wait(0.05)
            self.assertTrue(captured, "serve() never reported itself ready")
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "the parent-pid watch did not stop the server")

    def test_a_broken_log_pipe_does_not_prevent_the_stop(self):
        """The shell's death is exactly when our stderr has no reader: logging must not raise."""
        class Stdin:
            def __init__(self):
                self.reads = 0

            def read(self, _size):
                self.reads += 1
                return "" if self.reads > 1 else "x"

        def broken_log(_message):
            raise BrokenPipeError(32, "Broken pipe")

        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        cfg = resolve(**fx.resolve_kwargs, require_llm=False, require_beehive=False)
        captured = {}
        thread = threading.Thread(target=serve,
                                  kwargs={"cfg": cfg,
                                          "options": ServeOptions(port=0, token=TOKEN,
                                                                  orphan_guard=True),
                                          "resolve_kwargs": fx.resolve_kwargs,
                                          "on_ready": captured.update, "note": broken_log,
                                          "stdin": Stdin()},
                                  daemon=True)
        thread.start()
        for _ in range(100):
            if captured:
                break
            threading.Event().wait(0.05)
        self.assertTrue(captured, "serve() never reported itself ready")
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "a broken log pipe stopped the guard from stopping")

    def test_parent_gone_is_posix_only(self):
        with mock.patch("serve.os.getppid", return_value=1):
            self.assertTrue(parent_gone(4242))
        with mock.patch("serve.os.getppid", return_value=4242):
            self.assertFalse(parent_gone(4242))
        with mock.patch("serve.os.name", "nt"):
            self.assertFalse(parent_gone(4242))

    def test_without_the_guard_a_closed_stdin_is_ignored(self):
        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        cfg = resolve(**fx.resolve_kwargs, require_llm=False, require_beehive=False)
        captured = {}
        thread = threading.Thread(target=serve,
                                  kwargs={"cfg": cfg, "options": ServeOptions(port=0, token=TOKEN),
                                          "resolve_kwargs": fx.resolve_kwargs,
                                          "on_ready": captured.update, "note": lambda _m: None},
                                  daemon=True)
        thread.start()
        for _ in range(100):
            if captured:
                break
            threading.Event().wait(0.05)
        self.assertTrue(captured)
        thread.join(timeout=1)
        self.assertTrue(thread.is_alive(), "the default serve must not exit on stdin EOF")
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{captured['port']}/shutdown", data=b"{}", method="POST",
            headers={"Authorization": f"Bearer {TOKEN}"}), timeout=5).read()
        thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
