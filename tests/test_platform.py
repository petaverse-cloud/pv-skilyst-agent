"""Platform-layer tests: scope gate, Beehive client, LLM client/router, configuration.

No test here touches the public internet: the Beehive client and the chat client
take injectable transports, and `verify_artifact` is exercised against a
loopback HTTP server serving a temp file.
"""
import hashlib
import hmac
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from beehive import (BeehiveClient, BeehiveError, DEFAULT_SCOPE, FORBIDDEN_SCOPES, RestrictedToken,  # noqa: E402
                     ScopeRefusal, artifact_url, client_from_bearer, job_handle, verify_artifact)
from config import ConfigError, read_env_file, resolve                                     # noqa: E402
from llm import ChatClient, LLMConfig, LLMError, ModelRouter                                # noqa: E402

MODEL = "test/model"
BASE = "https://beehive.invalid"


class FakeTransport:
    """Records (method, url, headers, body) and replays scripted (status, body) pairs."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(data) if data else None})
        if not self.responses:
            return 200, "{}"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScopeTests(unittest.TestCase):
    def test_default_scope_covers_jobs_and_assets_only(self):
        self.assertEqual(sorted(DEFAULT_SCOPE), ["assets:read", "jobs:read", "jobs:write"])

    def test_billing_and_admin_are_refused_for_every_method(self):
        token = RestrictedToken("ak", "sk")
        for method in ("GET", "POST", "PUT", "DELETE"):
            for path in ("/api/v1/admin/users", "/api/v1/billing/wallet",
                         "/api/v1/billing/history", "/api/v1/auth/api-keys"):
                allowed, why = token.allows(method, path)
                self.assertFalse(allowed, f"{method} {path}")
                self.assertIn("never granted", why)

    def test_unlisted_routes_are_refused_rather_than_allowed(self):
        token = RestrictedToken("ak", "sk")
        allowed, why = token.allows("POST", "/api/v1/workflows")
        self.assertFalse(allowed)
        self.assertIn("no scope rule", why)

    def test_forbidden_scopes_cannot_be_minted(self):
        for scope in FORBIDDEN_SCOPES:
            with self.assertRaises(ScopeRefusal):
                RestrictedToken("ak", "sk", scope=(scope,))

    def test_signature_matches_the_core_contract(self):
        token = RestrictedToken("AK123", "SK456")
        headers = token.sign(ts="1700000000")
        expected = hmac.new(b"SK456", b"AK1231700000000", hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Api-Signature"], expected)
        self.assertEqual(headers["X-Api-Key"], "AK123")
        self.assertEqual(headers["X-Api-Timestamp"], "1700000000")

    def test_bearer_client_is_still_gated(self):
        transport = FakeTransport()
        client = client_from_bearer(BASE, "jwt-token")
        client._transport = transport
        with self.assertRaises(ScopeRefusal):
            client.request("GET", "/api/v1/billing/wallet")
        self.assertEqual(transport.calls, [])          # refused before any HTTP


class BeehiveClientTests(unittest.TestCase):
    def _client(self, responses=None, **kwargs):
        transport = FakeTransport(responses)
        client = BeehiveClient(BASE, token=RestrictedToken("ak", "sk"), **kwargs)
        client._transport = transport
        return client, transport

    def test_submit_job_posts_the_node_plan(self):
        client, transport = self._client([(201, json.dumps({"payload": {"id": "job-9", "status": "queued"}}))])
        payload = client.submit_job([{"type": "generate", "provider": "minimax-h3",
                                      "config": {"prompt": "x", "duration": 15}}], workflow_id="wf-1")
        self.assertEqual(payload["id"], "job-9")
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], BASE + "/api/v1/jobs")
        self.assertEqual(call["body"]["workflow_id"], "wf-1")
        self.assertIn("X-Api-Signature", call["headers"])

    def test_http_error_becomes_a_loud_beehive_error(self):
        client, _ = self._client([(500, "boom")])
        with self.assertRaises(BeehiveError) as ctx:
            client.get_job("job-1")
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_wait_for_job_stops_at_a_terminal_status(self):
        running = json.dumps({"payload": {"id": "job-1", "status": "running", "progress": 40}})
        done = json.dumps({"payload": {"id": "job-1", "status": "completed",
                                       "output": {"dest_video_url": "https://cdn.example/v.mp4"}}})
        client, _ = self._client([(200, running), (200, done)])
        ticks = []
        final = client.wait_for_job("job-1", timeout_s=5, interval_s=0, on_tick=ticks.append)
        handle = job_handle(final)
        self.assertTrue(handle.succeeded)
        self.assertEqual(handle.artifact_url, "https://cdn.example/v.mp4")
        self.assertEqual(len(ticks), 2)

    def test_artifact_url_is_found_on_the_node_output_too(self):
        job = {"id": "job-1", "status": "completed", "nodes": [
            {"output": {"dest_image_url": "https://cdn.example/i.png"}}]}
        self.assertEqual(artifact_url(job), "https://cdn.example/i.png")
        self.assertIsNone(artifact_url({"id": "job-2", "status": "running"}))

    def test_registry_and_assets_payloads_are_unwrapped(self):
        client, _ = self._client([(200, json.dumps({"payload": {"nodes": [{"id": "generate:minimax-h3"}]}})),
                                  (200, json.dumps({"payload": {"assets": [{"id": "as-1"}]}}))])
        self.assertEqual(client.list_nodes()[0]["id"], "generate:minimax-h3")
        self.assertEqual(client.list_assets()[0]["id"], "as-1")


class ArtifactVerificationTests(unittest.TestCase):
    """A job reporting `completed` is not proof the file is downloadable."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "clip.mp4").write_bytes(b"x" * 2048)
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=root, **k)  # noqa: E731
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def test_reachable_artifact_reports_size(self):
        check = verify_artifact(f"{self.base}/clip.mp4")
        self.assertTrue(check.reachable)
        self.assertEqual(check.status, 200)
        self.assertEqual(check.content_length, 2048)

    def test_missing_artifact_is_reported_not_assumed(self):
        check = verify_artifact(f"{self.base}/nope.mp4")
        self.assertFalse(check.reachable)
        self.assertEqual(check.status, 404)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.saved = {k: os.environ.pop(k, None) for k in
                      ("SKILYST_ENV_FILE", "SKILYST_DEV_PROFILE", "SKILYST_LLM_BASE_URL",
                       "SKILYST_LLM_API_KEY", "SKILYST_LLM_MODEL", "SKILYST_LLM_CONFIG",
                       "SKILYST_LLM_FALLBACKS", "BEEHIVE_API", "BEEHIVE_PLATFORM_AK",
                       "BEEHIVE_PLATFORM_SK", "BEEHIVE_PLATFORM_USER", "BEEHIVE_PLATFORM_PASS",
                       "BEEHIVE_PLATFORM_UID")}
        self.addCleanup(self._restore)

    def _restore(self):
        for key, value in self.saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def _env_file(self, text: str) -> Path:
        path = self.root / "env"
        path.write_text(text)
        return path

    def test_env_file_parsing_ignores_comments_and_quotes(self):
        path = self._env_file('# comment\nBEEHIVE_PLATFORM_AK="AK1"\n\nBEEHIVE_API=https://x\n')
        values = read_env_file(path)
        self.assertEqual(values["BEEHIVE_PLATFORM_AK"], "AK1")
        self.assertEqual(values["BEEHIVE_API"], "https://x")

    def test_process_env_wins_over_the_env_file(self):
        path = self._env_file("BEEHIVE_PLATFORM_AK=fromfile\nBEEHIVE_PLATFORM_SK=sk\n"
                              "SKILYST_LLM_BASE_URL=https://llm.invalid/v1\nSKILYST_LLM_API_KEY=k\n"
                              "SKILYST_LLM_MODEL=m\n")
        os.environ["BEEHIVE_PLATFORM_AK"] = "fromenv"
        cfg = resolve(env_file=path, store_dir=self.root / "store")
        self.assertEqual(cfg.beehive.access_key, "fromenv")
        self.assertEqual(cfg.llm.base_url, "https://llm.invalid/v1")

    def test_missing_credentials_fail_loudly_with_the_paths_it_looked_at(self):
        with self.assertRaises(ConfigError) as ctx:
            resolve(env_file=self.root / "absent")
        message = str(ctx.exception)
        self.assertIn("BEEHIVE_PLATFORM_AK", message)
        self.assertIn("absent", message)

    def test_llm_settings_can_come_from_a_hermes_style_config(self):
        config = self.root / "config.yaml"
        config.write_text("model:\n  api_key: key-from-config\n  base_url: https://gateway.invalid/v1\n"
                          "  default: deepseek/deepseek-v4.1-flash\nfallback_providers:\n  - x\n")
        path = self._env_file("BEEHIVE_PLATFORM_AK=ak\nBEEHIVE_PLATFORM_SK=sk\n")
        cfg = resolve(env_file=path, llm_config=config)
        self.assertEqual(cfg.llm.model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(cfg.llm.api_key, "key-from-config")
        self.assertEqual(cfg.sources["llm_config"], str(config))

    def test_dev_profile_env_file_is_a_fallback_layer(self):
        profile = self.root / "profile"
        profile.mkdir()
        (profile / ".env").write_text("BEEHIVE_PLATFORM_AK=fromprofile\nBEEHIVE_PLATFORM_SK=sk\n"
                                      "BEEHIVE_API=https://dev.invalid\n")
        os.environ["SKILYST_DEV_PROFILE"] = str(profile)
        cfg = resolve(env_file=self.root / "absent", require_llm=False)
        self.assertEqual(cfg.beehive.access_key, "fromprofile")
        self.assertEqual(cfg.beehive.base_url, "https://dev.invalid")
        self.assertIn("dev profile", cfg.beehive.source)

    def test_redaction_never_prints_a_secret(self):
        path = self._env_file("BEEHIVE_PLATFORM_AK=AKSECRET123\nBEEHIVE_PLATFORM_SK=SKSECRET456\n"
                              "BEEHIVE_PLATFORM_USER=someone\nSKILYST_LLM_BASE_URL=https://llm.invalid/v1\n"
                              "SKILYST_LLM_API_KEY=LLMSECRET\nSKILYST_LLM_MODEL=m\n")
        cfg = resolve(env_file=path)
        dumped = json.dumps(cfg.redacted())
        for secret in ("SKSECRET456", "LLMSECRET", "AKSECRET123"):
            self.assertNotIn(secret, dumped)
        self.assertEqual(cfg.redacted()["llm"]["api_key"], "set")
        self.assertEqual(cfg.redacted()["beehive"]["secret_key"], "set")


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
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, body, headers, timeout):
        self.requests.append(body)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def router_with(script, fallbacks=()) -> ModelRouter:
    cfg = LLMConfig(base_url="https://llm.invalid/v1", api_key="k", model=MODEL, fallbacks=tuple(fallbacks))
    return ModelRouter(cfg, client_factory=lambda c: ChatClient(c, post=script))


class LLMTests(unittest.TestCase):
    def test_tools_are_declared_and_parsed(self):
        script = ScriptedChat([chat_response(tool_calls=[{"name": "read_skill",
                                                         "arguments": {"skill_id": "x"}}])])
        router = router_with(script)
        response = router.chat([{"role": "user", "content": "hi"}],
                               [{"type": "function", "function": {"name": "read_skill"}}])
        self.assertTrue(response.wants_tools)
        self.assertEqual(response.tool_calls[0].parsed_arguments(), {"skill_id": "x"})
        self.assertEqual(script.requests[0]["tool_choice"], "auto")

    def test_retryable_failure_falls_back_and_is_recorded(self):
        script = ScriptedChat([LLMError("LLM HTTP 503: upstream", status=503, retryable=True),
                               chat_response(content="answered by the fallback")])
        router = router_with(script, fallbacks=("fallback/model",))
        response = router.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(response.content, "answered by the fallback")
        self.assertEqual([a.model for a in router.trace], [MODEL, "fallback/model"])
        self.assertFalse(router.trace[0].ok)
        self.assertEqual(router.total_usage["models_used"], ["fallback/model"])

    def test_client_error_does_not_fall_back(self):
        script = ScriptedChat([LLMError("LLM HTTP 401: bad key", status=401)])
        router = router_with(script, fallbacks=("fallback/model",))
        with self.assertRaises(LLMError):
            router.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(router.trace), 1)

    def test_streaming_accumulates_content_and_tool_calls(self):
        frames = [
            'data: {"model": "test/model", "choices": [{"delta": {"content": "Hel"}, "finish_reason": null}]}',
            'data: {"choices": [{"delta": {"content": "lo"}}]}',
            'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "list_skills", "arguments": "{}"}}]}}]}',
            'data: {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"total_tokens": 7}}',
            "data: [DONE]",
        ]
        seen = []
        client = ChatClient(LLMConfig(base_url="https://llm.invalid/v1", api_key="k", model=MODEL),
                            post_stream=lambda url, body, headers, timeout: iter(frames))
        response = client.chat_stream([{"role": "user", "content": "hi"}], [], on_delta=seen.append)
        self.assertEqual(response.content, "Hello")
        self.assertEqual(seen, ["Hel", "lo"])
        self.assertEqual(response.tool_calls[0].name, "list_skills")
        self.assertEqual(response.tool_calls[0].id, "c1")
        self.assertEqual(response.usage["total_tokens"], 7)
        self.assertEqual(response.finish_reason, "tool_calls")

    def test_a_partial_stream_is_never_retried_into_another_model(self):
        frames = ['data: {"choices": [{"delta": {"content": "half"}}]}']
        calls = {"n": 0}

        def stream(url, body, headers, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                def gen():
                    yield from frames
                    raise LLMError("connection reset", retryable=True)
                return gen()
            return iter(['data: {"choices": [{"delta": {"content": "other model"}}]}'])

        cfg = LLMConfig(base_url="https://llm.invalid/v1", api_key="k", model=MODEL,
                        fallbacks=("fallback/model",))
        router = ModelRouter(cfg, client_factory=lambda c: ChatClient(c, post_stream=stream))
        emitted = []
        with self.assertRaises(LLMError):
            router.chat([{"role": "user", "content": "hi"}], stream=True, on_delta=emitted.append)
        self.assertEqual(emitted, ["half"])
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
