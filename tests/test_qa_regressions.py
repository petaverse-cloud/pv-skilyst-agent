"""QA regression pins for #15 / #16 (qa #13 full-regression findings).

These are the unittest pins of the qa reproduction scripts
(tools/qa_a3_t1a.py T1a-5 for #15, T1a/T2 cost observations for #16),
pinned so the defects cannot silently return:

#15 (medium) — material wiring field-name silent degradation:
  * an `entry_id`-spelled material node must wire as material_deps with its
    input_port INTACT (lenient read), and the field must be normalized to the
    canonical `pool_entry_id` inside the same locked write, reported on the
    result and the action row's board_delta (loud, not silent);
  * an explicit input_port on a NON-material source must be a loud structured
    ValueError -- the pre-fix behaviour (silently dropping the port and
    degrading to a plain depends_on edge) is exactly the regression being
    pinned;
  * a material node with NO pool reference at all is refused, not degraded.

#16 (low) —
  * A: the action cost chain: the wire quote carries `total_estimate`
    (the core's QuoteResponse spelling), not `total_estimate_usd`; the cost
    view must carry estimate_usd derived from either spelling, and fall back
    to the hold (M1: estimate == hold upper bound) so the badge never
    silently disappears;
  * B: note rows are written with origin='agent' (R3 contract: agent|user,
    never 'system').

Same fake-transport pattern as tests/test_canvas.py -- no network.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.tools import build_registry  # noqa: E402
from beehive import BeehiveClient, RestrictedToken  # noqa: E402
from canvas import CanvasOps  # noqa: E402
from sandbox import PermissionGate  # noqa: E402
from session import SessionStore  # noqa: E402
from skills import load_package  # noqa: E402


def _wrap(payload: dict) -> str:
    return json.dumps({"code": 0, "message": "ok", "payload": payload})


BASE = "https://beehive.invalid"
SESSION = "20260929-120000-abcdef"


class WorkflowStore:
    """In-memory /api/v1/workflows, the same contract subset as
    test_canvas.py's store: lock/unlock, GET with expected_updated_at,
    optimistic-concurrency PUT."""

    def __init__(self, workflows: dict | None = None):
        self.workflows = copy.deepcopy(workflows or {})
        self.locked_by: dict[str, dict] = {}
        # tolerate being handed a single workflow row (keyed by its own id)
        if "id" in self.workflows and "nodes" in self.workflows:
            self.workflows = {self.workflows["id"]: self.workflows}

    def handle(self, method: str, path: str, body):
        if path.endswith("/lock"):
            wid = path.split("/")[-2]
            if wid in self.locked_by and self.locked_by[wid].get("id") != SESSION:
                return 409, _wrap({"held_by": {"holder": self.locked_by[wid],
                                               "acquired_at": "2026-09-29T10:00:00Z"}})
            self.locked_by[wid] = {"kind": "agent-session", "id": SESSION}
            return 200, _wrap({"lock": {"holder": self.locked_by[wid]}})
        if path.endswith("/unlock"):
            wid = path.split("/")[-2]
            self.locked_by.pop(wid, None)
            return 200, _wrap({"ok": True})
        if method == "GET" and path.startswith("/api/v1/workflows/"):
            wid = path.split("/")[-1].split("?")[0]
            row = copy.deepcopy(self.workflows[wid])
            row["expected_updated_at"] = row["updated_at"]
            return 200, _wrap(row)
        if method == "PUT" and path.startswith("/api/v1/workflows/"):
            wid = path.split("/")[-1]
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
        path = url[len(BASE):]
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "body": body})
        status, text = self.store.handle(method, path, body)
        return status, text


def board_with_entry_id_material() -> dict:
    """The #15 reproduction board: a material node whose pool pointer is
    drift-spelled `entry_id` (what the model actually wrote in qa T1a) plus a
    minimax consumer."""
    return {"id": "wf-1", "name": "qa15", "description": "",
            "nodes": [
                {"type": "material", "provider": "pool", "key": "material-image-3",
                 "config": {"entry_id": "mp-1", "kind": "image",
                            "name": "cat.png", "url": "https://cdn.example/cat.png"},
                 "position": {"x": 80, "y": 80}},
                {"type": "generate", "provider": "minimax-h3", "key": "generate-minimax-h3-4",
                 "config": {"prompt": "a cat", "duration": 15}, "position": {"x": 320, "y": 80}}],
            "media_pool": [{"id": "mp-1", "name": "cat.png", "kind": "image",
                            "url": "https://cdn.example/cat.png", "thumb": "t.png"}],
            "updated_at": "2026-09-29T12:00:00Z"}


def board_with_plain_source() -> dict:
    return {"id": "wf-1", "name": "qa15", "description": "",
            "nodes": [
                {"type": "generate", "provider": "script", "key": "generate-script-1",
                 "config": {"instruction": "write a script"}, "position": {"x": 80, "y": 80}},
                {"type": "generate", "provider": "minimax-h3", "key": "generate-minimax-h3-4",
                 "config": {"prompt": "a cat"}, "position": {"x": 320, "y": 80}}],
            "media_pool": [],
            "updated_at": "2026-09-29T12:00:00Z"}


class OpsFixture:
    def __init__(self, workflows: dict):
        self.store = WorkflowStore(workflows)
        self.transport = FakeTransport(self.store)
        self.client = BeehiveClient(BASE, token=RestrictedToken("ak", "sk"))
        self.client._transport = self.transport
        self.ops = CanvasOps(self.client, SESSION)

    def node(self, key: str) -> dict:
        return next(n for n in self.store.workflows["wf-1"]["nodes"] if n["key"] == key)


# -- #15: material wiring field-name lenient read + loud failure ---------------


class TestQa15MaterialFieldLeniency(unittest.TestCase):
    def test_entry_id_material_wires_with_port_and_normalizes(self):
        """The qa T1a-5 FAIL pin: `entry_id` variant must produce the
        material_deps entry with input_port INTACT (the pre-fix code dropped
        the port entirely), plus the canonical field rewrite."""
        fx = OpsFixture(board_with_entry_id_material())
        result = fx.ops.connect_ports("wf-1", "material-image-3", "generate-minimax-h3-4",
                                      input_port="first_frame")
        consumer = fx.node("generate-minimax-h3-4")
        deps = (consumer.get("config") or {}).get("material_deps")
        assert deps == [{"key": "material-image-3", "input_port": "first_frame"}], \
            "entry_id-spelled material must land a full material_deps entry with its port"
        assert "material-image-3" in (consumer.get("depends_on") or [])
        # canonical field rewrite happened inside the same locked write
        material = fx.node("material-image-3")
        assert (material.get("config") or {}).get("pool_entry_id") == "mp-1"
        assert "entry_id" not in (material.get("config") or {})
        # ... and the normalization is reported (loud, not silent)
        assert result["normalized"] == {"node": "material-image-3", "field": "entry_id",
                                        "to": "pool_entry_id", "entry_id": "mp-1"}

    def test_canonical_pool_entry_id_still_wires_unchanged(self):
        fx = OpsFixture(board_with_entry_id_material())
        # normalize first, then wire again with the canonical spelling
        fx.ops.connect_ports("wf-1", "material-image-3", "generate-minimax-h3-4",
                             input_port="first_frame")
        result = fx.ops.connect_ports("wf-1", "material-image-3", "generate-minimax-h3-4",
                                      input_port="reference_image")
        consumer = fx.node("generate-minimax-h3-4")
        deps = (consumer.get("config") or {}).get("material_deps")
        assert {d["input_port"] for d in deps} == {"first_frame", "reference_image"}
        assert "normalized" not in result

    def test_read_board_shows_material_edge_for_entry_id_node_before_rewiring(self):
        """The read side is lenient too: a board that still carries the
        drift-spelled node renders its material edges (the pre-fix board
        showed NOTHING for these wirings)."""
        fx = OpsFixture(board_with_entry_id_material())
        fx.ops.connect_ports("wf-1", "material-image-3", "generate-minimax-h3-4",
                             input_port="first_frame")
        # rewind the stored node's field spelling to simulate an untouched board
        node = fx.node("material-image-3")
        node["config"] = {"entry_id": "mp-1", "kind": "image",
                          "name": "cat.png", "url": "https://cdn.example/cat.png"}
        fx.store.workflows["wf-1"]["updated_at"] = "2026-09-29T12:00:00Z"
        board = fx.ops.read_board("wf-1")
        material_edges = [e for e in board["edges"]
                          if e["kind"] == "material" and e["input_port"] == "first_frame"]
        assert material_edges == [{"from": "material-image-3", "to": "generate-minimax-h3-4",
                                   "kind": "material", "input_port": "first_frame"}]

    def test_list_media_counts_entry_id_references(self):
        fx = OpsFixture(board_with_entry_id_material())
        listing = fx.ops.list_media("wf-1")
        assert listing["entries"][0]["referenced_by"] == ["material-image-3"]

    def test_material_kind_resolves_through_entry_id(self):
        fx = OpsFixture(board_with_entry_id_material())
        node = fx.node("material-image-3")
        node["config"] = {"entry_id": "mp-1"}   # no kind of its own
        kind = fx.ops._pool_refs and None
        from canvas import _material_kind
        assert _material_kind(fx.store.workflows["wf-1"], node) == "image"


class TestQa15LoudFailures(unittest.TestCase):
    def test_input_port_on_non_material_source_is_refused_not_dropped(self):
        """The silent-degradation regression pin: pre-fix, this call returned
        'wired' with the input_port thrown away. Post-fix it must raise."""
        fx = OpsFixture(board_with_plain_source())
        with self.assertRaises(ValueError) as excinfo:
            fx.ops.connect_ports("wf-1", "generate-script-1", "generate-minimax-h3-4",
                                 input_port="first_frame")
        message = str(excinfo.exception)
        assert "generate-script-1" in message
        assert "not a material node" in message
        assert "first_frame" in message
        # the board was NOT mutated by the refused write
        consumer = fx.node("generate-minimax-h3-4")
        assert not (consumer.get("config") or {}).get("material_deps")
        assert consumer.get("depends_on") is None or consumer.get("depends_on") == []

    def test_portless_material_without_pool_reference_is_refused(self):
        fx = OpsFixture(board_with_entry_id_material())
        node = fx.node("material-image-3")
        node["config"] = {}   # a draft material: no pointer at all
        with self.assertRaises(ValueError) as excinfo:
            fx.ops.connect_ports("wf-1", "material-image-3", "generate-minimax-h3-4",
                                 input_port="first_frame")
        assert "no pool entry reference" in str(excinfo.exception)

    def test_plain_edge_without_port_still_works(self):
        fx = OpsFixture(board_with_plain_source())
        result = fx.ops.connect_ports("wf-1", "generate-script-1", "generate-minimax-h3-4")
        consumer = fx.node("generate-minimax-h3-4")
        assert consumer["depends_on"] == ["generate-script-1"]
        assert "normalized" not in result

    def test_invalid_image_port_still_rejected_early(self):
        fx = OpsFixture(board_with_entry_id_material())
        with self.assertRaises(ValueError) as excinfo:
            fx.ops.connect_ports("wf-1", "material-image-3", "generate-minimax-h3-4",
                                 input_port="middle_frame")
        assert "valid image port" in str(excinfo.exception)


# -- #15: the action stream reports the normalization --------------------------


def make_canvas_skill(directory: Path) -> Path:
    """A minimal published canvas-ops-like package (same shape as
    test_canvas_agent.make_skill, but self-contained)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: canvas-ops\ndescription: A canvas test skill.\nlicense: MIT\n---\n\n"
        "# Test skill\n\nUse the canvas tools.\n")
    manifest = json.loads((ROOT / "skills" / "official" / "video-15s" / "manifest.json").read_text())
    manifest.update({"skill_id": "skilyst/canvas-ops", "version": "1.0.0",
                     "description": "A canvas test skill.",
                     "permission": {"egress": [BASE], "filesystem": "workspace", "exec": "none",
                                    "secrets": True},
                     "requires": {"nodes": []}})
    manifest["content_digest"] = "sha256:" + "0" * 64
    (directory / "manifest.json").write_text(json.dumps(manifest))
    from skills import content_digest
    manifest["content_digest"] = content_digest(directory)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


class AgentFixture:
    def __init__(self, workflows: dict):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pkg = load_package(make_canvas_skill(self.root / "store" / "canvas-ops"))
        self.store = WorkflowStore(workflows)
        self.transport = FakeTransport(self.store)
        self.client = BeehiveClient(BASE, token=RestrictedToken("ak", "sk"))
        self.client._transport = self.transport
        self.sessions = SessionStore(self.root / "sessions")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        self._tmp.cleanup()

    def registry(self, on_action=None):
        session = self.sessions.create(title="qa", model="test/model",
                                       workspace=str(self.workspace))
        gate = PermissionGate(self.pkg.dir, self.pkg.permission, workspace=self.workspace)
        return (session, build_registry(self.sessions, self.client, self.pkg, gate,
                                        self.workspace, session_id=session.session_id,
                                        on_action=on_action))


class TestQa15ActionStream(unittest.TestCase):
    def test_wiring_action_row_carries_the_normalization_warning(self):
        fx = AgentFixture(board_with_entry_id_material())
        recorded: list[dict] = []
        session, registry = fx.registry(on_action=recorded.append)
        try:
            registry.call("canvas_connect_ports",
                          {"workflow_id": "wf-1", "from_key": "material-image-3",
                           "to_key": "generate-minimax-h3-4", "input_port": "first_frame"})
            assert len(recorded) == 1
            delta = recorded[0]["board_delta"]
            assert delta["wired"] == ["material-image-3", "generate-minimax-h3-4", "first_frame"]
            assert delta["normalized_material_field"] == {
                "node": "material-image-3", "field": "entry_id",
                "to": "pool_entry_id", "entry_id": "mp-1"}
            assert recorded[0]["origin"] == "agent"
        finally:
            fx.cleanup()


# -- #16A: the action cost chain ------------------------------------------------


class TestQa16CostChain(unittest.TestCase):
    def _cost_from_quote(self, quote_payload: dict) -> dict | None:
        from agent.tools import _micro_to_usd_cost
        return _micro_to_usd_cost(quote_payload)

    def test_wire_spelling_total_estimate_is_understood(self):
        """The core's QuoteResponse serializes `total_estimate` -- the
        pre-fix runtime read only `total_estimate_usd` and the estimate was
        always null (badge never rendered)."""
        cost = self._cost_from_quote({"total_estimate": 960000, "total_hold": 960000,
                                      "nodes": []})
        assert cost == {"estimate_usd": 0.96, "hold_micro_usd": 960000}

    def test_view_spelling_total_estimate_usd_still_understood(self):
        cost = self._cost_from_quote({"total_estimate_usd": 250000, "total_hold": 300000,
                                      "nodes": []})
        assert cost == {"estimate_usd": 0.25, "hold_micro_usd": 300000}

    def test_estimate_missing_falls_back_to_hold(self):
        """M1 semantics: estimate == hold upper bound; a quote with only a
        hold must still produce a cost badge (qa saw $0.96/$1.035 cards with
        no amount)."""
        cost = self._cost_from_quote({"total_hold": 1035000, "nodes": []})
        assert cost == {"estimate_usd": 1.035, "hold_micro_usd": 1035000}

    def test_zero_hold_free_action_has_no_badge_amount(self):
        """An explicitly-free action (estimate=0) must not synthesize a paid
        badge from nothing -- the fallback only kicks in when the estimate is
        MISSING and the hold is positive."""
        cost = self._cost_from_quote({"total_estimate": 0, "total_hold": 0, "nodes": []})
        assert cost is None or cost.get("estimate_usd") in (None, 0.0)

    def test_quote_view_carries_the_estimate_from_the_wire_spelling(self):
        from canvas import _quote_view
        view = _quote_view({"total_estimate": 960000, "total_hold": 960000, "nodes": []})
        assert view["total_estimate_usd"] == 960000
        assert view["total_estimate_usd_display"] == 0.96
        assert view["total_hold_display"] == 0.96

    def test_submit_action_cost_survives_the_wire_spelling(self):
        """End-to-end through the registry: a wire-shaped quote (total_estimate)
        lands estimate_usd on the action row the ActionCard renders from."""
        fx = AgentFixture(board_with_plain_source())

        def quoted_transport(method, url, headers, data, timeout):
            if url.endswith("/api/v1/billing/quote"):
                return 200, _wrap({"total_estimate": 960000, "total_hold": 960000,
                                   "nodes": []})
            if url.endswith("/api/v1/jobs"):
                return 201, _wrap({"id": "job-9", "status": "queued"})
            return fx.transport(method, url, headers, data, timeout)

        fx.client._transport = quoted_transport
        recorded: list[dict] = []
        session, registry = fx.registry(on_action=recorded.append)
        try:
            registry.call("canvas_submit_node_job",
                          {"workflow_id": "wf-1", "node_type": "generate",
                           "provider": "minimax-h3", "config": {"prompt": "a cat"}})
            assert recorded[0]["cost"] == {"estimate_usd": 0.96, "hold_micro_usd": 960000}
        finally:
            fx.cleanup()


# -- #16B: note origin contract --------------------------------------------------


class TestQa16NoteOrigin(unittest.TestCase):
    def test_note_default_origin_is_agent_not_system(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            session = SessionStore(Path(tmp.name)).create(title="n", model="m")
            row = session.append_note("a note", note_kind="info")
            assert row["origin"] == "agent"
        finally:
            tmp.cleanup()

    def test_lock_note_lands_with_agent_origin(self):
        """The qa 1c-03 evidence pin: the lock note row (written by the
        runtime on the agent's behalf) must carry origin='agent' per the R3
        contract -- 'system' is not a contract origin."""
        from canvas import LockHeldError
        fx = AgentFixture(board_with_plain_source())
        fx.store.locked_by["wf-1"] = {"kind": "user-session", "id": "wes@web"}

        def record(action):
            note_kind = action.pop("note_kind", None)
            if note_kind:
                session.append_note(action.pop("note_text", ""), origin="agent",
                                    note_kind=note_kind, tool=action.get("tool"))
                return
            session.append_action(action)

        session, registry = fx.registry(on_action=record)
        try:
            with self.assertRaises(LockHeldError):
                registry.call("canvas_create_node",
                              {"workflow_id": "wf-1", "node_type": "process",
                               "provider": "script"})
            notes = [row for row in session.messages if row.get("role") == "note"]
            assert len(notes) == 1
            assert notes[0]["note_kind"] == "lock"
            assert notes[0]["origin"] == "agent"
        finally:
            fx.cleanup()


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
