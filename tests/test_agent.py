"""Agent-layer tests: session store, progressive-disclosure prompt, tool registry, the loop.

The loop is exercised with a scripted chat transport (no network) and a real
registry/session on disk, so the assertions cover the parts that actually ship:
what the model is told, what it may call, what is persisted, and how failures
surface.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent import AgentLoop, LoopConfig, PromptContext, build_registry, build_system_prompt  # noqa: E402
from agent.prompt import skill_index                                                      # noqa: E402
from beehive import ScopeRefusal                                         # noqa: E402
from llm import ChatClient, LLMConfig, LLMError, ModelRouter                              # noqa: E402
from sandbox import PermissionGate, SandboxViolation                                      # noqa: E402
from session import SessionStore                                                          # noqa: E402
from skills import SkillStore, content_digest, load_package                               # noqa: E402

OFFICIAL = ROOT / "skills" / "official" / "video-15s"
BUNDLE = ROOT / "skills" / "official"
MODEL = "test/model"


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


class ScriptedTransport:
    """Returns scripted chat payloads; records the request bodies it was given."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, body, headers, timeout):
        self.requests.append(body)
        if not self.responses:
            raise AssertionError("the loop made more LLM calls than the script provides")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def router_with(script: ScriptedTransport, fallbacks=()) -> ModelRouter:
    cfg = LLMConfig(base_url="https://llm.invalid/v1", api_key="test-key", model=MODEL,
                    fallbacks=tuple(fallbacks))
    return ModelRouter(cfg, client_factory=lambda c: ChatClient(c, post=script))


def make_skill(directory: Path, **manifest_overrides) -> Path:
    """A published (sidecar-carrying) skill package with overridable manifest fields.

    The package name always follows the directory name: our own packages are held
    to the spec's equality rule (community packages are not).
    """
    directory.mkdir(parents=True, exist_ok=True)
    name = directory.name
    description = "A test skill for the agent loop."
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nlicense: MIT\n---\n\n"
        "# Test skill\n\nCall the platform tool once, then report the URL verbatim.\n")
    manifest = json.loads((OFFICIAL / "manifest.json").read_text())
    manifest["skill_id"] = f"test/{name}"
    manifest["description"] = description
    manifest.update(manifest_overrides)
    manifest["content_digest"] = "sha256:" + "0" * 64
    (directory / "manifest.json").write_text(json.dumps(manifest))
    manifest["content_digest"] = content_digest(directory)   # digest excludes the sidecar: stable
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


class Fixture:
    """A store with the official bundle installed + a session + a prompt context."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = SkillStore(self.root / "store")
        self.store.preload_official_bundle(BUNDLE)
        self.sessions = SessionStore(self.root / "sessions")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def cleanup(self):
        self._tmp.cleanup()

    def package(self, skill_id="skilyst/video-15s"):
        return self.store.get(skill_id)

    def gate(self, package):
        return PermissionGate(package.dir, package.permission, workspace=self.workspace)

    def registry(self, package=None, client=None, dry_run=False):
        return build_registry(self.store, client, package, self.gate(package) if package else None,
                              self.workspace, dry_run=dry_run, artifact_verifier=self.verify_artifact)

    @staticmethod
    def verify_artifact(url):
        from beehive import ArtifactCheck
        return ArtifactCheck(url=url, reachable=True, status=200, content_type="video/mp4",
                             content_length=1024, detail="HTTP 200, 1024 bytes (stubbed)")

    def session(self, title="test"):
        return self.sessions.create(title=title, model=MODEL, workspace=str(self.workspace))

    def prompt_ctx(self, package=None):
        return PromptContext(skills=self.store.list(), active_skill=package,
                             workspace=str(self.workspace), model=MODEL,
                             platform="https://beehive-api.verse4.pet")


class StubBeehiveClient:
    """Stands in for the scope-gated client: records calls, never touches the network."""

    def __init__(self, submit=None, job=None, nodes=None):
        self.calls = []
        self._submit = submit or {"id": "job-1", "status": "queued"}
        self._job = job or {"id": "job-1", "status": "completed",
                            "output": {"dest_video_url": "https://cdn.example/clip.mp4"}}
        self._nodes = nodes or [{"id": "generate:minimax-h3", "node_type": "generate", "enabled": True}]

    def request(self, method, path, body=None, raw=False):
        self.calls.append((method, path))
        if path == "/api/v1/nodes":
            return 200, {"payload": {"nodes": self._nodes}}
        return 200, {"payload": {}}

    def submit_job(self, nodes, workflow_id=""):
        self.calls.append(("POST", "/api/v1/jobs", nodes, workflow_id))
        return self._submit

    def get_job(self, job_id):
        self.calls.append(("GET", f"/api/v1/jobs/{job_id}"))
        return self._job

    def wait_for_job(self, job_id, timeout_s=900, interval_s=10, on_tick=None):
        self.calls.append(("WAIT", f"/api/v1/jobs/{job_id}"))
        if on_tick:
            on_tick(self._job)
        return self._job

    def list_assets(self, limit=20):
        self.calls.append(("GET", "/api/v1/assets"))
        return [{"id": "as-1", "url": "https://cdn.example/as-1.mp4"}]


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_transcript_is_append_only_and_resumable(self):
        session = self.fx.session("first")
        session.append_message("user", "hello")
        session.append_message("assistant", "hi", tool_calls=[{"id": "c1", "type": "function",
                                                              "function": {"name": "list_skills",
                                                                           "arguments": "{}"}}])
        session.append_message("tool", '{"ok": true}', tool_call_id="c1", name="list_skills")
        reopened = self.fx.sessions.open(session.session_id)
        history = reopened.history()
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "tool"])
        self.assertEqual(history[2]["tool_call_id"], "c1")
        self.assertEqual(reopened.meta["message_count"], 3)
        self.assertNotIn("seq", history[0])            # internal bookkeeping is not sent to the model

    def test_artifacts_and_usage_accumulate(self):
        session = self.fx.session()
        session.record_artifact({"artifact_url": "https://cdn.example/a.mp4", "job_id": "job-1"})
        session.add_usage({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "calls": 1})
        session.add_usage({"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6, "calls": 1})
        reopened = self.fx.sessions.open(session.session_id)
        self.assertEqual(reopened.artifacts[0]["job_id"], "job-1")
        self.assertEqual(reopened.meta["usage"]["total_tokens"], 18)
        self.assertEqual(reopened.meta["usage"]["calls"], 2)
        self.assertEqual(self.fx.sessions.list()[0]["session_id"], session.session_id)

    def test_sessions_list_reports_artifacts_and_usage(self):
        session = self.fx.session("listed")
        session.record_artifact({"artifact_url": "https://cdn.example/a.mp4"})
        session.add_usage({"total_tokens": 12, "calls": 1})
        row = [r for r in self.fx.sessions.list() if r["session_id"] == session.session_id][0]
        self.assertEqual(row["artifacts"], 1)
        self.assertEqual(row["usage"]["total_tokens"], 12)

    def test_torn_final_line_does_not_lose_the_transcript(self):
        session = self.fx.session()
        session.append_message("user", "kept")
        with (session.path / "messages.jsonl").open("a") as fh:
            fh.write('{"role": "assistant", "content": "tru')
        self.assertEqual([m["content"] for m in self.fx.sessions.open(session.session_id).history()],
                         ["kept"])


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_index_carries_descriptions_only(self):
        index = skill_index(self.fx.store.list())
        self.assertIn(f"skilyst/video-15s@{self.fx.package().version}", index)
        self.assertIn("Produce a single 15-second vertical short video", index)
        self.assertNotIn("Prompt skeleton", index)          # the body stays out of context

    def test_active_skill_block_declares_sandbox_and_plan(self):
        package = self.fx.package()
        prompt = build_system_prompt(self.fx.prompt_ctx(package))
        self.assertIn(f"Active skill: skilyst/video-15s@{package.version}", prompt)
        self.assertIn("generate:minimax-h3", prompt)
        self.assertIn("'minimax-h3'", prompt.replace('"', "'"))
        self.assertIn("secrets=True", prompt)
        self.assertIn("billing", prompt)                    # the no-money rule is always present

    def test_without_active_skill_platform_tools_are_declared_off(self):
        prompt = build_system_prompt(self.fx.prompt_ctx(None))
        self.assertIn("No skill is active", prompt)


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def test_platform_tools_require_an_active_skill(self):
        without = self.fx.registry(None)
        self.assertEqual(without.names, ["list_skill_files", "list_skills", "read_skill",
                                        "read_skill_file", "write_workspace_file"])
        with_skill = self.fx.registry(self.fx.package(), StubBeehiveClient())
        self.assertIn("beehive_submit_job", with_skill.names)
        self.assertIn("beehive_get_job", with_skill.names)

    def test_skill_without_secrets_permission_gets_no_platform_tools(self):
        directory = make_skill(self.fx.root / "no-secrets",
                               permission={"egress": "none", "filesystem": "skill-dir",
                                           "exec": "none", "secrets": False})
        package = load_package(directory)
        registry = self.fx.registry(package, StubBeehiveClient())
        self.assertNotIn("beehive_submit_job", registry.names)

    def test_read_skill_returns_instructions_and_node_requirements(self):
        registry = self.fx.registry(self.fx.package(), StubBeehiveClient())
        result = registry.call("read_skill", {"skill_id": "skilyst/video-15s"})
        self.assertIn("Prompt skeleton", result["instructions"])
        self.assertEqual(result["requires_nodes"][0]["node_id"], "generate:minimax-h3")
        # layer-3 index: what the model can pull on demand, not just the two contract files
        self.assertIn("SKILL.md", result["files"])
        self.assertIn("references/i18n/glossary.en.json", result["files"])

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(KeyError):
            self.fx.registry(None).call("beehive_submit_job", {})

    def test_workspace_write_is_sandboxed(self):
        package = self.fx.package()
        registry = self.fx.registry(package, StubBeehiveClient())
        written = registry.call("write_workspace_file", {"path": "notes/shot.md", "content": "x"})
        self.assertTrue(Path(written["written"]).is_file())
        with self.assertRaises(SandboxViolation):
            registry.call("write_workspace_file", {"path": "/tmp/escape.md", "content": "x"})

    def test_submit_job_derives_the_node_from_the_manifest(self):
        client = StubBeehiveClient()
        registry = self.fx.registry(self.fx.package(), client)
        result = registry.call("beehive_submit_job", {"prompt": "a lighthouse at dawn"})
        self.assertEqual(result["job_id"], "job-1")
        node = client.calls[-1][2][0]
        self.assertEqual(node["type"], "generate")
        self.assertEqual(node["provider"], "minimax-h3")          # variant of generate:minimax-h3
        self.assertEqual(node["config"]["duration"], 15)
        self.assertEqual(node["config"]["resolution"], "768P")

    def test_submit_job_refuses_an_undeclared_node(self):
        registry = self.fx.registry(self.fx.package(), StubBeehiveClient())
        with self.assertRaises(ValueError):
            registry.call("beehive_submit_job", {"prompt": "x", "node_id": "generate:drawnow"})

    def test_dry_run_never_submits(self):
        client = StubBeehiveClient()
        registry = self.fx.registry(self.fx.package(), client, dry_run=True)
        result = registry.call("beehive_submit_job", {"prompt": "x"})
        self.assertTrue(result["dry_run"])
        self.assertEqual(client.calls, [])

    def test_job_budget_stops_a_second_paid_submission(self):
        client = StubBeehiveClient()
        registry = self.fx.registry(self.fx.package(), client)
        registry.call("beehive_submit_job", {"prompt": "first"})
        with self.assertRaises(RuntimeError) as ctx:
            registry.call("beehive_submit_job", {"prompt": "second"})
        self.assertIn("budget", str(ctx.exception))
        self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 1)

    def test_job_budget_can_be_raised_explicitly(self):
        client = StubBeehiveClient()
        registry = build_registry(self.fx.store, client, self.fx.package(),
                                  self.fx.gate(self.fx.package()), self.fx.workspace,
                                  artifact_verifier=self.fx.verify_artifact, max_jobs=2)
        registry.call("beehive_submit_job", {"prompt": "first"})
        registry.call("beehive_submit_job", {"prompt": "second"})
        self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 2)


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)

    def _loop(self, script, package=None, registry=None, max_turns=4):
        session = self.fx.session()
        loop = AgentLoop(router_with(script), registry or self.fx.registry(None), session,
                         self.fx.prompt_ctx(package), LoopConfig(max_turns=max_turns, stream=False))
        return loop, session

    def test_usage_is_recorded_per_llm_call(self):
        script = ScriptedTransport([chat_response(content="done")])
        loop, session = self._loop(script)
        loop.run("hi")
        usage = self.fx.sessions.open(session.session_id).meta["usage"]
        self.assertEqual(usage["calls"], 1)
        self.assertEqual(usage["models_used"], [MODEL])
        self.assertEqual(usage["total_tokens"], 15)

    def test_tool_call_then_answer(self):
        script = ScriptedTransport([
            chat_response(tool_calls=[{"name": "list_skills", "arguments": {}}]),
            chat_response(content="There is one skill installed.")])
        loop, session = self._loop(script)
        result = loop.run("what skills do you have?")
        self.assertTrue(result.ok)
        self.assertEqual(result.answer, "There is one skill installed.")
        self.assertEqual(result.turns, 2)
        self.assertEqual(result.tool_calls[0].name, "list_skills")
        self.assertIsNone(result.tool_calls[0].error)
        self.assertEqual([m["role"] for m in session.history()],
                         ["user", "assistant", "tool", "assistant"])
        self.assertEqual(result.usage["total_tokens"], 30)
        self.assertEqual(len(session.trace), 3)          # 2 llm turns + 1 tool call

    def test_the_second_llm_call_sees_the_tool_result(self):
        script = ScriptedTransport([
            chat_response(tool_calls=[{"name": "list_skills", "arguments": {}}]),
            chat_response(content="done")])
        loop, _ = self._loop(script)
        loop.run("go")
        tool_message = [m for m in script.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("skilyst/video-15s", tool_message["content"])

    def test_tool_refusal_is_reported_to_the_model_not_swallowed(self):
        script = ScriptedTransport([
            chat_response(tool_calls=[{"name": "beehive_submit_job", "arguments": {"prompt": "x"}}]),
            chat_response(content="I cannot reach the platform in this session.")])
        loop, session = self._loop(script)
        result = loop.run("make a video")
        self.assertTrue(result.ok)
        self.assertIn("KeyError", result.tool_calls[0].error)          # unknown tool here: no skill active
        tool_message = [m for m in script.requests[1]["messages"] if m["role"] == "tool"][0]
        self.assertIn("error", tool_message["content"])

    def test_scope_refusal_reaches_the_model(self):
        class RefusingClient(StubBeehiveClient):
            def submit_job(self, nodes, workflow_id=""):
                raise ScopeRefusal("POST /api/v1/jobs refused client-side: scope jobs:write not granted")

        script = ScriptedTransport([
            chat_response(tool_calls=[{"name": "beehive_submit_job", "arguments": {"prompt": "x"}}]),
            chat_response(content="The runtime refused that call.")])
        registry = self.fx.registry(self.fx.package(), RefusingClient())
        loop, session = self._loop(script, self.fx.package(), registry)
        result = loop.run("make a video")
        self.assertIn("ScopeRefusal", result.tool_calls[0].error)
        self.assertIn("refused", result.answer)

    def test_artifact_is_recorded_on_the_session(self):
        script = ScriptedTransport([
            chat_response(tool_calls=[{"name": "beehive_submit_job",
                                       "arguments": {"prompt": "x", "wait": True}}]),
            chat_response(content="https://cdn.example/clip.mp4")])
        registry = self.fx.registry(self.fx.package(), StubBeehiveClient())
        loop, session = self._loop(script, self.fx.package(), registry)
        result = loop.run("make a video")
        self.assertEqual(result.artifact_urls(), ["https://cdn.example/clip.mp4"])
        self.assertTrue(result.tool_calls[0].result["artifact_verified"])
        self.assertEqual(session.artifacts[0]["artifact_url"], "https://cdn.example/clip.mp4")

    def test_max_turns_is_not_reported_as_success(self):
        script = ScriptedTransport([chat_response(tool_calls=[{"name": "list_skills", "arguments": {}}])] * 3)
        loop, session = self._loop(script, max_turns=3)
        result = loop.run("loop forever")
        self.assertFalse(result.ok)
        self.assertEqual(result.stop_reason, "max_turns")
        self.assertEqual(result.model, MODEL)          # the model that was used is still reported
        self.assertEqual(session.meta["status"], "incomplete")

    def test_llm_failure_is_not_reported_as_success(self):
        script = ScriptedTransport([LLMError("LLM HTTP 400: bad request", status=400)])
        loop, session = self._loop(script)
        result = loop.run("anything")
        self.assertEqual(result.stop_reason, "llm_error")
        self.assertIn("400", result.error)
        self.assertEqual(session.meta["status"], "error")

    def test_malformed_tool_arguments_are_reported(self):
        bad = {"model": MODEL, "choices": [{"message": {"role": "assistant", "content": "",
               "tool_calls": [{"id": "c1", "type": "function",
                               "function": {"name": "list_skills", "arguments": "{not json"}}]},
               "finish_reason": "tool_calls"}], "usage": {}}
        script = ScriptedTransport([bad, chat_response(content="recovered")])
        loop, _ = self._loop(script)
        result = loop.run("go")
        self.assertTrue(result.ok)
        self.assertIn("not valid JSON", result.tool_calls[0].error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
