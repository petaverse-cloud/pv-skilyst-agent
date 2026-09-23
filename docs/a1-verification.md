# A1 phase 1 verification -- baseline migration + agent loop + CLI

Date: 2026-09-23 (UTC+8). Branch `platform/a1-runtime-mvp`. Evidence: `evidence/a1-phase1/`.

Scope of this phase: move the T1 path-A baseline into the repo layout with its two known
defects fixed, build the smallest real agent loop on top of it (session store, LLM loop with
tool calling, progressive-disclosure prompt), and prove the whole chain through one CLI
command. GUI/desktop shell is A2; streaming UI, sub-agents, approvals and the update channel
are later phases.

## Acceptance lines (re-run, not re-quoted)

| # | Line | Result |
|---|------|--------|
| 1 | Community skill packages load zero-modification, every reference resolves under the declared egress policy | PASS |
| 2 | One command -> 15s video through the dev API (submit -> poll -> artifact URL), artifact verified | PASS |
| 3 | Restricted key against admin/billing: client-side gate + real server status | PASS |

### Line 1 -- format ingestion + offline behaviour

`tools/verify_line1.py` (evidence `line1.json`):

* `anthropics/skills` `pdf` -- 12 files, **0 files changed** by inventory+load (sha256 per file
  before/after), loaded in degraded/compat mode, `skill_id=pdf`, no spec warnings.
* `Emily2040/seedance-2.0` -- 338 files, **0 files changed**, degraded/compat mode with the
  recorded warning `name 'seedance-20' != directory name 'seedance-2.0'`, 67 in-package
  references indexed.
* Reference resolution: `pdf/reference.md` read 16,692 bytes from disk and
  `seedance/references/directing-engine.md` read 22,925 bytes from disk -- both byte-identical
  to T1's recorded numbers, so the migration did not change resolution behaviour. A remote ref
  under `egress=none` is refused before fetch; a ref with egress granted but an unreachable host
  fails **loudly** (`OfflineError`), never silently skipped.
* Sandbox contract through the migrated gate: exec refused (`exec=none`), write outside the
  workspace refused, write inside the workspace allowed, credential refused for a skill that
  declares `secrets=false`, unknown egress host refused.

### Line 2 -- end-to-end production line through the agent loop

One command, one paid job (`--max-jobs` default 1):

```
./bin/skilyst run skilyst/video-15s --request "做一条 15 秒竖版短视频：清晨的灯塔…"
```

* `stop_reason=completed`, 3 turns, model `deepseek/deepseek-v4.1-flash`, wall clock 435.3s.
* Tools the model chose: `read_skill` (layer 2) -> `write_workspace_file` (prompt draft in the
  run workspace) -> `beehive_submit_job` with `wait=true`.
* Job `job-1790157367916-027fd42bc72436de`, node `generate:minimax-h3`, `15s / 768P / 9:16`
  taken from the skill's declared plan -- the model did not change the tier.
* Artifact URL, reported verbatim:
  `https://pv-devops-blob.s3.ap-southeast-1.amazonaws.com/beehive-temp/beehive/job-1790157367916-027fd42bc72436de/node-job-1790157367916-027fd42bc72436de-0-1790157780166.mp4`
* Runtime-side check: HEAD -> HTTP 200, 2,563,359 bytes. Independent check (`curl` + `ffprobe`,
  `line2-ffprobe.json`): h264 768x1344 24fps + aac, **duration 15.084s**, 2,563,359 bytes.
* Session `20260923-175553-c9103d` recorded the artifact and 9,196 tokens over 3 LLM calls.

Cost of this phase: exactly one 15s render (~$1.035), the same cheapest acceptable tier T1 used.

### Line 3 -- restricted key

`authz-probe --bypass-gate` (evidence `line3-authz.json`), restricted AK/SK for `e2e-platform`
(uid 213508887911264, role=user), scope `jobs:write, jobs:read, assets:read`:

| probe | client gate | server (restricted key, gate bypassed) | anonymous |
|---|---|---|---|
| `GET/POST /api/v1/admin/users` | refused before HTTP | 403 | 401 |
| `PUT /api/v1/nodes/generate:drawnow` | refused before HTTP | 403 | 401 |
| `PUT /api/v1/billing/nodes/…/pricing` | refused before HTTP | 403 | 401 |
| `POST /api/v1/billing/wallet/{uid}/grant` | refused before HTTP | 403 | 401 |
| `GET /api/v1/billing/wallet` | refused before HTTP | **200** | 401 |
| `GET /api/v1/billing/history` | refused before HTTP | **200** | 401 |
| `GET /api/v1/jobs`, `/assets`, `/nodes` | 200 | 200 | 401 |

Unchanged from T1: **the server-side scope middleware does not exist yet** -- a non-admin token
can still read billing. The client-side gate (plus the new per-run job budget) is the only
thing protecting billing today; this is the T3 dependency, not a runtime gap we can close here.

## Unit tests

`PYTHONPATH=src python3 -m unittest discover -s tests` -> **85 tests, 0.85s, offline.**

* `tests/test_baseline.py` -- the 19 T1 tests, carried over to the migrated layout, plus the
  two defect classes they did not cover (digest self-reference / post-install mutation
  detection; `node_id` semantics and pre-flight matching).
* `tests/test_platform.py` -- scope gate (including that a bearer client is gated too and that
  a forbidden scope cannot be minted), Beehive client against a fake transport, artifact
  verification against a loopback HTTP server, LLM client/streaming/router fallback, config
  layering and redaction.
* `tests/test_agent.py` -- session store (append-only, resumable, torn-line recovery), prompt
  disclosure, manifest-driven tool availability, sandboxed writes, job budget, and the loop
  itself: tool call -> answer, refusal propagation, artifact recording, `max_turns` and
  `llm_error` stop reasons, malformed tool arguments.

## Defects fixed during the migration

1. **Nothing detected post-install mutation.** `content_digest` now excludes `manifest.json`
   (the field was self-referential), the manifest's declared digest is re-verified on every
   load, and the store records a per-file receipt at install. Editing `SKILL.md` *or* the
   sidecar now raises `SkillMutationError` naming the changed files.
2. **`node_type` in the manifest meant the node-definition id.** v0.2 spelling is `node_id`
   (v0.1 spelling still loads, with a deprecation warning). Pre-flight matches registry `id`,
   cross-checks the derived node type, and refuses to run blind when the registry is
   unreachable. Verified against the live registry: `generate:minimax-h3` matches and the
   skill is runnable.

## Not covered yet (next phases)

* GUI / Tauri shell (A2), and packaging the runtime as a bundled sidecar.
* Approvals/escalation UX for the `danger-full-access` sandbox tier; the tier contract is
  declared and enforced for the three dimensions in use today (`egress`, `filesystem`, `exec`,
  `secrets`) but there is no interactive escalation flow.
* `update skills` channel execution (hot/cold decision exists; applying an update does not).
* Server-side token scope middleware (pv-beehive-core#585 T3) -- the client gate is not a
  substitute for it.
* Multi-skill workflows, sub-agents, cron, streaming UI.
