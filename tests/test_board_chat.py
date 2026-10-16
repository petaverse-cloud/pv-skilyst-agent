"""#103 conversational board editing — the chatbox drives the canvas.

Contract pins:
1. A message with workflow_id binds the turn: the system prompt gains a
   current-board section (the model speaks board-aware from turn one).
2. An unreadable board degrades to an explicit UNAVAILABLE note in the
   prompt — never a 500, never silent.
3. Canvas tool actions dual-write: transcript (unchanged) AND the stream
   callback (the webview's live-refresh hook).
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_serve import (BUNDLE, EnvIsolation, MODEL, ServeFixture, TOKEN,  # noqa: E402
                        chat_response)
from agent import runner as runner_mod                     # noqa: E402
from agent.runner import open_run                          # noqa: E402
from canvas import CanvasOps                               # noqa: E402
from config import resolve                                 # noqa: E402


class BoardBoundMessageTests(EnvIsolation):
    def setUp(self):
        super().setUp()

    def test_a_bound_turn_injects_the_board_section_into_the_prompt(self):
        fx = ServeFixture(responses=[chat_response(content="Editing the board.")])
        self.addCleanup(fx.cleanup)
        board = {"name": "Dawn board", "nodes": [
            {"key": "gen", "node_type": "image.gen", "depends_on": [],
             "config": {"prompt": "dawn", "material_deps": []}},
        ], "edges": [], "media_pool": []}
        real_read = CanvasOps.read_board
        def fake_read(self, workflow_id):
            assert workflow_id == "wf-1"
            return board
        CanvasOps.read_board = fake_read
        self.addCleanup(lambda: setattr(CanvasOps, "read_board", real_read))
        status, _headers, body = fx.request("POST", "/message",
                                            {"message": "make it warmer",
                                             "workflow_id": "wf-1"})
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["ok"])
        prompt = json.dumps(fx.chat.requests[0]["messages"])
        self.assertIn("Current board: wf-1 (Dawn board)", prompt)
        self.assertIn("gen: image.gen", prompt)
        self.assertIn("canvas tool calls for it use this workflow_id", prompt)

    def test_an_empty_board_prompts_the_agent_to_create_the_first_node(self):
        fx = ServeFixture(responses=[chat_response(content="Creating.")])
        self.addCleanup(fx.cleanup)
        real_read = CanvasOps.read_board
        CanvasOps.read_board = lambda self, workflow_id: {"name": "Empty", "nodes": [], "edges": []}
        self.addCleanup(lambda: setattr(CanvasOps, "read_board", real_read))
        fx.request("POST", "/message", {"message": "add a node", "workflow_id": "wf-2"})
        prompt = json.dumps(fx.chat.requests[0]["messages"])
        self.assertIn("create the first node", prompt)

    def test_an_unreadable_board_degrades_to_an_explicit_note_not_a_crash(self):
        fx = ServeFixture(responses=[chat_response(content="Board is unreachable.")])
        self.addCleanup(fx.cleanup)
        real_read = CanvasOps.read_board
        def boom(self, workflow_id):
            raise RuntimeError("GET failed: HTTP 500")
        CanvasOps.read_board = boom
        self.addCleanup(lambda: setattr(CanvasOps, "read_board", real_read))
        status, _headers, body = fx.request("POST", "/message",
                                            {"message": "edit the board", "workflow_id": "wf-x"})
        self.assertEqual(status, 200, "an unavailable board must not 500 the turn")
        prompt = json.dumps(fx.chat.requests[0]["messages"])
        self.assertIn("UNAVAILABLE", prompt)
        self.assertIn("wf-x", prompt)

    def test_a_message_without_a_board_binding_is_exactly_today_s_turn(self):
        fx = ServeFixture(responses=[chat_response(content="No board talk.")])
        self.addCleanup(fx.cleanup)
        status, _headers, body = fx.request("POST", "/message", {"message": "hello"})
        self.assertEqual(status, 200)
        prompt = json.dumps(fx.chat.requests[0]["messages"])
        self.assertNotIn("Current board:", prompt)


class StreamedActionTests(EnvIsolation):
    """The dual-write seam: canvas actions reach BOTH the transcript (the
    unchanged R3 duty) AND the stream callback (the #103 live wire)."""

    def setUp(self):
        super().setUp()

    def _open_with_stream_hook(self, seen):
        fx = ServeFixture()
        self.addCleanup(fx.cleanup)
        real_gated = runner_mod.gated_client
        runner_mod.gated_client = lambda cfg, bypass=False: object()
        self.addCleanup(lambda: setattr(runner_mod, "gated_client", real_gated))
        cfg = resolve(**fx.resolve_kwargs, require_llm=False, require_beehive=False)
        ctx = open_run(cfg, workflow_id="wf-9", on_stream_action=seen.append)
        self.addCleanup(fx.cleanup)
        return ctx

    def test_canvas_actions_dual_write_transcript_and_stream(self):
        seen = []
        ctx = self._open_with_stream_hook(seen)
        rows_before = len(ctx.session.messages)
        action = {"tool": "canvas_create_node", "workflow_id": "wf-9",
                  "board_delta": {"added": ["n1"]}}
        ctx.record_action(dict(action))
        # transcript duty: the action row landed on the session
        self.assertEqual(len(ctx.session.messages), rows_before + 1)
        self.assertEqual(ctx.session.messages[-1]["tool"], "canvas_create_node")
        # stream duty: the same action flowed out the #103 hook
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["tool"], "canvas_create_node")
        self.assertEqual(seen[0]["board_delta"], {"added": ["n1"]})

    def test_a_note_kind_action_streams_too_but_writes_no_action_row(self):
        seen = []
        ctx = self._open_with_stream_hook(seen)
        rows_before = len(ctx.session.messages)
        ctx.record_action({"tool": "canvas_run", "workflow_id": "wf-9",
                           "note_kind": "lock_held", "note_text": "board busy"})
        self.assertEqual(len(ctx.session.messages), rows_before + 1)  # the NOTE row
        self.assertEqual(ctx.session.messages[-1]["note_kind"], "lock_held")
        self.assertEqual(seen[0]["note_kind"], "lock_held")
        self.assertEqual(seen[0]["note_text"], "board busy")
        self.assertNotIn("board_delta", seen[0] or {})

    def test_no_hook_configured_is_today_s_behavior(self):
        fx = ServeFixture(responses=[chat_response(content="fine")])
        self.addCleanup(fx.cleanup)
        status, _headers, body = fx.request("POST", "/message", {"message": "plain turn"})
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["ok"])


class BoardBindingDegradationTests(EnvIsolation):
    """The shapes desktop-verify's round-1 probe observed and blessed —
    pinned so they cannot drift: a bound turn with NO credential must be
    the structured 409 ConfigError (loud, actionable), never a 500 and
    never a silent unbound run."""

    def setUp(self):
        super().setUp()

    def test_a_bound_turn_without_credentials_is_a_structured_409(self):
        fx = ServeFixture(credentials=False,
                          responses=[chat_response(content="unreachable")])
        self.addCleanup(fx.cleanup)
        status, headers, body = fx.request(
            "POST", "/message",
            {"message": "edit the board", "workflow_id": "wf-nocreds"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["kind"], "ConfigError")
        self.assertIn("credential", body["error"]["message"].lower())
