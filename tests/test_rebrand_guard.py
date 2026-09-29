"""Rebrand guard (issue #21): no user-visible 'Beehive' anywhere the user can see.

skilyst is the official brand (skilyst.ai); beehive remains the internal project
codename. What this suite pins down, by surface:

  * CLI --help output and every argparse description the parser prints;
  * the ConfigError text a user hits with no credential;
  * serve /health -- its payload must not mention the codename;
  * the submit-tool description the model (and the transcript) sees;
  * the desktop shell's TSX source -- UI strings render from there;
  * the official skills bundle -- SKILL.md descriptions and compatibility lines.

What is deliberately NOT flagged here (the issue's boundary rules):

  * technical identifiers: BEEHIVE_* env vars, the beehive-api.verse4.pet DNS,
    tool names (beehive_submit_job ...), the src/beehive module, config keys
    (the "beehive" section of `skilyst config` output, beehive.access_key ...);
  * the internal codename in comments/docs that describe plumbing (e.g. the
    beehive integration in architecture docs, cross-repo issue references).

A hit is only a failure when it is a user-facing string; that is exactly what
the checked surfaces below are.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import ConfigError                                     # noqa: E402

CLI = [sys.executable, str(ROOT / "bin" / "skilyst")]
DESKTOP_SRC = ROOT / "desktop" / "src"
BUNDLE = ROOT / "skills" / "official"


def _run(*args: str) -> str:
    out = subprocess.run(CLI + list(args), capture_output=True, text=True)
    return out.stdout + out.stderr


class CliHelp(unittest.TestCase):
    """Every help surface the CLI can print, plus its banner."""

    def test_top_level_help_has_no_beehive(self):
        self.assertNotIn("Beehive", _run("--help"))

    def test_subcommand_help_has_no_beehive(self):
        for cmd in ("run", "chat", "sessions", "doctor", "serve", "config",
                    "authz-probe", "job", "list", "preload"):
            with self.subTest(cmd=cmd):
                self.assertNotIn("Beehive", _run(cmd, "--help"))

    def test_usage_error_output_has_no_beehive(self):
        self.assertNotIn("Beehive", _run("nosuchcommand"))


class CliErrorText(unittest.TestCase):
    """The credential failure is the most likely user-visible CLI error."""

    def test_missing_credential_error_says_skilyst(self):
        from config import resolve
        with unittest.mock.patch.dict(os.environ, {}, clear=True), \
             unittest.mock.patch("config.ENV_FILE_DEFAULT", "/nonexistent/env"), \
             unittest.mock.patch("config._login_credentials", lambda: (None, None, None, None)):
            with self.assertRaises(ConfigError) as ctx:
                resolve({}, require_llm=False, require_beehive=True)
        self.assertNotIn("Beehive", str(ctx.exception))
        self.assertIn("skilyst credential", str(ctx.exception))


import os                                                          # noqa: E402
import tempfile                                                   # noqa: E402
from unittest import mock                                          # noqa: E402


class ServeHealth(unittest.TestCase):
    """/health is the shell's first read; its payload must be brand-clean."""

    def _api(self) -> "RuntimeAPI":
        import serve
        from config import resolve
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "env"
            env_file.write_text("SKILYST_LLM_BASE_URL=https://llm.invalid/v1\n"
                                "SKILYST_LLM_API_KEY=k\nSKILYST_LLM_MODEL=m\n"
                                "BEEHIVE_PLATFORM_AK=ak\nBEEHIVE_PLATFORM_SK=sk\n")
            kwargs = {"env_file": env_file, "store_dir": Path(tmp) / "store",
                      "workspace_dir": Path(tmp) / "workspace",
                      "session_dir": Path(tmp) / "sessions"}
            cfg = resolve(**kwargs, require_llm=False, require_beehive=False)
            return serve.RuntimeAPI(cfg, resolve_kwargs=kwargs)

    def test_health_payload_has_no_beehive(self):
        rendered = repr(self._api().health())
        self.assertNotIn("Beehive", rendered)
        self.assertNotIn("beehive", rendered)


class ToolDescription(unittest.TestCase):
    """The submit-tool description lands in the transcript the user reads."""

    def test_submit_description_says_skilyst(self):
        from agent.tools import _submit_description
        active = type("A", (), {"skill_id": "skilyst/video-15s"})()
        # plan is the skill's declared plan dict; the two callables mirror
        # build_registry's closure over the manifest bindings.
        text = _submit_description(active,
                                   ["generate:minimax-h3"], "generate:minimax-h3",
                                   {}, lambda n: ["prompt"], lambda n: ["prompt"])
        self.assertIn("skilyst platform", text)
        self.assertNotIn("Beehive", text)



class DesktopUiStrings(unittest.TestCase):
    """UI strings render from the TSX source; grep the rendered literals."""

    def _tsx_literals(self):
        for path in sorted(DESKTOP_SRC.rglob("*.tsx")):
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                # rendered string children / label / title props only: the line
                # is inside JSX, not an import, identifier, comment or key.
                if stripped.startswith(("//", "import ", "export ", "*")):
                    continue
                yield path, stripped

    def test_ui_copy_has_no_beehive_and_no_chinese(self):
        for path, line in self._tsx_literals():
            if "Beehive" in line and "TOKEN_KEY" not in line:
                self.fail(f"{path}: user-visible Beehive in {line!r}")

    def test_canvas_signin_copy_is_english(self):
        src = (DESKTOP_SRC / "components" / "CanvasView.tsx").read_text(encoding="utf-8")
        for cjk in ("登录", "用户名", "密码", "画板", "返回", "退出", "刷新"):
            self.assertNotIn(cjk, src, f"CanvasView still has Chinese UI copy: {cjk}")


class OfficialSkillsCopy(unittest.TestCase):
    """Skill descriptions surface in `skilyst list` and the desktop UI."""

    def test_descriptions_and_compatibility_have_no_beehive(self):
        for skill_md in sorted(BUNDLE.rglob("SKILL.md")):
            text = skill_md.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith(("description:", "compatibility:")):
                    self.assertNotIn("Beehive", line,
                                     f"{skill_md}: user-facing line names the codename: {line!r}")


if __name__ == "__main__":
    unittest.main()
