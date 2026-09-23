"""skilyst CLI -- the entire agent-facing surface of Path A.

Commands:
  load <skill-dir>              parse + validate a package (zero-modification), print inventory
  install <skill-dir>           install into the local store
  list                          installed skills
  preload <bundle-dir>          preload the official skills bundle
  doctor <skill-id>             pre-flight: permission + requires.nodes vs the live cluster
  job --prompt ...              submit a 15s video job through the scope-gated token, print artifact URL
  run <skill-id> --request ...  one command: agent loop -> tool -> 15s video artifact
  authz-probe                   acceptance line 3: restricted token vs admin/billing endpoints
  update-plan <new-skill-dir>   hot vs cold decision for an incoming version
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .agent import JOB_TOOL, MinimishAgent, llm_from_hermes_config
from .credentials import (DENIED_PREFIXES, BeehiveClient, RestrictedToken, ScopeRefusal, artifact_url, login)
from .permission import PermissionGate, SandboxViolation, classify_resources
from .registry import SkillStore, inventory_report, preflight_nodes
from .resolver import OfflineError, refine_inventory, resolve
from .skill import SkillValidationError, load_package
from .registry import content_digest

PROFILE_ENV = os.path.expanduser("~/.hermes/profiles/wigowago-visual/.env")
HERMES_CONFIG = os.path.expanduser("~/.hermes/profiles/wigowago-visual/config.yaml")


def read_env_file(path: str) -> dict:
    out = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def load_credentials() -> dict:
    env = read_env_file(PROFILE_ENV)
    return {
        "base": os.environ.get("BEEHIVE_API") or env.get("BEEHIVE_API", "https://beehive-api.verse4.pet"),
        "ak": env.get("BEEHIVE_PLATFORM_AK", ""),
        "sk": env.get("BEEHIVE_PLATFORM_SK", ""),
        "user": env.get("BEEHIVE_PLATFORM_USER", ""),
        "pass": env.get("BEEHIVE_PLATFORM_PASS", ""),
        "uid": env.get("BEEHIVE_PLATFORM_UID", ""),
    }


def restricted_token(creds: dict) -> RestrictedToken:
    return RestrictedToken(access_key=creds["ak"], secret_key=creds["sk"], account=creds["user"])


def cmd_load(args) -> int:
    skill_dir = Path(args.dir).resolve()
    package = load_package(skill_dir, content_digest(skill_dir))
    report = inventory_report(package)
    report["load_mode"] = "degraded (community skill: no manifest.json)" if package.degraded else "full"
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_install(args) -> int:
    store = SkillStore(Path(args.store))
    package = store.install(Path(args.dir).resolve(), origin=args.origin)
    print(json.dumps({"installed": package.skill_id, "version": package.version,
                      "degraded": package.degraded, "digest": package.digest}, indent=2))
    return 0


def cmd_list(args) -> int:
    store = SkillStore(Path(args.store))
    print(json.dumps([{"skill_id": p.skill_id, "version": p.version, "degraded": p.degraded,
                       "channel": p.update.get("channel", "community")} for p in store.list()], indent=2))
    return 0


def cmd_preload(args) -> int:
    store = SkillStore(Path(args.store))
    loaded = store.preload_official_bundle(Path(args.bundle).resolve())
    print(json.dumps({"preloaded": [p.skill_id for p in loaded]}, indent=2))
    return 0


def cmd_doctor(args) -> int:
    creds = load_credentials()
    store = SkillStore(Path(args.store))
    package = store.get(args.skill_id)
    problems = preflight_nodes(package, BeehiveClient(creds["base"], token=restricted_token(creds)))
    print(json.dumps({"skill_id": package.skill_id, "permission": package.permission,
                      "problems": problems, "runnable": not [p for p in problems if p.startswith("REQUIRED")]},
                     indent=2, ensure_ascii=False))
    return 0 if not [p for p in problems if p.startswith("REQUIRED")] else 2


def _submit_video(creds: dict, nodes: list[dict], label: str, timeout_s: int = 900) -> dict:
    client = BeehiveClient(creds["base"], token=restricted_token(creds))
    job = client.submit_job(nodes)
    job_id = job.get("id") or job.get("job_id")
    print(f"[{label}] submitted job {job_id}", file=sys.stderr)

    def tick(state):
        print(f"[{label}] {state.get('status')} progress={state.get('progress')}", file=sys.stderr)

    final = client.wait_for_job(job_id, timeout_s=timeout_s, on_tick=tick)
    return {"job_id": job_id, "status": final.get("status"), "artifact_url": artifact_url(final),
            "error": final.get("error_msg"), "node_count": len(final.get("nodes") or [])}


def cmd_job(args) -> int:
    creds = load_credentials()
    node = {"type": "generate", "provider": args.provider,
            "config": {"prompt": args.prompt, "duration": args.duration,
                       "resolution": args.resolution, "ratio": args.ratio}}
    print(json.dumps(_submit_video(creds, [node], "job"), indent=2, ensure_ascii=False))
    return 0


def cmd_run(args) -> int:
    """One command: load skill -> agent loop -> tool -> 15s video artifact."""
    started = time.time()
    creds = load_credentials()
    store = SkillStore(Path(args.store))
    package = store.get(args.skill_id)
    gate = PermissionGate(package.dir, package.permission, workspace=Path(args.workspace))
    problems = preflight_nodes(package, BeehiveClient(creds["base"], token=restricted_token(creds)))
    if [p for p in problems if p.startswith("REQUIRED")]:
        print(json.dumps({"refused": problems}, indent=2, ensure_ascii=False), file=sys.stderr)
        return 2

    plan = (package.manifest or {}).get("plan", {})
    calls: list[dict] = []

    def executor(name: str, tool_args: dict) -> dict:
        if name != "beehive_submit_video":
            raise RuntimeError(f"unknown tool {name}")
        gate.require_secrets("Beehive credential")          # permission.secrets gate
        node = {"type": "generate", "provider": plan.get("provider", "minimax-h3"), "config": {
            "prompt": tool_args["prompt"], "duration": int(tool_args.get("duration", plan.get("duration", 15))),
            "resolution": tool_args.get("resolution", plan.get("resolution", "768P")),
            "ratio": tool_args.get("ratio", plan.get("ratio", "9:16"))}}
        result = _submit_video(creds, [node], "run")
        calls.append({"tool": name, "args": tool_args, "result": result})
        return result

    llm = llm_from_hermes_config(HERMES_CONFIG)
    agent = MinimishAgent(llm, [JOB_TOOL], executor, max_turns=args.max_turns)
    system = (f"You are the Skilyst video agent. The user loaded skill {package.skill_id}@{package.version}.\n"
              f"Skill instructions:\n{package.body[:2500]}\n\n"
              f"Declared node plan: {json.dumps(plan)}\n"
              f"Call beehive_submit_video exactly once with parameters that follow the skill methodology, "
              f"then report the artifact URL.")
    answer = agent.run(system, args.request)
    print(json.dumps({"skill": package.skill_id, "version": package.version, "answer": answer,
                      "tool_calls": calls, "wall_clock_s": round(time.time() - started, 1),
                      "llm_trace": agent.trace}, indent=2, ensure_ascii=False))
    return 0


def cmd_authz_probe(args) -> int:
    """Acceptance line 3: what does a scope-restricted key actually get?"""
    creds = load_credentials()
    probes = [("GET", "/api/v1/admin/users"), ("POST", "/api/v1/admin/users"),
              ("PUT", "/api/v1/nodes/generate:drawnow"),
              ("PUT", "/api/v1/billing/nodes/generate:drawnow/pricing"),
              ("POST", f"/api/v1/billing/wallet/{creds['uid']}/grant"),
              ("GET", "/api/v1/billing/wallet"), ("GET", "/api/v1/billing/history"),
              ("GET", "/api/v1/jobs"), ("GET", "/api/v1/assets"), ("GET", "/api/v1/nodes")]
    results = []
    scoped = BeehiveClient(creds["base"], token=restricted_token(creds))
    bearer = login(creds["base"], creds["user"], creds["pass"]) if args.with_jwt else None
    jwt_client = BeehiveClient(creds["base"], bearer=bearer) if bearer else None
    anon = BeehiveClient(creds["base"])
    for method, path in probes:
        row = {"method": method, "path": path}
        try:
            status, _ = scoped.request(method, path, {} if method == "POST" or method == "PUT" else None, raw=True)
            row["scoped_ak_sk"] = status
        except ScopeRefusal as exc:
            row["scoped_ak_sk"] = f"refused client-side: {exc}"
        if jwt_client:
            row["nonadmin_jwt"] = jwt_client.request(method, path,
                                                     {} if method in ("POST", "PUT") else None, raw=True)[0]
        row["anonymous"] = anon.request(method, path, {} if method in ("POST", "PUT") else None, raw=True)[0]
        results.append(row)
    print(json.dumps({"denied_prefixes": list(DENIED_PREFIXES), "probes": results}, indent=2, ensure_ascii=False))
    return 0


def cmd_update_plan(args) -> int:
    store = SkillStore(Path(args.store))
    print(json.dumps(store.plan_update(Path(args.dir).resolve()), indent=2, ensure_ascii=False))
    return 0


def cmd_inventory(args) -> int:
    """Acceptance line 1 (first half): community package, zero modification, full inventory."""
    skill_dir = Path(args.dir).resolve()
    package = load_package(skill_dir, content_digest(skill_dir))
    report = inventory_report(package)
    report.update(refine_inventory(package.dir, package.body, report["remote"]))
    report["load_mode"] = "degraded (community skill, no manifest.json)" if package.degraded else "full (manifest.json)"
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_resolve(args) -> int:
    """Acceptance line 1 (second half): every reference, resolved with egress off/limited."""
    skill_dir = Path(args.dir).resolve()
    package = load_package(skill_dir, content_digest(skill_dir))
    gate = PermissionGate(package.dir, package.permission)
    results = []
    for ref in args.refs:
        try:
            res = resolve(skill_dir, ref, gate.egress_hosts)
            results.append(res.__dict__)
        except OfflineError as exc:
            results.append({"ref": ref, "kind": "remote", "detail": f"LOUD FAILURE: {exc}"})
    print(json.dumps({"skill_id": package.skill_id, "permission_egress": package.permission.get("egress"),
                      "resolutions": results}, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skilyst", description="Skilyst thin agent runtime (Path A PoC)")
    parser.add_argument("--store", default=os.path.expanduser("~/t1-poc/path-a-skilyst/store"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("load"); p.add_argument("dir"); p.set_defaults(func=cmd_load)
    p = sub.add_parser("install"); p.add_argument("dir"); p.add_argument("--origin", default="local")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("list"); p.set_defaults(func=cmd_list)
    p = sub.add_parser("preload"); p.add_argument("bundle"); p.set_defaults(func=cmd_preload)
    p = sub.add_parser("doctor"); p.add_argument("skill_id"); p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("job")
    p.add_argument("--prompt", required=True); p.add_argument("--provider", default="minimax-h3")
    p.add_argument("--duration", type=int, default=15); p.add_argument("--resolution", default="768P")
    p.add_argument("--ratio", default="9:16"); p.set_defaults(func=cmd_job)
    p = sub.add_parser("run")
    p.add_argument("skill_id"); p.add_argument("--request", required=True)
    p.add_argument("--workspace", default=os.path.expanduser("~/t1-poc/path-a-skilyst/workspace"))
    p.add_argument("--max-turns", type=int, default=4); p.set_defaults(func=cmd_run)
    p = sub.add_parser("authz-probe"); p.add_argument("--with-jwt", action="store_true")
    p.set_defaults(func=cmd_authz_probe)
    p = sub.add_parser("update-plan"); p.add_argument("dir"); p.set_defaults(func=cmd_update_plan)
    p = sub.add_parser("inventory"); p.add_argument("dir"); p.set_defaults(func=cmd_inventory)
    p = sub.add_parser("resolve"); p.add_argument("dir"); p.add_argument("refs", nargs="+")
    p.set_defaults(func=cmd_resolve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (SkillValidationError, ScopeRefusal) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
