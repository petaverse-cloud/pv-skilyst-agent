"""skilyst CLI -- the whole agent-facing surface of the runtime.

Skill / package commands (acceptance line 1):
  inventory <dir>            parse a package with zero modification, print the full inventory
  load <dir>                 parse + validate + integrity-check a package
  resolve <dir> <refs...>    resolve every reference with the declared egress policy
  install <dir>              install into the local store (receipt-backed)
  uninstall <skill-id>       remove an installed skill
  list                       installed skills
  preload <bundle-dir>       preload the platform-signed official bundle
  digest <dir> [--write]     content digest of a package (--write records it in manifest.json)
  update-plan <dir>          hot vs cold decision for an incoming version

Runtime commands (A1: session + loop + platform tools):
  run <skill-id> --request … one command: load skill -> agent loop -> tool -> artifact URL
  chat [--skill id]          conversation (one-shot with --message, otherwise a REPL)
  sessions                   list local sessions
  doctor [skill-id]          integrity + node pre-flight (acceptance line 2/3 precondition)

Platform diagnostics (acceptance line 3):
  authz-probe                what a scope-restricted key actually gets, client-side and server-side
  job --prompt …             direct 15s video submission (bypasses the loop; used for line-2 reruns)
  config                     the resolved configuration, secrets redacted
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from agent import AgentLoop, LoopConfig, PromptContext, build_registry
from beehive import (DEFAULT_SCOPE, DENIED_PREFIXES, BeehiveClient, BeehiveError, ScopeRefusal,
                     client_for, client_from_bearer, job_handle, verify_artifact)
from config import ConfigError, RuntimeConfig, resolve
from llm import LLMError, ModelRouter
from sandbox import (OfflineError, PermissionGate, SandboxViolation, refine_inventory,
                     resolve as resolve_ref)
from session import SessionStore
from skills import SkillPackage, SkillStore, SkillValidationError, content_digest, load_package
from skills.store import inventory_report, preflight_nodes

EXIT_OK = 0
EXIT_REFUSED = 2          # a rule said no (scope/sandbox/validation)
EXIT_INCOMPLETE = 3       # the loop did not reach an answer
EXIT_PLATFORM = 4         # the platform call failed


def emit(payload, stream=sys.stdout) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str), file=stream)


def note(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------
def runtime(args) -> RuntimeConfig:
    return resolve(env_file=getattr(args, "env_file", None),
                   store_dir=getattr(args, "store", None),
                   workspace_dir=getattr(args, "workspace", None),
                   session_dir=getattr(args, "sessions", None),
                   llm_config=getattr(args, "llm_config", None),
                   model=getattr(args, "model", None),
                   require_llm=bool(getattr(args, "needs_llm", True)),
                   require_beehive=bool(getattr(args, "needs_beehive", True)))


def store_for(cfg: RuntimeConfig, verify: bool = True) -> SkillStore:
    return SkillStore(cfg.store_dir, verify=verify)


def gated_client(cfg: RuntimeConfig, bypass: bool = False) -> BeehiveClient:
    """The only way a skill-facing component reaches the platform."""
    if cfg.beehive.has_api_key:
        client = client_for(cfg.beehive.access_key, cfg.beehive.secret_key, cfg.beehive.user,
                            cfg.beehive.base_url)
        client.bypass_scope = bypass
        return client
    if cfg.beehive.has_password:
        bearer = BeehiveClient(cfg.beehive.base_url).login(cfg.beehive.user, cfg.beehive.password)
        client = client_from_bearer(cfg.beehive.base_url, bearer, cfg.beehive.user)
        client.bypass_scope = bypass
        return client
    raise ConfigError("no usable Beehive credential (need AK/SK or user/password)")


def load_skill(store: SkillStore, skill_id: str) -> SkillPackage:
    try:
        return store.get(skill_id)
    except KeyError as exc:
        raise SkillValidationError(str(exc)) from exc


# ---------------------------------------------------------------------------
# package commands (acceptance line 1)
# ---------------------------------------------------------------------------
def cmd_inventory(args) -> int:
    skill_dir = Path(args.dir).resolve()
    package = load_package(skill_dir)
    report = inventory_report(package)
    report.update(refine_inventory(package.dir, package.body, report["remote"]))
    report["load_mode"] = ("degraded (community skill, no manifest.json)" if package.degraded
                           else "full (manifest.json)")
    report["tree_digest"] = package.tree_digest
    emit(report)
    return EXIT_OK


def cmd_load(args) -> int:
    package = load_package(Path(args.dir).resolve())
    emit({"skill_id": package.skill_id, "version": package.version, "degraded": package.degraded,
          "digest": package.digest, "tree_digest": package.tree_digest,
          "permission": package.permission, "requires_nodes": [n.node_id for n in package.requires_nodes],
          "warnings": list(package.warnings)})
    return EXIT_OK


def cmd_resolve(args) -> int:
    skill_dir = Path(args.dir).resolve()
    package = load_package(skill_dir)
    gate = PermissionGate(package.dir, package.permission, workspace=Path(args.workspace or os.getcwd()))
    results = []
    for ref in args.refs:
        try:
            results.append(resolve_ref(skill_dir, ref, gate.egress_hosts).__dict__)
        except OfflineError as exc:
            results.append({"ref": ref, "kind": "remote", "detail": f"LOUD FAILURE: {exc}"})
    emit({"skill_id": package.skill_id, "permission_egress": package.permission.get("egress"),
          "resolutions": results})
    return EXIT_OK


def cmd_install(args) -> int:
    cfg = runtime(args)
    package = store_for(cfg).install(Path(args.dir).resolve(), origin=args.origin)
    emit({"installed": package.skill_id, "version": package.version, "degraded": package.degraded,
          "digest": package.digest, "store": str(cfg.store_dir)})
    return EXIT_OK


def cmd_uninstall(args) -> int:
    cfg = runtime(args)
    store_for(cfg).uninstall(args.skill_id)
    emit({"uninstalled": args.skill_id})
    return EXIT_OK


def cmd_list(args) -> int:
    cfg = runtime(args)
    store = store_for(cfg)
    emit([{"skill_id": p.skill_id, "version": p.version, "degraded": p.degraded,
           "channel": p.update.get("channel", "community"), "requires_nodes": [n.node_id for n in p.requires_nodes]}
          for p in store.list()])
    return EXIT_OK


def cmd_preload(args) -> int:
    cfg = runtime(args)
    loaded = store_for(cfg).preload_official_bundle(Path(args.bundle).resolve())
    emit({"preloaded": [{"skill_id": p.skill_id, "version": p.version} for p in loaded],
          "store": str(cfg.store_dir)})
    return EXIT_OK


def cmd_digest(args) -> int:
    skill_dir = Path(args.dir).resolve()
    digest = content_digest(skill_dir)
    payload = {"dir": str(skill_dir), "content_digest": digest}
    sidecar = skill_dir / "manifest.json"
    if args.write:
        if not sidecar.is_file():
            raise SkillValidationError(f"{skill_dir} has no manifest.json to write the digest into")
        manifest = json.loads(sidecar.read_text())
        manifest["content_digest"] = digest
        sidecar.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        payload["written_to"] = str(sidecar)
    elif sidecar.is_file():
        payload["declared"] = json.loads(sidecar.read_text()).get("content_digest")
    emit(payload)
    return EXIT_OK


def cmd_update_plan(args) -> int:
    cfg = runtime(args)
    emit(store_for(cfg).plan_update(Path(args.dir).resolve()))
    return EXIT_OK


# ---------------------------------------------------------------------------
# runtime commands (A1)
# ---------------------------------------------------------------------------
def _agent(args, cfg: RuntimeConfig, skill_id: str | None, session_title: str):
    store = store_for(cfg)
    skills = store.list()
    active = load_skill(store, skill_id) if skill_id else None
    if active is not None and active.skill_id not in {p.skill_id for p in skills}:
        raise SkillValidationError(f"{active.skill_id} is not installed in {cfg.store_dir}")
    gate = PermissionGate(active.dir, active.permission, workspace=cfg.workspace_dir) if active else None
    client = None
    preflight = None
    if active is not None and active.requires_nodes:
        client = gated_client(cfg)
        preflight = preflight_nodes(active, client,
                                    allow_fallback=bool(getattr(args, "allow_fallback", False)))
    cfg.workspace_dir.mkdir(parents=True, exist_ok=True)
    sessions = SessionStore(cfg.session_dir)
    if getattr(args, "session", None):
        session = sessions.open(args.session)
    else:
        session = sessions.create(title=session_title, model=cfg.llm.model,
                                  workspace=str(cfg.workspace_dir),
                                  skills=[active.skill_id] if active else [])
    registry = build_registry(store, client, active, gate, cfg.workspace_dir,
                              dry_run=getattr(args, "dry_run", False), on_event=note,
                              max_jobs=getattr(args, "max_jobs", 1))
    prompt_ctx = PromptContext(skills=skills, active_skill=active, workspace=str(cfg.workspace_dir),
                               model=cfg.llm.model, platform=cfg.beehive.base_url)
    loop = AgentLoop(ModelRouter(cfg.llm), registry, session, prompt_ctx,
                     LoopConfig(max_turns=args.max_turns, stream=not args.no_stream),
                     on_event=note, on_delta=(lambda text: print(text, end="", file=sys.stderr, flush=True))
                     if not args.no_stream else None)
    return loop, session, active, preflight


def _run_summary(result, session, active, preflight) -> dict:
    return {"session_id": session.session_id, "skill": active.skill_id if active else None,
            "stop_reason": result.stop_reason, "answer": result.answer, "model": result.model,
            "turns": result.turns, "wall_clock_s": result.wall_clock_s, "usage": result.usage,
            "artifacts": result.artifact_urls(),
            "tool_calls": [{"tool": c.name, "arguments": c.arguments, "result": c.result, "error": c.error,
                            "duration_s": c.duration_s} for c in result.tool_calls],
            "preflight": None if preflight is None else {
                "registry_available": preflight.registry_available,
                "problems": [{"node_id": p.node_id, "severity": p.severity, "detail": p.detail}
                             for p in preflight.problems]},
            "error": result.error or None}


def cmd_run(args) -> int:
    cfg = runtime(args)
    loop, session, active, preflight = _agent(args, cfg, args.skill_id, args.request[:60])
    if preflight is not None and not preflight.runnable:
        emit({"refused": "node pre-flight failed -- the skill's required nodes are not runnable here",
              "skill": active.skill_id,
              "problems": [{"node_id": p.node_id, "severity": p.severity, "detail": p.detail}
                           for p in preflight.problems]}, stream=sys.stderr)
        return EXIT_REFUSED
    if preflight is not None:
        for problem in preflight.problems:
            note(f"preflight[{problem.severity}] {problem.node_id}: {problem.detail}")
    result = loop.run(args.request)
    emit(_run_summary(result, session, active, preflight))
    if result.stop_reason == "llm_error":
        return EXIT_INCOMPLETE
    if result.stop_reason != "completed":
        return EXIT_INCOMPLETE
    return EXIT_OK


def cmd_chat(args) -> int:
    cfg = runtime(args)
    if args.message:
        loop, session, active, preflight = _agent(args, cfg, args.skill, args.message[:60])
        result = loop.run(args.message)
        emit(_run_summary(result, session, active, preflight))
        return EXIT_OK if result.ok else EXIT_INCOMPLETE

    loop, session, active, preflight = _agent(args, cfg, args.skill, "interactive session")
    note(f"session {session.session_id} | skill {active.skill_id if active else 'none'} | "
         f"model {cfg.llm.model} | tools {', '.join(loop.tools.names)}")
    note("commands: /skills /tools /session /exit")
    while True:
        try:
            line = input("skilyst> ").strip()
        except (EOFError, KeyboardInterrupt):
            note("")
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/skills":
            emit([{"skill_id": p.skill_id, "version": p.version, "degraded": p.degraded}
                  for p in store_for(cfg).list()])
            continue
        if line == "/tools":
            emit(loop.tools.names)
            continue
        if line == "/session":
            emit(session.summary())
            continue
        result = loop.run(line)
        if result.answer:
            note("")
        note(f"[{result.stop_reason} turns={result.turns} tools={len(result.tool_calls)} "
             f"{result.wall_clock_s}s]")
    return EXIT_OK


def cmd_sessions(args) -> int:
    cfg = runtime(args)
    emit(SessionStore(cfg.session_dir).list())
    return EXIT_OK


def cmd_doctor(args) -> int:
    cfg = runtime(args)
    store = store_for(cfg)
    payload: dict = {"store": str(cfg.store_dir), "env_file": str(cfg.env_file) if cfg.env_file else None,
                     "integrity": store.verify_all(), "config": cfg.redacted()}
    if args.skill_id:
        package = load_skill(store, args.skill_id)
        client = gated_client(cfg)
        report = preflight_nodes(package, client, allow_fallback=args.allow_fallback)
        payload["skill"] = {"skill_id": package.skill_id, "version": package.version,
                            "digest": package.digest, "permission": package.permission,
                            "plan": package.plan, "warnings": list(package.warnings)}
        payload["preflight"] = {"registry_available": report.registry_available,
                                "runnable": report.runnable,
                                "problems": [{"node_id": p.node_id, "severity": p.severity,
                                              "detail": p.detail} for p in report.problems]}
        emit(payload)
        return EXIT_OK if report.runnable else EXIT_REFUSED
    emit(payload)
    return EXIT_OK if all(row["ok"] for row in payload["integrity"]) else EXIT_REFUSED


def cmd_config(args) -> int:
    cfg = runtime(args)
    emit(cfg.redacted())
    return EXIT_OK


# ---------------------------------------------------------------------------
# platform diagnostics (acceptance line 2 / 3)
# ---------------------------------------------------------------------------
def cmd_job(args) -> int:
    cfg = runtime(args)
    client = gated_client(cfg)
    node = {"type": "generate", "provider": args.provider,
            "config": {"prompt": args.prompt, "duration": args.duration,
                       "resolution": args.resolution, "ratio": args.ratio}}
    if args.dry_run:
        emit({"dry_run": True, "node": node})
        return EXIT_OK
    payload = client.submit_job([node])
    handle = job_handle(payload)
    note(f"submitted job {handle.job_id}")
    final = client.wait_for_job(handle.job_id, timeout_s=args.timeout,
                                on_tick=lambda s: note(f"  {s.get('status')} progress={s.get('progress')}"))
    handle = job_handle(final)
    check = verify_artifact(handle.artifact_url) if handle.artifact_url else None
    emit({"job_id": handle.job_id, "status": handle.status, "artifact_url": handle.artifact_url,
          "error": handle.error, "nodes": len(final.get("nodes") or []),
          "artifact_check": check.__dict__ if check else None})
    return EXIT_OK if handle.succeeded else EXIT_PLATFORM


def cmd_authz_probe(args) -> int:
    cfg = runtime(args)
    probes = [("GET", "/api/v1/admin/users"), ("POST", "/api/v1/admin/users"),
              ("PUT", "/api/v1/nodes/generate:drawnow"),
              ("PUT", "/api/v1/billing/nodes/generate:drawnow/pricing"),
              ("POST", f"/api/v1/billing/wallet/{cfg.beehive.uid or 'self'}/grant"),
              ("GET", "/api/v1/billing/wallet"), ("GET", "/api/v1/billing/history"),
              ("GET", "/api/v1/jobs"), ("GET", "/api/v1/assets"), ("GET", "/api/v1/nodes")]
    scoped = gated_client(cfg)
    ungated = gated_client(cfg, bypass=True) if args.bypass_gate else None
    anonymous = BeehiveClient(cfg.beehive.base_url)
    rows = []
    for method, path in probes:
        row = {"method": method, "path": path}
        try:
            status, _ = scoped.request(method, path, {} if method in ("POST", "PUT") else None, raw=True)
            row["scoped_client_gate"] = status
        except ScopeRefusal as exc:
            row["scoped_client_gate"] = f"refused client-side: {exc}"
        if ungated is not None:
            try:
                status, body = ungated.request(method, path, {} if method in ("POST", "PUT") else None,
                                               raw=True)
                row["server_side_restricted_key"] = status
                row["server_body"] = body[:160]
            except (BeehiveError, ScopeRefusal) as exc:
                row["server_side_restricted_key"] = f"transport error: {exc}"
        try:
            row["anonymous"] = anonymous.request(method, path, {} if method in ("POST", "PUT") else None,
                                                 raw=True)[0]
        except BeehiveError as exc:
            row["anonymous"] = f"transport error: {exc}"
        rows.append(row)
    emit({"base_url": cfg.beehive.base_url, "token_scope": sorted(DEFAULT_SCOPE),
          "denied_prefixes": list(DENIED_PREFIXES), "probes": rows})
    return EXIT_OK


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skilyst", description="Skilyst agent runtime")
    parser.add_argument("--store", default=None, help="skill store directory (default ~/.skilyst/store)")
    parser.add_argument("--workspace", default=None, help="run workspace (default ~/.skilyst/workspace)")
    parser.add_argument("--sessions", default=None, help="session directory (default ~/.skilyst/sessions)")
    parser.add_argument("--env-file", default=None, help="local credential file (default ~/.skilyst/env)")
    parser.add_argument("--llm-config", default=None, help="Hermes-style config.yaml to read model settings from")
    parser.add_argument("--model", default=None, help="override the model id")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inventory"); p.add_argument("dir"); p.set_defaults(func=cmd_inventory, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("load"); p.add_argument("dir"); p.set_defaults(func=cmd_load, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("resolve"); p.add_argument("dir"); p.add_argument("refs", nargs="+")
    p.set_defaults(func=cmd_resolve, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("install"); p.add_argument("dir"); p.add_argument("--origin", default="local")
    p.set_defaults(func=cmd_install, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("uninstall"); p.add_argument("skill_id"); p.set_defaults(func=cmd_uninstall, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("list"); p.set_defaults(func=cmd_list, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("preload"); p.add_argument("bundle"); p.set_defaults(func=cmd_preload, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("digest"); p.add_argument("dir"); p.add_argument("--write", action="store_true")
    p.set_defaults(func=cmd_digest, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("update-plan"); p.add_argument("dir"); p.set_defaults(func=cmd_update_plan, needs_llm=False, needs_beehive=False)

    p = sub.add_parser("run")
    p.add_argument("skill_id"); p.add_argument("--request", required=True)
    p.add_argument("--max-turns", type=int, default=8); p.add_argument("--no-stream", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="run the loop without submitting a paid job")
    p.add_argument("--allow-fallback", action="store_true",
                   help="accept the manifest's declared fallback node when the required one is absent")
    p.add_argument("--max-jobs", type=int, default=1,
                   help="how many paid jobs this run may submit (default 1)")
    p.add_argument("--session", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("chat")
    p.add_argument("--skill", default=None, help="activate a skill for this session")
    p.add_argument("--message", default=None, help="one-shot message instead of a REPL")
    p.add_argument("--max-turns", type=int, default=8); p.add_argument("--no-stream", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--session", default=None)
    p.add_argument("--allow-fallback", action="store_true")
    p.add_argument("--max-jobs", type=int, default=1)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("sessions"); p.set_defaults(func=cmd_sessions, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("doctor"); p.add_argument("skill_id", nargs="?")
    p.add_argument("--allow-fallback", action="store_true")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("config"); p.set_defaults(func=cmd_config)

    p = sub.add_parser("job")
    p.add_argument("--prompt", required=True); p.add_argument("--provider", default="minimax-h3")
    p.add_argument("--duration", type=int, default=15); p.add_argument("--resolution", default="768P")
    p.add_argument("--ratio", default="9:16"); p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_job)

    p = sub.add_parser("authz-probe")
    p.add_argument("--bypass-gate", action="store_true",
                   help="also measure what the platform enforces server-side with this key")
    p.set_defaults(func=cmd_authz_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    try:
        return args.func(args)
    except ConfigError as exc:
        note(f"CONFIG: {exc}")
        return EXIT_REFUSED
    except (SkillValidationError, ScopeRefusal, SandboxViolation) as exc:
        note(f"REFUSED: {type(exc).__name__}: {exc}")
        return EXIT_REFUSED
    except BeehiveError as exc:
        note(f"PLATFORM: {exc}")
        return EXIT_PLATFORM
    except (LLMError, TimeoutError) as exc:
        note(f"INCOMPLETE: {type(exc).__name__}: {exc}")
        return EXIT_INCOMPLETE
    except KeyboardInterrupt:
        note(f"interrupted after {time.time() - started:.1f}s")
        return EXIT_INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
