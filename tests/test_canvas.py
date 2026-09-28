"""Canvas ops tests: the ten A3 canvas tools against a fake transport.

No test touches the network: the canvas talks through a BeehiveClient whose
transport records every call and replays scripted responses, the same pattern
as tests/test_platform.py. A tiny in-memory workflow store backs the
workflow routes so the GET-modify-PUT round-trips are actually observable.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from beehive import BeehiveClient, BeehiveError, RestrictedToken  # noqa: E402
from canvas import CanvasOps, LockHeldError  # noqa: E402

BASE = "https://beehive.invalid"
SESSION = "20260928-120000-abcdef"


def _wrap(payload: dict) -> str:
    """The beehive API wraps every response as {code, message, payload}."""
    return json.dumps({"code": 0, "message": "ok", "payload": payload})


class WorkflowStore:
    """In-memory /api/v1/workflows: enough server behaviour for the canvas
    round-trips to be honest -- id/created_at on create, 409 on a stale
    expected_updated_at, media pool preserved when the PUT body omits it."""

    def __init__(self, workflows: dict | None = None):
        self.workflows = copy.deepcopy(workflows or {})
        self.locked_by: dict[str, dict] = {}
        self.counter = 0

    def row(self, workflow_id: str) -> dict:
        return copy.deepcopy(self.workflows[workflow_id])

    def handle(self, method: str, path: str, body):
        if path.endswith("/lock"):
            wid = path.split("/")[-2]
            if wid in self.locked_by and self.locked_by[wid].get("id") != SESSION:
                return 409, _wrap({"held_by": {"holder": self.locked_by[wid],
                                               "acquired_at": "2026-09-28T10:00:00Z"}})
            self.locked_by[wid] = {"kind": "agent-session", "id": SESSION}
            return 200, _wrap({"lock": {"holder": self.locked_by[wid],
                                        "acquired_at": "2026-09-28T12:00:00Z",
                                        "heartbeat_dead_after_s": 90}})
        if path.endswith("/unlock"):
            wid = path.split("/")[-2]
            if wid in self.locked_by and self.locked_by[wid].get("id") != SESSION:
                return 403, _wrap({"code": 403, "message": "not the holder"})
            self.locked_by.pop(wid, None)
            return 200, _wrap({"ok": True})
        if path.endswith("/media-pool"):
            wid = path.split("/")[-2]
            entry = dict(body)
            entry["id"] = f"mp-{len(self.workflows[wid]['media_pool']) + 1}"
            entry["created_at"] = "2026-09-28T12:00:00Z"
            self.workflows[wid]["media_pool"].append(entry)
            return 201, _wrap(self.row(wid))
        if method == "DELETE" and "/media-pool/" in path:
            wid, entry_id = path.split("/api/v1/workflows/")[1].split("/media-pool/")
            pool = self.workflows[wid]["media_pool"]
            kept = [e for e in pool if e["id"] != entry_id]
            if len(kept) == len(pool):
                return 404, _wrap({"code": 404, "message": "media pool entry not found"})
            self.workflows[wid]["media_pool"] = kept
            return 200, _wrap(self.row(wid))
        if method == "POST" and path == "/api/v1/workflows":
            self.counter += 1
            workflow = {"id": f"wf-{self.counter}", "name": body.get("name"),
                        "description": body.get("description"), "nodes": body.get("nodes") or [],
                        "media_pool": [], "updated_at": "2026-09-28T12:00:00Z"}
            self.workflows[workflow["id"]] = workflow
            return 201, _wrap(self.row(workflow["id"]))
        if method == "GET" and path.startswith("/api/v1/workflows/"):
            wid = path.split("/")[-1].split("?")[0]
            if wid not in self.workflows:
                return 404, _wrap({"code": 404, "message": "not found"})
            row = self.row(wid)
            row["expected_updated_at"] = row["updated_at"]
            return 200, _wrap(row)
        if method == "PUT" and path.startswith("/api/v1/workflows/"):
            wid = path.split("/")[-1]
            if (body.get("expected_updated_at")
                    and body["expected_updated_at"] != self.workflows[wid]["updated_at"]):
                return 409, _wrap({"code": 409, "message": "stale write"})
            if "media_pool" in body:
                # the server contract: an echoed pool would replace the stored
                # one -- the fake enforces the preservation rule the canvas
                # relies on (absent field = keep stored pool).
                self.workflows[wid]["media_pool"] = body["media_pool"]
            for key, value in body.items():
                if key not in ("expected_updated_at", "media_pool"):
                    self.workflows[wid][key] = copy.deepcopy(value)
            self.workflows[wid]["updated_at"] = "2026-09-28T12:01:00Z"
            return 200, _wrap(self.row(wid))
        return 404, _wrap({"code": 404, "message": f"unhandled {method} {path}"})


class FakeTransport:
    """Records calls and serves the workflow store plus scripted non-workflow
    routes (nodes, jobs, quote)."""

    def __init__(self, store: WorkflowStore, nodes: list | None = None, jobs: list | None = None,
                 quotes: list | None = None):
        self.store = store
        self.nodes = nodes or []
        self.jobs = jobs or []
        self.quotes = quotes or []
        self.calls: list[dict] = []

    def __call__(self, method, url, headers, data, timeout):
        path = url[len(BASE):]
        body = json.loads(data) if data else None
        self.calls.append({"method": method, "path": path, "body": body})
        if path == "/api/v1/nodes":
            return 200, _wrap({"nodes": self.nodes})
        if path == "/api/v1/jobs?limit=50" or (path == "/api/v1/jobs" and method == "GET"):
            return 200, _wrap({"jobs": self.jobs})
        if path == "/api/v1/billing/quote":
            item = self.quotes.pop(0) if self.quotes else {"total_estimate_usd": 250000,
                                                           "total_hold": 300000, "nodes": []}
            return 200, _wrap(item)
        if path == "/api/v1/jobs":
            self.jobs.insert(0, {"id": f"job-{len(self.jobs) + 1}", "status": "queued",
                                 "workflow_id": body.get("workflow_id"), "nodes": body.get("nodes")})
            return 201, _wrap(self.jobs[0])
        status, text = self.store.handle(method, path, body)
        return status, text

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]


def minimax_definition() -> dict:
    """A minimax-h3-like registry entry: image_roles enum with the three image
    ports, plus the images/image_roles vs video_refs alternative input modes."""
    return {"id": "generate:minimax-h3", "name": "MiniMax H3",
            "description": "video generation",
            "input_schema": {
                "type": "object",
                "required": ["prompt"],
                "properties": {
                    "prompt": {"type": "string"},
                    "images": {"type": "array", "items": {"type": "string", "format": "uri"}},
                    "image_roles": {"type": "array",
                                    "items": {"enum": ["reference_image", "first_frame",
                                                       "last_frame", ""]}},
                    "video_refs": {"type": "array", "items": {"type": "string"}},
                    "duration": {"type": "integer"}}},
            "output_schema": {"dest_video_url": {"type": "string"}},
            "config_defaults": {"duration": 6}, "enabled": True}


class CanvasFixture:
    def __init__(self, workflows: dict | None = None, nodes: list | None = None,
                 jobs: list | None = None):
        self.store = WorkflowStore(workflows)
        self.transport = FakeTransport(self.store, nodes=nodes, jobs=jobs)
        self.client = BeehiveClient(BASE, token=RestrictedToken("ak", "sk"))
        self.client._transport = self.transport
        self.ops = CanvasOps(self.client, SESSION)


def workflow_with_material() -> dict:
    return {"id": "wf-1", "name": "board", "description": "",
            "nodes": [
                {"type": "material", "provider": "pool", "key": "mat-1",
                 "config": {"pool_entry_id": "mp-1"}, "position": {"x": 80, "y": 80}},
                {"type": "generate", "provider": "minimax-h3", "key": "generate-minimax-h3-2",
                 "config": {"prompt": "a cat"}, "position": {"x": 320, "y": 80}}],
            "media_pool": [{"id": "mp-1", "name": "cat.png", "kind": "image",
                            "url": "https://cdn.example/cat.png", "thumb": "t.png"}],
            "updated_at": "2026-09-28T12:00:00Z"}


class CreateWorkflowTests(unittest.TestCase):
    def test_create_posts_an_empty_node_list_and_returns_the_id(self):
        fx = CanvasFixture()
        result = fx.ops.create_workflow("board", "a cat video")
        post = next(c for c in fx.transport.calls if c["method"] == "POST")
        self.assertEqual(post["path"], "/api/v1/workflows")
        self.assertEqual(post["body"], {"name": "board", "description": "a cat video", "nodes": []})
        self.assertEqual(result["id"], "wf-1")


class CreateNodeTests(unittest.TestCase):
    def test_auto_key_position_and_media_pool_preservation(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        result = fx.ops.create_node("wf-1", "process", "script")
        # third node in the blueprint -> key suffix 3, x spread 80+240*2
        self.assertEqual(result["node"]["key"], "process-script-3")
        self.assertEqual(result["node"]["position"], {"x": 560, "y": 80})
        # the PUT body must NOT contain media_pool (absent = server preserves)
        put = next(c for c in fx.transport.calls if c["method"] == "PUT")
        self.assertNotIn("media_pool", put["body"])
        self.assertIn("expected_updated_at", put["body"])
        # and the server-side pool is intact after the write
        self.assertEqual(len(fx.store.workflows["wf-1"]["media_pool"]), 1)

    def test_explicit_key_position_and_config_are_honoured(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        result = fx.ops.create_node("wf-1", "process", "script", key="my-script",
                                    config={"prompt": "hi"}, position={"x": 1, "y": 2},
                                    depends_on=["mat-1"])
        self.assertEqual(result["node"]["key"], "my-script")
        self.assertEqual(result["node"]["position"], {"x": 1, "y": 2})
        self.assertEqual(result["node"]["config"], {"prompt": "hi"})
        self.assertEqual(result["node"]["depends_on"], ["mat-1"])

    def test_write_is_wrapped_in_lock_and_unlock(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.ops.create_node("wf-1", "process", "script")
        paths = fx.transport.paths()
        self.assertIn(("POST", "/api/v1/workflows/wf-1/lock"), paths)
        self.assertIn(("POST", "/api/v1/workflows/wf-1/unlock"), paths)
        self.assertLess(paths.index(("POST", "/api/v1/workflows/wf-1/lock")),
                        paths.index(("PUT", "/api/v1/workflows/wf-1")))
        self.assertLess(paths.index(("PUT", "/api/v1/workflows/wf-1")),
                        paths.index(("POST", "/api/v1/workflows/wf-1/unlock")))

    def test_stale_write_conflicting_twice_surfaces_loudly(self):
        wf = workflow_with_material()
        fx = CanvasFixture({"wf-1": wf})
        # a rogue writer makes every PUT stale: the first 409 is retried
        # (re-GET, re-apply), the second surfaces as a loud BeehiveError.
        real_handle = fx.store.handle

        def always_stale(method, path, body):
            if method == "PUT":
                return 409, _wrap({"code": 409, "message": "stale write"})
            return real_handle(method, path, body)

        fx.store.handle = always_stale
        with self.assertRaises(BeehiveError) as ctx:
            fx.ops.create_node("wf-1", "process", "script")
        self.assertIn("409", str(ctx.exception))
        # retried once: two PUTs total
        self.assertEqual(len([c for c in fx.transport.calls if c["method"] == "PUT"]), 2)


class WriteNodeConfigTests(unittest.TestCase):
    def test_deep_merge_adds_overwrites_and_merges_nested(self):
        wf = workflow_with_material()
        wf["nodes"][1]["config"] = {"prompt": "a cat", "style": {"color": "red", "mood": "calm"}}
        fx = CanvasFixture({"wf-1": wf})
        fx.ops.write_node_config("wf-1", "generate-minimax-h3-2",
                                 {"prompt": "a red cat", "style": {"color": "blue"},
                                  "duration": 8})
        config = fx.store.workflows["wf-1"]["nodes"][1]["config"]
        self.assertEqual(config, {"prompt": "a red cat",
                                  "style": {"color": "blue", "mood": "calm"},
                                  "duration": 8})

    def test_unknown_key_is_a_loud_value_error(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        with self.assertRaises(ValueError) as ctx:
            fx.ops.write_node_config("wf-1", "nope", {"x": 1})
        self.assertIn("no node with key 'nope'", str(ctx.exception))
        self.assertIn("mat-1", str(ctx.exception))


class ConnectPortsTests(unittest.TestCase):
    def test_material_wiring_adds_material_deps_and_depends_on(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.ops.connect_ports("wf-1", "mat-1", "generate-minimax-h3-2", input_port="reference_image")
        consumer = fx.store.workflows["wf-1"]["nodes"][1]
        self.assertEqual(consumer["config"]["material_deps"],
                         [{"key": "mat-1", "input_port": "reference_image"}])
        self.assertIn("mat-1", consumer["depends_on"])

    def test_material_wiring_dedupes_by_key_and_port(self):
        wf = workflow_with_material()
        wf["nodes"][1]["config"]["material_deps"] = [{"key": "mat-1", "input_port": "first_frame"}]
        fx = CanvasFixture({"wf-1": wf})
        fx.ops.connect_ports("wf-1", "mat-1", "generate-minimax-h3-2", input_port="first_frame")
        self.assertEqual(fx.store.workflows["wf-1"]["nodes"][1]["config"]["material_deps"],
                         [{"key": "mat-1", "input_port": "first_frame"}])

    def test_invalid_image_port_is_rejected_early(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        with self.assertRaises(ValueError) as ctx:
            fx.ops.connect_ports("wf-1", "mat-1", "generate-minimax-h3-2",
                                 input_port="middle_frame")
        self.assertIn("middle_frame", str(ctx.exception))
        self.assertIn("reference_image", str(ctx.exception))
        # refused before any PUT: only the lock pair and GET happened
        self.assertNotIn("PUT", [c["method"] for c in fx.transport.calls])

    def test_non_material_node_wires_as_plain_depends_on_only(self):
        wf = workflow_with_material()
        wf["nodes"].append({"type": "process", "provider": "script", "key": "s-1", "config": {}})
        fx = CanvasFixture({"wf-1": wf})
        fx.ops.connect_ports("wf-1", "s-1", "generate-minimax-h3-2")
        consumer = fx.store.workflows["wf-1"]["nodes"][1]
        self.assertIn("s-1", consumer["depends_on"])
        self.assertNotIn("material_deps", consumer["config"])


class LockTests(unittest.TestCase):
    def test_lock_409_raises_lock_held_error_with_holder_info(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.store.locked_by["wf-1"] = {"kind": "user-session", "id": "wes@web"}
        with self.assertRaises(LockHeldError) as ctx:
            fx.ops.create_node("wf-1", "process", "script")
        self.assertEqual(ctx.exception.held_by["holder"]["id"], "wes@web")
        self.assertIn("画板正被占用", str(ctx.exception))
        self.assertIn("wes@web", str(ctx.exception))
        # nothing was written and nothing needs unlocking (we never held it)
        self.assertNotIn("PUT", [c["method"] for c in fx.transport.calls])

    def test_locked_unlocks_on_success_and_on_exception(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        with fx.ops.locked("wf-1") as workflow:
            self.assertEqual(workflow["id"], "wf-1")
            self.assertIn("wf-1", fx.store.locked_by)
        self.assertNotIn("wf-1", fx.store.locked_by)          # unlocked on success

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with fx.ops.locked("wf-1"):
                raise Boom("PUT exploded")
        self.assertNotIn("wf-1", fx.store.locked_by)          # unlocked in finally

    def test_unlock_is_idempotent(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.ops.unlock_workflow("wf-1")   # never locked: still 200
        fx.ops.unlock_workflow("wf-1")


class QuerySchemaTests(unittest.TestCase):
    def test_port_analysis_lists_image_role_enums_and_modes(self):
        fx = CanvasFixture(nodes=[minimax_definition()])
        result = fx.ops.query_schema("generate", "minimax-h3")
        self.assertEqual(result["node_id"], "generate:minimax-h3")
        analysis = result["port_analysis"]
        roles = next(f for f in analysis["enum_fields"] if f["field"] == "image_roles")
        self.assertEqual(roles["enum"], ["reference_image", "first_frame", "last_frame", ""])
        self.assertIn("images/image_roles and video_refs are alternative input modes",
                      analysis["notes"])
        self.assertEqual(analysis["required"], ["prompt"])
        self.assertEqual(result["config_defaults"], {"duration": 6})

    def test_unknown_node_lists_available_ids(self):
        other = dict(minimax_definition(), id="generate:veo-4")
        fx = CanvasFixture(nodes=[other])
        with self.assertRaises(ValueError) as ctx:
            fx.ops.query_schema("generate", "minimax-h3")
        self.assertIn("generate:veo-4", str(ctx.exception))

    def test_exact_node_id_lookup(self):
        fx = CanvasFixture(nodes=[minimax_definition()])
        self.assertEqual(fx.ops.query_schema(node_id="generate:minimax-h3")["node_id"],
                         "generate:minimax-h3")


class ReadNodeOutputTests(unittest.TestCase):
    def test_newest_matching_job_node_wins(self):
        jobs = [
            {"id": "job-1", "workflow_id": "wf-1", "created_at": "2026-09-28T10:00:00Z",
             "status": "completed", "nodes": [{"key": "n-1", "status": "completed",
                                               "output": {"dest_video_url": "old.mp4"}}]},
            {"id": "job-2", "workflow_id": "wf-1", "created_at": "2026-09-28T11:00:00Z",
             "status": "completed", "nodes": [{"key": "n-1", "status": "completed",
                                               "output": {"dest_video_url": "new.mp4"}}]},
            {"id": "job-3", "workflow_id": "wf-2", "created_at": "2026-09-28T12:00:00Z",
             "status": "completed", "nodes": [{"key": "n-1", "status": "completed",
                                               "output": {"dest_video_url": "other.mp4"}}]},
        ]
        fx = CanvasFixture(jobs=jobs)
        result = fx.ops.read_node_output("wf-1", "n-1")
        self.assertTrue(result["found"])
        self.assertEqual(result["job_id"], "job-2")       # newest for THIS workflow
        self.assertEqual(result["node"]["output"]["dest_video_url"], "new.mp4")

    def test_keyless_single_node_job_matches_by_index(self):
        jobs = [{"id": "job-9", "workflow_id": "wf-1", "status": "running",
                 "nodes": [{"type": "generate", "config": {}}]}]
        fx = CanvasFixture(jobs=jobs)
        result = fx.ops.read_node_output("wf-1", "anything")
        self.assertTrue(result["found"])
        self.assertEqual(result["node"]["key"], "anything")

    def test_no_match_reports_not_found(self):
        fx = CanvasFixture(jobs=[])
        self.assertEqual(fx.ops.read_node_output("wf-1", "n-1"), {"found": False})


class SubmitJobTests(unittest.TestCase):
    def test_quote_is_called_before_the_jobs_endpoint(self):
        fx = CanvasFixture()
        result = fx.ops.submit_node_job("wf-1", "generate", "minimax-h3",
                                        {"prompt": "a cat"}, key="n-1")
        self.assertTrue(result["quoted"])
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["quote"]["total_estimate_usd"], 250000)
        self.assertEqual(result["quote"]["total_estimate_usd_display"], 0.25)
        self.assertEqual(result["quote"]["total_hold_display"], 0.3)
        order = [c["path"] for c in fx.transport.calls]
        self.assertLess(order.index("/api/v1/billing/quote"), order.index("/api/v1/jobs"))
        job_call = next(c for c in fx.transport.calls if c["path"] == "/api/v1/jobs")
        self.assertEqual(job_call["body"]["workflow_id"], "wf-1")
        self.assertEqual(job_call["body"]["nodes"][0]["key"], "n-1")

    def test_quote_first_false_skips_the_quote(self):
        fx = CanvasFixture()
        result = fx.ops.submit_node_job("wf-1", "generate", "minimax-h3", {"prompt": "x"},
                                        quote_first=False)
        self.assertFalse(result["quoted"])
        self.assertNotIn("quote", result)
        self.assertNotIn("/api/v1/billing/quote", [c["path"] for c in fx.transport.calls])

    def test_run_workflow_submits_every_node_verbatim(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        result = fx.ops.run_workflow("wf-1")
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["node_count"], 2)
        job_call = next(c for c in fx.transport.calls if c["path"] == "/api/v1/jobs")
        self.assertEqual(len(job_call["body"]["nodes"]), 2)
        self.assertEqual(job_call["body"]["nodes"][0]["key"], "mat-1")

    def test_run_empty_workflow_refuses(self):
        fx = CanvasFixture({"wf-1": {"id": "wf-1", "name": "x", "nodes": [], "media_pool": [],
                                     "updated_at": "t"}})
        with self.assertRaises(ValueError):
            fx.ops.run_workflow("wf-1")


class GenerateImageTests(unittest.TestCase):
    def test_submits_generate_node_always_with_quote_and_a_poll_note(self):
        fx = CanvasFixture()
        result = fx.ops.generate_image("wf-1", "a cat", model="gpt-image-2", size="512x512")
        self.assertTrue(result["quoted"])
        self.assertIn("poll", result["note"])
        job_call = next(c for c in fx.transport.calls if c["path"] == "/api/v1/jobs")
        node = job_call["body"]["nodes"][0]
        self.assertEqual((node["type"], node["provider"]),
                         ("generate", "gpt-image-2"))
        self.assertEqual(node["config"], {"prompt": "a cat", "size": "512x512"})

    def test_unknown_model_is_refused(self):
        fx = CanvasFixture()
        with self.assertRaises(ValueError):
            fx.ops.generate_image("wf-1", "a cat", model="dall-e-99")


class MediaPoolTests(unittest.TestCase):
    def test_add_media_posts_the_entry_and_finds_it_by_url(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        result = fx.ops.add_media("wf-1", "https://cdn.example/dog.png", "dog.png")
        post = next(c for c in fx.transport.calls
                    if c["path"] == "/api/v1/workflows/wf-1/media-pool")
        self.assertEqual(post["method"], "POST")
        self.assertEqual(post["body"], {"url": "https://cdn.example/dog.png", "name": "dog.png",
                                        "kind": "image", "mime_type": "image/png",
                                        "origin": {"kind": "upload"}})
        self.assertEqual(result["added"]["id"], "mp-2")
        self.assertEqual(len(result["entries"]), 2)

    def test_list_media_returns_the_pool_fields(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        media = fx.ops.list_media("wf-1")
        self.assertEqual(media["entries"],
                         [{"id": "mp-1", "name": "cat.png", "kind": "image",
                           "url": "https://cdn.example/cat.png", "thumb": "t.png",
                           "index": 1, "referenced_by": ["mat-1"]}])

    def test_list_media_marks_unreferenced_entries(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        # a second pool entry no material node points at: reference count 0
        fx.store.workflows["wf-1"]["media_pool"].append(
            {"id": "mp-2", "name": "dog.png", "kind": "image",
             "url": "https://cdn.example/dog.png", "thumb": "d.png"})
        media = fx.ops.list_media("wf-1")
        self.assertEqual(media["entries"][1]["index"], 2)
        self.assertEqual(media["entries"][1]["referenced_by"], [])


class MediaPoolLifecycleTests(unittest.TestCase):
    """S4 FR-5: rename / delete / reference counts on the board's pool."""

    def test_rename_sends_the_whole_pool_under_the_replace_header(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        result = fx.ops.rename_media("wf-1", "mp-1", "钟馗参考图")
        self.assertEqual(result["renamed"], {"id": "mp-1", "name": "钟馗参考图"})
        # the pool actually changed server-side
        self.assertEqual(fx.store.workflows["wf-1"]["media_pool"][0]["name"], "钟馗参考图")
        # the write carried the pool and stayed locked-scoped
        put = [c for c in fx.transport.calls if c["method"] == "PUT"][0]
        self.assertEqual(put["body"]["media_pool"][0]["name"], "钟馗参考图")
        paths = fx.transport.paths()
        self.assertIn(("POST", "/api/v1/workflows/wf-1/lock"), paths)
        self.assertIn(("POST", "/api/v1/workflows/wf-1/unlock"), paths)

    def test_rename_sends_pool_replace_header(self):
        # the transport records headers via the client's request log
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.ops.rename_media("wf-1", "mp-1", "new name")
        put_calls = [r for r in fx.client.requests if r["method"] == "PUT"]
        self.assertTrue(put_calls)
        self.assertEqual(put_calls[-1]["headers"].get("X-Beehive-Pool-Replace"), "1")

    def test_rename_unknown_entry_is_a_loud_error(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        with self.assertRaises(ValueError) as ctx:
            fx.ops.rename_media("wf-1", "mp-404", "x")
        self.assertIn("mp-404", str(ctx.exception))

    def test_rename_empty_name_is_refused(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        with self.assertRaises(ValueError):
            fx.ops.rename_media("wf-1", "mp-1", "   ")

    def test_delete_calls_the_dedicated_endpoint(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.store.workflows["wf-1"]["media_pool"].append(
            {"id": "mp-2", "name": "dog.png", "kind": "image",
             "url": "https://cdn.example/dog.png", "thumb": "d.png"})
        result = fx.ops.delete_media("wf-1", "mp-2")
        self.assertEqual(result["deleted"], "mp-2")
        self.assertIn(("DELETE", "/api/v1/workflows/wf-1/media-pool/mp-2"),
                      fx.transport.paths())


class PaidConfirmTests(unittest.TestCase):
    """S4 quote UX: the confirm gate inside the submission path."""

    def _fixture(self, confirm):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.ops.paid_confirm = confirm
        return fx

    def test_a_declined_confirm_blocks_the_submission(self):
        from canvas import PaidConfirmDeclined
        fx = self._fixture(lambda quote: False)
        with self.assertRaises(PaidConfirmDeclined) as ctx:
            fx.ops.submit_node_job("wf-1", "generate", "minimax-h3", {"prompt": "a cat"})
        self.assertIn("declined", str(ctx.exception))
        # NOTHING was submitted: no POST /api/v1/jobs beyond the quote call
        self.assertNotIn(("POST", "/api/v1/jobs"),
                         [(c["method"], c["path"]) for c in fx.transport.calls])

    def test_an_approved_confirm_submits(self):
        seen: list[dict] = []
        fx = self._fixture(lambda quote: seen.append(quote) or True)
        result = fx.ops.submit_node_job("wf-1", "generate", "minimax-h3", {"prompt": "a cat"})
        self.assertTrue(result["job_id"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["total_hold_display"], 0.3)

    def test_no_confirm_callback_keeps_the_s3_behaviour(self):
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        result = fx.ops.submit_node_job("wf-1", "generate", "minimax-h3", {"prompt": "a cat"})
        self.assertTrue(result["quoted"])
        self.assertTrue(result["job_id"])

    def test_a_zero_hold_quote_is_not_gated(self):
        calls: list[dict] = []
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.transport.quotes = [{"total_estimate_usd": 0, "total_hold": 0, "nodes": []}]
        fx.ops.paid_confirm = lambda quote: calls.append(quote) or False
        result = fx.ops.submit_node_job("wf-1", "generate", "minimax-h3", {"prompt": "free"})
        # free submissions go through without consulting the gate
        self.assertEqual(calls, [])
        self.assertTrue(result["job_id"])


class ScopeGateCanvasTests(unittest.TestCase):
    """A default (restricted) token now allows the canvas workflow routes --
    the regression counterpart of the old 'POST /api/v1/workflows is refused'
    assertion in test_platform.py."""

    def test_default_token_allows_canvas_read_and_write(self):
        token = RestrictedToken("ak", "sk")
        for method, path in (("GET", "/api/v1/workflows/wf-1"),
                             ("POST", "/api/v1/workflows"),
                             ("PUT", "/api/v1/workflows/wf-1"),
                             ("POST", "/api/v1/workflows/wf-1/lock"),
                             ("POST", "/api/v1/workflows/wf-1/unlock"),
                             ("POST", "/api/v1/workflows/wf-1/media-pool"),
                             ("DELETE", "/api/v1/workflows/wf-1")):
            allowed, why = token.allows(method, path)
            self.assertTrue(allowed, f"{method} {path}: {why}")

    def test_billing_stays_denied_for_a_default_token(self):
        token = RestrictedToken("ak", "sk")
        allowed, why = token.allows("GET", "/api/v1/billing/wallet")
        self.assertFalse(allowed)
        self.assertIn("never granted", why)

    def test_canvas_ops_passes_the_gate_end_to_end(self):
        # the full tool flow runs through the scope-gated client: if the rules
        # were wrong, the client would raise ScopeRefusal before any transport
        # call and this would not reach the fake.
        fx = CanvasFixture({"wf-1": workflow_with_material()})
        fx.ops.create_node("wf-1", "process", "script")
        fx.ops.list_media("wf-1")
        self.assertTrue(fx.transport.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
