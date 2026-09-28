"""Canvas agent-layer tests: the 12 registry tools, the R3 action stream, lock notes.

Builds on the test_canvas.py fakes (WorkflowStore / FakeTransport) so the
registry->CanvasOps->transport chain runs against a real in-memory board. The
skill-side fixtures follow test_agent.py's make_skill pattern: a tiny published
package whose skill_id / permission decide what build_registry registers.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.tools import CANVAS_TOOLS, build_registry  # noqa: E402
from beehive import BeehiveClient, RestrictedToken  # noqa: E402
from canvas import LockHeldError  # noqa: E402
from sandbox import PermissionGate  # noqa: E402
from session import SessionStore  # noqa: E402
from skills import content_digest, load_package  # noqa: E402

# -- the canvas fakes, reused from test_canvas.py ------------------------------

BASE = "https://beehive.invalid"
SESSION = "20260928-120000-abcdef"


def _wrap(payload: dict) -> str:
    return json.dumps({"code": 0, "message": "ok", "payload": payload})


class WorkflowStore:
    def __init__(self, workflows: dict | None = None):
        import copy
        self.workflows = copy.deepcopy(workflows or {})
        self.locked_by: dict[str, dict] = {}

    def handle(self, method: str, path: str, body):
        if path.endswith("/lock"):
            wid = path.split("/")[-2]
            if wid in self.locked_by and self.locked_by[wid].get("id") != SESSION:
                return 409, _wrap({"held_by": {"holder": self.locked_by[wid],
                                               "acquired_at": "2026-09-28T10:00:00Z"}})
            self.locked_by[wid] = {"kind": "agent-session", "id": SESSION}
            return 200, _wrap({"lock": {"holder": self.locked_by[wid]}})
        if path.endswith("/unlock"):
            wid = path.split("/")[-2]
            self.locked_by.pop(wid, None)
            return 200, _wrap({"ok": True})
        if method == "GET" and path == "/api/v1/workflows?limit=50":
            return 200, _wrap({"workflows": [
                {"id": wid, "name": wf.get("name"), "description": "",
                 "updated_at": wf.get("updated_at"), "nodes": wf.get("nodes"),
                 "media_pool": wf.get("media_pool")}
                for wid, wf in self.workflows.items()]})
        if method == "GET" and path.startswith("/api/v1/workflows/"):
            wid = path.split("/")[-1].split("?")[0]
            if wid not in self.workflows:
                return 404, _wrap({"code": 404, "message": "not found"})
            import copy
            row = copy.deepcopy(self.workflows[wid])
            row["expected_updated_at"] = row["updated_at"]
            return 200, _wrap(row)
        if method == "PUT" and path.startswith("/api/v1/workflows/"):
            wid = path.split("/")[-1]
            import copy
            if (body.get("expected_updated_at")
                    and body["expected_updated_at"] != self.workflows[wid]["updated_at"]):
                return 409, _wrap({"code": 409, "message": "stale write"})
            for key, value in body.items():
                if key not in ("expected_updated_at", "media_pool"):
                    self.workflows[wid][key] = copy.deepcopy(value)
            return 200, _wrap(self.workflows[wid])
        return 404, _wrap({"code": 404, "message": f"unhandled {method} {path}"})


class FakeTransport:
    def __init__(self, store: WorkflowStore):
        self.store = store
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, data, timeout):
        import json as _json
        path = url[len(BASE):]
        body = _json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "body": body})
        status, text = self.store.handle(method, path, body)
        return status, text


def board() -> dict:
    return {"id": "wf-1", "name": "board", "description": "",
            "nodes": [{"type": "generate", "provider": "minimax-h3",
                       "key": "generate-minimax-h3-1", "config": {"prompt": "a cat"},
                       "position": {"x": 80, "y": 80}}],
            "media_pool": [], "updated_at": "2026-09-28T12:00:00Z"}


# -- the skill-side fixtures ----------------------------------------------------

def make_skill(directory: Path, skill_id: str, secrets: bool = True,
               requires_nodes: bool = False) -> Path:
    """A minimal published package: manifest carries skill_id/permission, the
    node requirement is off by default (canvas-ops is methodology only). Built
    from the official bundle's manifest so every required field is present."""
    directory.mkdir(parents=True, exist_ok=True)
    name = skill_id.split("/")[-1]
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A canvas test skill.\nlicense: MIT\n---\n\n"
        "# Test skill\n\nUse the canvas tools.\n")
    manifest = json.loads((ROOT / "skills" / "official" / "video-15s" / "manifest.json").read_text())
    manifest.update({"skill_id": skill_id, "version": "1.0.0",
                     "description": "A canvas test skill.",
                     "permission": {"egress": [BASE], "filesystem": "workspace", "exec": "none",
                                    "secrets": secrets}})
    if requires_nodes:
        manifest["requires"] = {"nodes": [
            {"node_id": "generate:minimax-h3", "node_definition_version": ">=1.0.0",
             "optional": False, "fallback": [],
             "binding": {"tool": "beehive_submit_job", "node_id": "generate:minimax-h3",
                         "config_map": {"prompt": "prompt"}}}]}
    else:
        # official-bundle must declare requires, so canvas-ops (methodology
        # only, no node dependencies) declares an EMPTY node list.
        manifest["requires"] = {"nodes": []}
    manifest["content_digest"] = "sha256:" + "0" * 64
    (directory / "manifest.json").write_text(json.dumps(manifest))
    manifest["content_digest"] = content_digest(directory)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


class CanvasAgentFixture:
    """Everything a canvas run needs: a store with the canvas-ops package, a
    session, a fake board behind a scope-gated client."""

    def __init__(self, workflows: dict | None = None):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # directory name must equal the skill's short name (spec equality rule)
        self.store_dir = make_skill(self.root / "store" / "canvas-ops", "skilyst/canvas-ops")
        self.canvas_pkg = load_package(self.store_dir)
        self.other_dir = make_skill(self.root / "store" / "video-15s", "skilyst/video-15s",
                                     requires_nodes=True)
        self.other_pkg = load_package(self.other_dir)
        self.nosecrets_dir = make_skill(self.root / "nosecrets" / "canvas-ops",
                                        "skilyst/canvas-ops", secrets=False)
        self.nosecrets_pkg = load_package(self.nosecrets_dir)
        self.wf_store = WorkflowStore(workflows)
        self.transport = FakeTransport(self.wf_store)
        self.client = BeehiveClient(BASE, token=RestrictedToken("ak", "sk"))
        self.client._transport = self.transport
        self.sessions = SessionStore(self.root / "sessions")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        self._tmp.cleanup()

    def session(self):
        return self.sessions.create(title="canvas", model="test/model",
                                    workspace=str(self.workspace))

    def gate(self, package):
        return PermissionGate(package.dir, package.permission, workspace=self.workspace)

    def registry(self, package, session_id="sess-1", on_action=None, on_event=None):
        return build_registry(self.sessions, self.client, package, self.gate(package),
                              self.workspace, session_id=session_id, on_action=on_action,
                              on_event=on_event)


class CanvasRegistryTests(unittest.TestCase):
    def setUp(self):
        self.fx = CanvasAgentFixture({"wf-1": board()})
        self.addCleanup(self.fx.cleanup)

    def test_canvas_skill_gets_all_canvas_tools(self):
        registry = self.fx.registry(self.fx.canvas_pkg)
        for name in CANVAS_TOOLS:
            self.assertIn(name, registry.names, name)
        self.assertEqual(sorted(n for n in registry.names if n.startswith("canvas_")),
                         sorted(CANVAS_TOOLS))
        # the FR-3 ten are all present (plus the two board reads and the S4
        # media-pool lifecycle pair)
        self.assertEqual(len([n for n in CANVAS_TOOLS if n != "canvas_list_workflows"
                              and n != "canvas_read_board"
                              and n not in ("canvas_rename_media", "canvas_delete_media")]), 10)

    def test_a_non_canvas_skill_gets_no_canvas_tools(self):
        registry = self.fx.registry(self.fx.other_pkg)
        self.assertEqual([n for n in registry.names if n.startswith("canvas_")], [])

    def test_canvas_skill_without_secrets_gets_no_canvas_tools(self):
        registry = self.fx.registry(self.fx.nosecrets_pkg)
        self.assertEqual([n for n in registry.names if n.startswith("canvas_")], [])

    def test_no_client_no_canvas_tools(self):
        from agent.tools import build_registry as br
        registry = br(self.fx.sessions, None, self.fx.canvas_pkg, None, self.fx.workspace,
                      session_id="sess-1")
        self.assertEqual([n for n in registry.names if n.startswith("canvas_")], [])

    def test_create_node_through_the_registry_lands_an_action_row(self):
        session = self.fx.session()
        registry = self.fx.registry(self.fx.canvas_pkg, session_id=session.session_id)

        def record(action):
            session.append_action(action)

        # rebuild with the recording callback wired (as open_run does)
        registry = self.fx.registry(self.fx.canvas_pkg, session_id=session.session_id,
                                    on_action=record)
        result = registry.call("canvas_create_node",
                               {"workflow_id": "wf-1", "node_type": "process",
                                "provider": "script"})
        self.assertEqual(result["node"]["key"], "process-script-2")
        # the board actually changed
        keys = [n["key"] for n in self.fx.wf_store.workflows["wf-1"]["nodes"]]
        self.assertIn("process-script-2", keys)
        # the action row landed on the transcript
        rows = session.messages
        actions = [row for row in rows if row.get("role") == "action"]
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["tool"], "canvas_create_node")
        self.assertEqual(action["type"], "canvas")
        self.assertEqual(action["origin"], "agent")
        self.assertEqual(action["board_delta"]["added_node"], "process-script-2")
        self.assertIn("duration_s", action)
        self.assertEqual(action["params"]["workflow_id"], "wf-1")
        # ... and history() (what the chat API receives) excludes it
        self.assertEqual([m for m in session.history() if m.get("role") == "action"], [])

    def test_submit_job_action_carries_cost_from_the_quote(self):
        # a quote route on the fake: every submission is quoted first
        fx = self.fx

        def quoted_transport(method, url, headers, data, timeout):
            if url.endswith("/api/v1/billing/quote"):
                return 200, _wrap({"total_estimate_usd": 250000, "total_hold": 300000,
                                   "nodes": []})
            if url.endswith("/api/v1/jobs"):
                return 201, _wrap({"id": "job-7", "status": "queued"})
            return fx.transport(method, url, headers, data, timeout)

        fx.client._transport = quoted_transport
        session = fx.session()
        recorded: list[dict] = []
        registry = fx.registry(fx.canvas_pkg, session_id=session.session_id,
                               on_action=recorded.append)
        registry.call("canvas_submit_node_job",
                      {"workflow_id": "wf-1", "node_type": "generate",
                       "provider": "minimax-h3", "config": {"prompt": "a cat"}})
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["cost"], {"estimate_usd": 0.25, "hold_micro_usd": 300000})
        self.assertEqual(recorded[0]["board_delta"]["submitted_job"], "job-7")
        self.assertEqual(recorded[0]["result_ref"]["job_id"], "job-7")

    def test_lock_held_appends_a_lock_note_and_reraises(self):
        self.fx.wf_store.locked_by["wf-1"] = {"kind": "user-session", "id": "wes@web"}
        session = self.fx.session()

        def record(action):
            note_kind = action.pop("note_kind", None)
            if note_kind:
                session.append_note(action.pop("note_text", ""), origin="system",
                                    note_kind=note_kind, tool=action.get("tool"))
                return
            session.append_action(action)

        registry = self.fx.registry(self.fx.canvas_pkg, session_id=session.session_id,
                                    on_action=record)
        with self.assertRaises(LockHeldError):
            registry.call("canvas_create_node",
                          {"workflow_id": "wf-1", "node_type": "process", "provider": "script"})
        notes = [row for row in session.messages if row.get("role") == "note"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note_kind"], "lock")
        self.assertIn("画板正被占用", notes[0]["text"])
        self.assertIn("wes@web", notes[0]["text"])
        # no action row for the refused write, and the board is untouched
        self.assertEqual([row for row in session.messages if row.get("role") == "action"], [])
        self.assertEqual(len(self.fx.wf_store.workflows["wf-1"]["nodes"]), 1)
        # history() stays chat-shaped
        self.assertEqual([m for m in session.history()
                          if m.get("role") not in ("user", "assistant", "tool", "system")], [])


class SessionActionStreamTests(unittest.TestCase):
    """The R3 message model on the session store itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self._tmp.name))
        self.session = self.store.create(title="r3", model="test/model")
        self.addCleanup(self._tmp.cleanup)

    def test_action_and_note_rows_are_persisted_and_shaped(self):
        self.session.append_message("user", "add a node")
        self.session.append_action({"type": "canvas", "tool": "canvas_create_node",
                                    "params": {"workflow_id": "wf-1"},
                                    "result_ref": {"node": "s-1"},
                                    "board_delta": {"added_node": "s-1"},
                                    "origin": "agent", "duration_s": 0.2})
        self.session.append_note("画板正被占用: held by user-session/wes@web",
                                 origin="system", note_kind="lock", tool="canvas_create_node")
        reopened = self.store.open(self.session.session_id)
        roles = [row["role"] for row in reopened.messages]
        self.assertEqual(roles, ["user", "action", "note"])
        action = reopened.messages[1]
        self.assertEqual(action["board_delta"], {"added_node": "s-1"})
        self.assertIn("seq", action)
        self.assertIn("ts", action)
        note = reopened.messages[2]
        self.assertEqual(note["note_kind"], "lock")
        self.assertEqual(note["origin"], "system")

    def test_history_excludes_action_and_note_rows(self):
        self.session.append_message("user", "hello")
        self.session.append_action({"type": "canvas", "tool": "canvas_read_board",
                                    "params": {}, "origin": "agent"})
        self.session.append_note("a note", note_kind="info")
        self.session.append_message("assistant", "hi")
        history = self.session.history()
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])

    def test_note_defaults_to_info_kind(self):
        row = self.session.append_note("plain")
        self.assertEqual(row["note_kind"], "info")
        self.assertEqual(row["origin"], "system")


if __name__ == "__main__":
    unittest.main(verbosity=2)
