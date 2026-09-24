"""Runtime configuration: where credentials and paths come from, and how they fail.

Credential policy (no secret ever lives in the repository):

  1. process environment (``SKILYST_*`` / ``BEEHIVE_*``) wins;
  2. then the local runtime env file -- ``$SKILYST_ENV_FILE`` or ``~/.skilyst/env``
     -- which the installer creates with mode 0600 and which is never committed;
  3. then, in development only, the profile env file pointed at by
     ``$SKILYST_DEV_PROFILE`` (e.g. a Hermes agent profile directory).

Nothing is guessed and nothing degrades silently: a missing credential raises
``ConfigError`` naming the exact variable and file it looked at.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from llm import DEFAULT_BASE_URL, DEFAULT_MODEL, LLMConfig
from llm.router import FALLBACK_MODELS

ENV_FILE_DEFAULT = "~/.skilyst/env"
HOME_ROOT = "~/.skilyst"

BEEHIVE_KEYS = ("BEEHIVE_PLATFORM_AK", "BEEHIVE_PLATFORM_SK", "BEEHIVE_PLATFORM_USER",
                "BEEHIVE_PLATFORM_PASS", "BEEHIVE_PLATFORM_UID", "BEEHIVE_API")
LLM_KEYS = ("SKILYST_LLM_BASE_URL", "SKILYST_LLM_API_KEY", "SKILYST_LLM_MODEL", "SKILYST_LLM_FALLBACKS")

# Paid-job budget per run (`beehive_submit_job` calls that spend money).
#
# The budget exists because a submitted job cannot be un-submitted: one shot is the
# only safe default for an unattended `run`, while an interactive session is where a
# multi-shot request actually arrives ("cut me three beats of this"), so it gets a
# small working budget instead of a refusal the user cannot fix from the GUI.
# `SKILYST_MAX_JOBS` overrides both.
MAX_JOBS_ENV = "SKILYST_MAX_JOBS"
DEFAULT_MAX_JOBS_ONE_SHOT = 1
DEFAULT_MAX_JOBS_INTERACTIVE = 3


class ConfigError(RuntimeError):
    """Configuration is incomplete -- say exactly what is missing, then stop."""


@dataclass
class BeehiveCredentials:
    base_url: str = "https://beehive-api.verse4.pet"
    access_key: str = ""
    secret_key: str = ""
    user: str = ""
    password: str = ""
    uid: str = ""
    source: str = ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.access_key and self.secret_key)

    @property
    def has_password(self) -> bool:
        return bool(self.user and self.password)

    def redacted(self) -> dict:
        return {"base_url": self.base_url, "account": self.user or "-",
                "uid": self.uid or "-",
                "access_key": f"{self.access_key[:6]}…" if self.access_key else "-",
                "secret_key": "set" if self.secret_key else "-",
                "password": "set" if self.password else "-", "source": self.source}


@dataclass
class RuntimeConfig:
    beehive: BeehiveCredentials
    llm: LLMConfig
    store_dir: Path
    workspace_dir: Path
    session_dir: Path
    env_file: Path | None = None
    sources: dict = field(default_factory=dict)
    # Operator-set paid-job budget; None means "use the mode default"
    # (DEFAULT_MAX_JOBS_ONE_SHOT for `run`/`job`, DEFAULT_MAX_JOBS_INTERACTIVE for a
    # chat/serve session). A run may always lower it via --max-jobs.
    max_jobs: int | None = None

    def job_budget(self, interactive: bool = False) -> int:
        """The budget this run gets, and why (the *why* is reported to the operator)."""
        if self.max_jobs is not None:
            return self.max_jobs
        return DEFAULT_MAX_JOBS_INTERACTIVE if interactive else DEFAULT_MAX_JOBS_ONE_SHOT

    def redacted(self) -> dict:
        return {"beehive": self.beehive.redacted(),
                "llm": {"base_url": self.llm.base_url, "model": self.llm.model,
                        "fallbacks": list(self.llm.fallbacks),
                        "api_key": "set" if self.llm.api_key else "-"},
                "paths": {"store": str(self.store_dir), "workspace": str(self.workspace_dir),
                          "sessions": str(self.session_dir)},
                "limits": {"job_budget": {"configured": self.max_jobs,
                                          "source": MAX_JOBS_ENV if self.max_jobs is not None
                                          else "mode default",
                                          "one_shot_default": DEFAULT_MAX_JOBS_ONE_SHOT,
                                          "interactive_default": DEFAULT_MAX_JOBS_INTERACTIVE}},
                "env_file": str(self.env_file) if self.env_file else None,
                "sources": self.sources}


def read_env_file(path: str | Path) -> dict:
    """Read a KEY=VALUE file; comments, blank lines and quotes tolerated."""
    out: dict = {}
    text = Path(path).expanduser().read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _dev_profile_dir() -> Path | None:
    value = os.environ.get("SKILYST_DEV_PROFILE", "").strip()
    return Path(value).expanduser() if value else None


def _layered_env(env_file: Path | None) -> tuple[dict, dict]:
    """Environment -> runtime env file -> dev profile env file (later never overrides earlier)."""
    layers: list[tuple[str, dict]] = []
    if env_file and Path(env_file).expanduser().is_file():
        layers.append((str(env_file), read_env_file(env_file)))
    profile = _dev_profile_dir()
    if profile and (profile / ".env").is_file():
        layers.append((f"{profile}/.env (dev profile)", read_env_file(profile / ".env")))
    merged: dict = {}
    for name, values in reversed(layers):          # nearest layer wins
        merged.update(values)
    merged.update({k: v for k, v in os.environ.items() if v})   # process env always wins
    sources = {"env_file": layers[0][0] if layers else None,
               "dev_profile": str(profile) if profile else None,
               "process_env": sorted(k for k in os.environ if k.startswith(("SKILYST_", "BEEHIVE_")))}
    return merged, sources


def _read_hermes_model_config(path: Path) -> tuple[str, str, str]:
    """Read base_url / api_key / default model out of a Hermes profile config.yaml."""
    text = Path(path).expanduser().read_text(encoding="utf-8")
    block = re.search(r"^model:\n((?:[ \t]+.*\n?)*)", text, re.M)
    if not block:
        raise ConfigError(f"{path} has no `model:` block")
    section = block.group(1)

    def field(name: str) -> str:
        m = re.search(rf"^[ \t]+{name}:[ \t]*(\S+)[ \t]*$", section, re.M)
        return m.group(1) if m else ""

    base, key, model = field("base_url"), field("api_key"), field("default")
    if not (base and key and model):
        raise ConfigError(f"{path} `model:` block is missing base_url/api_key/default")
    return base, key, model


def _login_credentials() -> tuple[str, str, str, str]:
    """Read the logged-in AgentScopes AK/SK from the auth TokenStore.

    Returns (access_key, secret_key, account_uid, account_name); all empty
    when not logged in. Never raises — an unreadable store means "not logged
    in", and the dev fallback below carries the error story.
    """
    try:
        from auth import AuthFlow
        rec = AuthFlow().current()
    except Exception:  # noqa: BLE001 — auth is an optional layer here
        return "", "", "", ""
    if rec is None:
        return "", "", "", ""
    return rec.access_key, rec.secret_key, rec.account_uid, rec.account_name


def resolve(env_file: str | Path | None = None, *, store_dir: str | Path | None = None,
            workspace_dir: str | Path | None = None, session_dir: str | Path | None = None,
            llm_config: str | Path | None = None, model: str | None = None,
            require_llm: bool = True, require_beehive: bool = True) -> RuntimeConfig:
    explicit_env = Path(env_file).expanduser() if env_file else None
    candidate_env = explicit_env or Path(os.environ.get("SKILYST_ENV_FILE", ENV_FILE_DEFAULT)).expanduser()
    values, sources = _layered_env(candidate_env if candidate_env.is_file() else None)

    # A2 auth (issue #8): a logged-in session's AgentScopes AK/SK (minted by
    # beehive-core #596, stored in keychain) takes precedence over dev env
    # credentials. Dev profile / env file remains the developer fallback.
    login_ak, login_sk, login_uid, login_name = _login_credentials()
    if login_ak and login_sk:
        beehive = BeehiveCredentials(
            base_url=values.get("BEEHIVE_API") or "https://beehive-api.verse4.pet",
            access_key=login_ak, secret_key=login_sk,
            user=login_name or "", password="",
            uid=str(login_uid or ""),
            source="login (keychain)",
        )
    else:
        beehive = BeehiveCredentials(
            base_url=values.get("BEEHIVE_API") or "https://beehive-api.verse4.pet",
            access_key=values.get("BEEHIVE_PLATFORM_AK", ""),
            secret_key=values.get("BEEHIVE_PLATFORM_SK", ""),
            user=values.get("BEEHIVE_PLATFORM_USER", ""),
            password=values.get("BEEHIVE_PLATFORM_PASS", ""),
            uid=str(values.get("BEEHIVE_PLATFORM_UID", "")),
            source=sources.get("env_file") or sources.get("dev_profile") or "process env",
        )
    if require_beehive and not (beehive.has_api_key or beehive.has_password):
        raise ConfigError(
            "no Beehive credential found: set BEEHIVE_PLATFORM_AK/BEEHIVE_PLATFORM_SK "
            f"(or BEEHIVE_PLATFORM_USER/BEEHIVE_PLATFORM_PASS) in the process environment, "
            f"in {candidate_env} (mode 0600), or point SKILYST_DEV_PROFILE at a profile directory. "
            f"Looked at: env_file={candidate_env if candidate_env.is_file() else 'absent'}, "
            f"dev_profile={sources.get('dev_profile') or 'unset'}")

    base_url = values.get("SKILYST_LLM_BASE_URL", "")
    api_key = values.get("SKILYST_LLM_API_KEY", "")
    llm_model = model or values.get("SKILYST_LLM_MODEL", "")
    if not (base_url and api_key and llm_model):
        cfg_path = llm_config or os.environ.get("SKILYST_LLM_CONFIG", "")
        if not cfg_path:
            profile = _dev_profile_dir()
            if profile:
                cfg_path = str(profile / "config.yaml")
        if cfg_path and Path(cfg_path).expanduser().is_file():
            f_base, f_key, f_model = _read_hermes_model_config(Path(cfg_path))
            base_url, api_key = base_url or f_base, api_key or f_key
            llm_model = llm_model or f_model
            sources["llm_config"] = str(cfg_path)
        elif require_llm:
            raise ConfigError(
                "no LLM endpoint configured: set SKILYST_LLM_BASE_URL / SKILYST_LLM_API_KEY / "
                f"SKILYST_LLM_MODEL, or provide --llm-config <hermes config.yaml>. Looked at: "
                f"env_file={candidate_env if candidate_env.is_file() else 'absent'}, "
                f"llm_config={cfg_path or 'unset'}")

    fallbacks = tuple(m for m in
                      (values.get("SKILYST_LLM_FALLBACKS", "").split(",") if values.get("SKILYST_LLM_FALLBACKS")
                       else FALLBACK_MODELS) if m)
    llm = LLMConfig(base_url=base_url or DEFAULT_BASE_URL, api_key=api_key,
                    model=llm_model or DEFAULT_MODEL, fallbacks=fallbacks)

    root = Path(HOME_ROOT).expanduser()
    max_jobs = None
    if values.get(MAX_JOBS_ENV):
        raw = str(values[MAX_JOBS_ENV]).strip()
        if not raw.isdigit() or int(raw) < 1:
            raise ConfigError(f"{MAX_JOBS_ENV}={raw!r} must be a positive integer (paid jobs per run); "
                              f"it is a spend guard, so an unreadable value is refused rather than "
                              f"guessed at")
        max_jobs = int(raw)
    return RuntimeConfig(beehive=beehive, llm=llm,
                         store_dir=Path(store_dir).expanduser() if store_dir else root / "store",
                         workspace_dir=Path(workspace_dir).expanduser() if workspace_dir else root / "workspace",
                         session_dir=Path(session_dir).expanduser() if session_dir else root / "sessions",
                         env_file=candidate_env if candidate_env.is_file() else None,
                         sources=sources, max_jobs=max_jobs)
