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

Desktop shell (A2):
  serve                      loopback HTTP control plane the Tauri shell drives (see src/serve.py)

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

from agent import open_run
from agent.runner import gated_client, load_skill, skill_store
from beehive import (DEFAULT_SCOPE, DENIED_PREFIXES, BeehiveClient, BeehiveError, ScopeRefusal,
                     job_handle, verify_artifact)
from config import (DEFAULT_MAX_JOBS_INTERACTIVE, DEFAULT_MAX_JOBS_ONE_SHOT, MAX_JOBS_ENV, ConfigError,
                    RuntimeConfig, resolve)
from llm import LLMError
from sandbox import (OfflineError, PermissionGate, SandboxViolation, refine_inventory,
                     resolve as resolve_ref)
from serve import ServeOptions, serve
from session import SessionStore
from skills import SkillStore, SkillValidationError, content_digest, load_package
from skills.store import inventory_report, preflight_nodes

EXIT_OK = 0
EXIT_REFUSED = 2          # a rule said no (scope/sandbox/validation)
EXIT_INCOMPLETE = 3       # the loop did not reach an answer
EXIT_PLATFORM = 4         # the platform call failed

# The placeholder title a REPL session starts with, replaced by the first request.
INTERACTIVE_TITLE = "interactive session"


def emit(payload, stream=sys.stdout) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str), file=stream)


def emit_line(payload) -> None:
    """One compact JSON object on one line -- the contract for machine readers."""
    print(json.dumps(payload, ensure_ascii=False, default=str), file=sys.stdout, flush=True)


def note(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------
def resolve_kwargs(args) -> dict:
    """The global configuration flags, in the shape `config.resolve` takes them.

    Shared with `serve`, which re-resolves at the strictness each route needs.
    """
    return {"env_file": getattr(args, "env_file", None), "store_dir": getattr(args, "store", None),
            "workspace_dir": getattr(args, "workspace", None),
            "session_dir": getattr(args, "sessions", None),
            "llm_config": getattr(args, "llm_config", None), "model": getattr(args, "model", None)}


def runtime(args, *, require_llm: bool | None = None,
            require_beehive: bool | None = None) -> RuntimeConfig:
    return resolve(**resolve_kwargs(args),
                   require_llm=bool(getattr(args, "needs_llm", True)) if require_llm is None else require_llm,
                   require_beehive=(bool(getattr(args, "needs_beehive", True))
                                    if require_beehive is None else require_beehive))


def store_for(cfg: RuntimeConfig, verify: bool = True) -> SkillStore:
    return skill_store(cfg, verify=verify)


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
def job_budget(explicit: int | None, cfg: RuntimeConfig, interactive: bool) -> int:
    """The paid-job budget for one run: an explicit flag, else the configured value,
    else the mode default. Pure, so the rule is testable without a run."""
    if explicit is not None and explicit < 1:
        raise ConfigError(f"--max-jobs {explicit} must be a positive integer (paid jobs per run); "
                          f"a job budget of 0 is what --dry-run is for")
    return explicit if explicit is not None else cfg.job_budget(interactive)


def _agent(args, cfg: RuntimeConfig, skill_id: str | None, session_title: str,
           interactive: bool = False):
    """Build the run for one turn through the shared runner (same path `serve` uses).

    ``interactive`` picks the job-budget default: an unattended one-shot run gets one
    paid job, a session where the user is watching gets the interactive default. An
    explicit ``--max-jobs`` or ``SKILYST_MAX_JOBS`` always wins.
    """
    budget = job_budget(getattr(args, "max_jobs", None), cfg, interactive)
    return open_run(cfg, skill_id=skill_id, session_id=getattr(args, "session", None),
                    title=session_title, dry_run=bool(getattr(args, "dry_run", False)),
                    max_turns=args.max_turns, stream=not args.no_stream,
                    allow_fallback=bool(getattr(args, "allow_fallback", False)),
                    max_jobs=budget, on_event=note,
                    on_delta=(lambda text: print(text, end="", file=sys.stderr, flush=True))
                    if not args.no_stream else None)


def cmd_run(args) -> int:
    cfg = runtime(args)
    ctx = _agent(args, cfg, args.skill_id, args.request[:60])
    if ctx.preflight is not None and not ctx.preflight.runnable:
        emit({"refused": "node pre-flight failed -- the skill's required nodes are not runnable here",
              "skill": ctx.active.skill_id,
              "problems": [{"node_id": p.node_id, "severity": p.severity, "detail": p.detail}
                           for p in ctx.preflight.problems]}, stream=sys.stderr)
        return EXIT_REFUSED
    if ctx.preflight is not None:
        for problem in ctx.preflight.problems:
            note(f"preflight[{problem.severity}] {problem.node_id}: {problem.detail}")
    result = ctx.loop.run(args.request)
    emit(ctx.result_payload(result))
    if result.stop_reason == "llm_error":
        return EXIT_INCOMPLETE
    if result.stop_reason != "completed":
        return EXIT_INCOMPLETE
    return EXIT_OK


def cmd_chat(args) -> int:
    cfg = runtime(args)
    if args.message:
        ctx = _agent(args, cfg, args.skill, args.message[:60])
        result = ctx.loop.run(args.message)
        emit(ctx.result_payload(result))
        return EXIT_OK if result.ok else EXIT_INCOMPLETE

    ctx = _agent(args, cfg, args.skill, INTERACTIVE_TITLE, interactive=True)
    session, active, loop = ctx.session, ctx.active, ctx.loop
    note(f"session {session.session_id} | skill {active.skill_id if active else 'none'} | "
         f"model {cfg.llm.model} | job budget {loop.tools.job_budget} | "
         f"tools {', '.join(loop.tools.names)}")
    note("commands: /skills /tools /session /exit")
    note("this is the interactive surface: it streams the run and writes the JSON summary to "
         "stderr. For a scriptable one-shot (summary on stdout) use `skilyst chat --message …` "
         "or `skilyst run <skill-id>`.")
    while True:
        try:
            # The prompt goes to stderr: stdout carries the machine-readable JSON
            # (same contract as `run`), so `skilyst chat < request.txt | jq` must not
            # have to strip prompt text out of its input.
            sys.stderr.write("skilyst> ")
            sys.stderr.flush()
            line = input().strip()
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
        if session.meta.get("title") == INTERACTIVE_TITLE:
            # Every REPL session used to be titled "interactive session", so the desktop
            # shell's list showed a column of identical rows. Name it after the first
            # request, like the one-shot paths do.
            session.update(title=line[:60])
        result = loop.run(line)
        if result.answer:
            note("")
        note(f"[{result.stop_reason} turns={result.turns} tools={len(result.tool_calls)} "
             f"{result.wall_clock_s}s]")
    return EXIT_OK


def cmd_serve(args) -> int:
    """Loopback control plane for the desktop shell (see src/serve.py)."""
    cfg = runtime(args, require_llm=False, require_beehive=False)
    options = ServeOptions(host=args.host, port=args.port, token=args.token,
                           token_file=args.token_file, skill=args.skill, dry_run=args.dry_run,
                           max_turns=args.max_turns, max_jobs=args.max_jobs,
                           allow_fallback=args.allow_fallback,
                           orphan_guard=args.orphan_guard,
                           cors_origins=tuple(args.cors_origin or ()))
    return serve(cfg, options, resolve_kwargs=resolve_kwargs(args), on_ready=emit_line, note=note)


def cmd_sessions(args) -> int:
    cfg = runtime(args)
    emit(SessionStore(cfg.session_dir).list())
    return EXIT_OK


def cmd_doctor(args) -> int:
    cfg = runtime(args, require_beehive=bool(args.skill_id), require_llm=False)
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
# master-library commands (#56 data half — registry reads, core#714)
# ---------------------------------------------------------------------------
def cmd_library(args) -> int:
    cfg = runtime(args)
    client = gated_client(cfg)
    payload = client.list_skills(limit=args.limit, offset=args.offset,
                                 visibility=args.visibility)
    skills = payload.get("skills") or []
    emit({"total": payload.get("total"), "limit": payload.get("limit"),
          "offset": payload.get("offset"),
          "skills": [{"id": s.get("id"), "slug": s.get("slug"), "version": s.get("version"),
                      "fork_depth": s.get("fork_depth"),
                      "price_usd": s.get("price_usd"),
                      "price_display": _display_price(s.get("price_usd")),
                      "visibility": s.get("visibility"),
                      "skeleton": s.get("skeleton_summary")}
                     for s in skills]})
    return EXIT_OK


def _display_price(price_usd) -> str:
    """µUSD integers (1 cent = 10,000) to a human string, per the ledger
    convention core#714 ships. 0 = free."""
    if price_usd in (None, 0):
        return "free"
    return f"${int(price_usd) / 1_000_000:.2f}"


def _price_to_micro(price_usd) -> int:
    """CLI --price is human USD; the registry field is µUSD (1 cent = 10,000,
    per core#714's ledger convention). E2E on 2026-10-10 caught the mismatch:
    `--price 2` was sent as the raw integer 2 and rendered back as $0.00.
    Convert at the boundary so the wire unit is always µUSD."""
    return int(round(float(price_usd) * 1_000_000))


def cmd_skill_info(args) -> int:
    cfg = runtime(args)
    client = gated_client(cfg)
    detail = client.get_skill(args.skill_id)
    out = dict(detail)
    out["price_display"] = _display_price(detail.get("price_usd"))
    if args.versions:
        out["versions"] = client.list_skill_versions(args.skill_id)
    if args.fork_tree:
        out["fork_tree"] = client.skill_fork_tree(args.skill_id)
    emit(out)
    return EXIT_OK


# ---------------------------------------------------------------------------
# lifecycle commands (#56 second half: publish / fork / clone — core #723)
# ---------------------------------------------------------------------------
_CREATOR_SCOPE = DEFAULT_SCOPE + ("skills:write",)

LANES = ("short_video", "music_video", "drama", "character", "asmr", "image_series", "general")
PURPOSES = ("create", "enhance", "transform", "utility")


def _creator_client(cfg):
    """The publish/fork client: default scope + skills:write (creator face).

    skills:write is deliberately NOT in DEFAULT_SCOPE — agent tools never
    grow catalog-write rights by accident; the creator commands opt in
    explicitly, mirroring the server-side preset split (#723 review C1
    landed the same decision server-side)."""
    from beehive import client_for
    b = cfg.beehive
    if not b.has_api_key:
        raise ConfigError("publish/fork need an AK/SK credential (creator face) — "
                          "run `skilyst login` first")
    return client_for(b.access_key, b.secret_key, b.user, b.base_url, scope=_CREATOR_SCOPE)


def _skeleton_counts(package) -> dict:
    """v0.3 skeleton summary for the create payload — counts, not the body
    (the registry stores SkeletonSummary; the full skeleton stays in the
    package, served by preview once the taxonomy contract lands)."""
    skel = package.skeleton
    if skel is None:
        return {"pinned": 0, "parameterized": 0, "free_zones": 0}
    return {"pinned": sum(1 for n in skel.nodes if n.freedom == "pinned"),
            "parameterized": sum(1 for n in skel.nodes if n.freedom == "parameterized"),
            "free_zones": len(skel.free_zones)}


def cmd_publish(args) -> int:
    cfg = runtime(args)
    package = load_package(Path(args.dir).resolve())
    if package.degraded:
        emit({"refused": "community packages (no manifest.json) cannot be published — "
                         "publish is the master-library creator face (closed ecosystem)"},
             stream=sys.stderr)
        return EXIT_REFUSED
    if args.lane not in LANES or args.purpose not in PURPOSES:
        emit({"refused": f"taxonomy required (master-library contract #719 §2.3): "
                         f"--lane in {LANES}; --purpose in {PURPOSES}",
              "lane": args.lane, "purpose": args.purpose}, stream=sys.stderr)
        return EXIT_REFUSED
    client = _creator_client(cfg)
    body = {"slug": args.slug or package.community.name,
            "price_usd": _price_to_micro(args.price),
            "skeleton": _skeleton_counts(package),
            # taxonomy per #719: author-declared, required. The server
            # ignores unknown fields until its taxonomy column lands —
            # a payload superset is forward-compatible by design.
            "taxonomy": {"lane": args.lane, "purpose": args.purpose}}
    view = client._payload("POST", "/api/v1/skills", body, ok=(200, 201))
    emit({"published": view.get("id"), "slug": view.get("slug"),
          "version": view.get("version"), "price_usd": view.get("price_usd"),
          "skeleton": view.get("skeleton_summary"),
          "note": "the registry stores metadata (slug/skeleton counts/taxonomy); the "
                  "package content itself travels the distribution channel when it "
                  "lands (tracked on #56)"})
    return EXIT_OK


def cmd_fork(args) -> int:
    cfg = runtime(args)
    if not args.slug:
        emit({"refused": "fork requires --slug: the fork is a NEW skill with its own manifest"},
             stream=sys.stderr)
        return EXIT_REFUSED
    client = _creator_client(cfg)
    parent = client.get_skill(args.skill_id)
    body = {"slug": args.slug, "price_usd": _price_to_micro(args.price),
            "skeleton": {"pinned": 0, "parameterized": 0, "free_zones": 0}}
    view = client._payload("POST", f"/api/v1/skills/{args.skill_id}/fork", body, ok=(200, 201))
    emit({"forked": view.get("id"), "upstream_id": parent.get("id"),
          "upstream_slug": parent.get("slug"),
          "fork_depth": view.get("fork_depth"),
          "attribution": view.get("attribution"),
          "note": "fork registers the derivative intent server-side (free; attribution "
                  "inherited with you appended) — author your diverged package locally "
                  "and publish it as the fork's next version when ready"})
    return EXIT_OK


def cmd_clone(args) -> int:
    cfg = runtime(args)
    client = gated_client(cfg)
    detail = client.get_skill(args.skill_id)
    price = detail.get("price_usd") or 0
    if price > 0:
        emit({"refused": f"skill {args.skill_id} is PAID ({_display_price(price)}) — "
                         "acquire (purchase) flows are not in the CLI yet; use the "
                         "desktop library UI", "price_usd": price},
             stream=sys.stderr)
        return EXIT_REFUSED
    emit({"cloned": args.skill_id, "slug": detail.get("slug"),
          "version": detail.get("version"), "digest": detail.get("content_digest"),
          "skeleton": detail.get("skeleton_summary")})
    # The download leg (#730 contract, registry-mediated): the server serves
    # the package + its declared digest; skills lacking a published package
    # answer 404 — that is a contract state, not an error to hide. Requesting
    # the download is the natural follow-up; failure surfaces loudly.
    if not args.no_download:
        try:
            body, declared = client.download_skill_package(args.skill_id)
            emit({"package_bytes": len(body), "declared_digest": declared,
                  "digest_verified": _digest_matches(body, declared)})
        except Exception as exc:  # loud, structured — never swallow
            emit({"package_download": f"unavailable: {exc}",
                  "hint": "the registry may not have a package published for this "
                          "skill yet (404) — publish the package content via the "
                          "distribution channel (core#730 rollout)"},
                 stream=sys.stderr)
    return EXIT_OK


def _digest_matches(body: bytes, declared: str) -> bool | None:
    """Client-side verify of the two-sided trust chain (#730): sha256 of the
    package BYTES vs the digest the server declares for them.

    Digest semantics, kept honest (mirrors the core review on PR #740):
    - 64-hex ('sha256-<64>') — a real package-body digest: compare whole.
    - 16-hex ('sha256-<16>') — the manifest content-digest (#723 semantics:
      hash of manifest+skeleton, truncated). It is NOT a body hash and can
      NEVER verify package bytes — prefix-comparing it was wrong (my
      first cut did that; a manifest hash never equals a body sha256).
    - 'fork-of:sha256-<hex>' — a fork reference snapshot, not this body.

    Returns True/False when the declared digest is a verifiable body
    digest, None when it is a manifest/identity digest (verification not
    applicable — the package's own body digest arrives with core#740's
    package_digest in storage.location). Callers surface the distinction
    instead of collapsing it into a misleading False."""
    import hashlib
    import re as _re
    m = _re.search(r"sha256[-:]?([0-9a-f]+)", str(declared))
    if not m:
        return False
    hexpart = m.group(1)
    if len(hexpart) != 64:
        return None  # manifest/identity digest — not a body hash
    actual = hashlib.sha256(body).hexdigest()
    return actual == hexpart


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
    p = sub.add_parser("library")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--visibility", default="", choices=["", "public", "unlisted"],
                   help="filter (default: public + your unlisted)")
    p.set_defaults(func=cmd_library, needs_llm=False)
    p = sub.add_parser("skill-info"); p.add_argument("skill_id")
    p.add_argument("--versions", action="store_true", help="include the version history")
    p.add_argument("--fork-tree", action="store_true", dest="fork_tree",
                   help="include the fork tree")
    p.set_defaults(func=cmd_skill_info, needs_llm=False)
    p = sub.add_parser("publish"); p.add_argument("dir")
    p.add_argument("--slug", default=None, help="registry slug (default: package name)")
    p.add_argument("--price", default=0, help="buyout price in USD (default 0 = free)")
    p.add_argument("--lane", default="", required=True,
                   help=f"taxonomy lane: one of {LANES}")
    p.add_argument("--purpose", default="", required=True,
                   help=f"taxonomy purpose: one of {PURPOSES}")
    p.set_defaults(func=cmd_publish, needs_llm=False)
    p = sub.add_parser("fork"); p.add_argument("skill_id")
    p.add_argument("--slug", default=None, required=True, help="the fork's own slug")
    p.add_argument("--price", default=0, help="fork price in USD (default 0 = free)")
    p.set_defaults(func=cmd_fork, needs_llm=False)
    p = sub.add_parser("clone"); p.add_argument("skill_id")
    p.add_argument("--no-download", dest="no_download", action="store_true",
                   help="record the acquisition without fetching the package "
                        "(skip the registry-mediated download leg)")
    p.set_defaults(func=cmd_clone, needs_llm=False)
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
    p.add_argument("--max-jobs", type=int, default=None,
                   help=f"paid jobs this run may submit (default: ${MAX_JOBS_ENV}, else "
                        f"{DEFAULT_MAX_JOBS_ONE_SHOT} for a one-shot run)")
    p.add_argument("--session", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("chat")
    p.add_argument("--skill", default=None, help="activate a skill for this session")
    p.add_argument("--message", default=None, help="one-shot message instead of a REPL")
    p.add_argument("--max-turns", type=int, default=8); p.add_argument("--no-stream", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--session", default=None)
    p.add_argument("--allow-fallback", action="store_true")
    p.add_argument("--max-jobs", type=int, default=None,
                   help=f"paid jobs this session may submit (default: ${MAX_JOBS_ENV}, else "
                        f"{DEFAULT_MAX_JOBS_ONE_SHOT} for --message and "
                        f"{DEFAULT_MAX_JOBS_INTERACTIVE} for an interactive session)")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("sessions"); p.set_defaults(func=cmd_sessions, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765, help="0 picks a free port (the ready line reports it)")
    p.add_argument("--token", default="", help="pin the bearer token (default: generated per process)")
    p.add_argument("--token-file", default=None, help="also write the token here (mode 0600)")
    p.add_argument("--skill", default=None, help="activate a skill for every session this server runs")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="refuse paid jobs (default)")
    p.add_argument("--live", dest="dry_run", action="store_false",
                   help="allow paid jobs -- an explicit operator decision")
    p.add_argument("--max-turns", type=int, default=8); p.add_argument("--max-jobs", type=int, default=None,
                                                                      help=f"paid jobs per message (default: "
                                                                           f"${MAX_JOBS_ENV}, else "
                                                                           f"{DEFAULT_MAX_JOBS_INTERACTIVE}")
    p.add_argument("--allow-fallback", action="store_true")
    p.add_argument("--orphan-guard", action="store_true",
                   help="stop when the parent that started us is gone (stdin EOF or a parent pid "
                        "change) -- the desktop shell uses this so a killed shell cannot leave an "
                        "orphaned runtime behind")
    p.add_argument("--cors-origin", action="append", default=[],
                   help="an extra origin allowed by CORS (repeatable) -- e.g. the web console "
                        "dev server when it drives this runtime directly "
                        "(http://localhost:1421)")
    p.set_defaults(func=cmd_serve, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("doctor"); p.add_argument("skill_id", nargs="?")
    p.add_argument("--allow-fallback", action="store_true")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("config"); p.set_defaults(func=cmd_config, needs_llm=False, needs_beehive=False)

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

    p = sub.add_parser("login")
    p.set_defaults(func=cmd_login, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("logout")
    p.set_defaults(func=cmd_logout, needs_llm=False, needs_beehive=False)
    p = sub.add_parser("whoami")
    p.set_defaults(func=cmd_whoami, needs_llm=False, needs_beehive=False)
    return parser


def cmd_login(args) -> int:
    from auth import run_cli_login, AuthError
    print("Opening browser for Skilyst login (web console account)...")
    try:
        rec = run_cli_login()
    except AuthError as e:
        print(f"login failed: {e}")
        return 2
    print(f"Logged in as {rec.account_name or rec.account_uid} "
          f"(scopes: {', '.join(rec.scopes)}; storage: {rec.storage})")
    return 0


def cmd_logout(args) -> int:
    from auth import AuthFlow
    AuthFlow().logout()
    print("Logged out.")
    return 0


def cmd_whoami(args) -> int:
    from auth import AuthFlow
    rec = AuthFlow().current()
    if rec is None:
        print("Not logged in.")
        return 1
    import time as _t
    remaining = int(rec.expires_at - _t.time())
    print(f"account: {rec.account_name or rec.account_uid}\n"
          f"scopes:  {', '.join(rec.scopes)}\n"
          f"storage: {rec.storage}\n"
          f"token expires in: {remaining // 3600}h{(remaining % 3600) // 60}m")
    return 0


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
