# ADR 0001 -- Runtime layout and the rules the agent loop enforces

Status: accepted (A1 phase 1, 2026-09-23). Supersedes the flat PoC layout in `poc-path-a/`.

## Context

T1 chose path A (build the runtime ourselves) on measured evidence: 1,264 lines of PoC
code passed the three acceptance lines while the "fork a mature runtime" path required
carrying ~1.3M lines of Python and 13k changed files per 60 days. A1 turns that PoC into
the product's core, so the layout and the invariants have to be explicit -- the PoC was
allowed to be a script, the product is not.

## Decision

### 1. Flat top-level packages under `src/`

```
src/skills/    package loader: frontmatter, sidecar manifest, digests, store
src/manifest/  manifest spec: validation, hot/cold rules, node requirements
src/beehive/   scope gate + platform API client
src/sandbox/   declared-permission enforcement + resource resolution
src/llm/       OpenAI-compatible chat client + model router
src/session/   conversation store (transcript, trace, artifacts)
src/agent/     prompt assembly, tool registry, the tool-calling loop
src/cli.py     the command surface
src/config.py  credential/config resolution
src/errors.py  one error hierarchy for every layer
```

The repo skeleton already declared `src/{skills,beehive,sandbox,llm}`; the rest follows
that shape. No install step is needed in development (`./bin/skilyst`, or
`PYTHONPATH=src python3 -m cli`), and `pyproject.toml` exists so the desktop shell can
bundle the same modules later.

Dependencies: standard library only. The runtime is the thing we ship inside a Tauri
shell, so every third-party dependency is a packaging problem on three platforms, and
the surface we actually use (chat completions, HMAC signing, JSONL) is small.

### 2. Two integrity digests, not one

`content_digest` hashes the package content (every file except `manifest.json`); it is
what the manifest declares and what the loader re-verifies on every load. `tree_digest`
hashes every file including the sidecar, and the store records it as an install receipt.

The split exists because of the T1 defect: a digest that hashes its own carrier can never
be written into that carrier. Excluding the sidecar makes the declared field writable;
the receipt is what catches a sidecar edit. Editing either `SKILL.md` or `manifest.json`
after install now raises `SkillMutationError` naming the changed paths.

### 3. `node_id`, not `node_type`

In the manifest, `requires.nodes[].node_id` names the node *definition* (`generate:minimax-h3`).
The platform registry's `node_type` field means the workflow node type (`generate`), which
is why matching requirements on it silently blocked every skill in T1. Pre-flight matches
registry `id`, cross-checks the derived node type, and refuses to run when the registry is
unreachable instead of guessing. The v0.1 spelling still loads, with a recorded deprecation
warning, so packages published against v0.1 do not break.

A missing *required* node is blocking even when the manifest declares an available
fallback: the fallback is a different model at a different price, so substituting it is a
user decision (`--allow-fallback`), never a runtime one.

### 4. Tool availability is manifest-driven

`read_skill` / `read_skill_file` / `list_skills` / `list_skill_files` are always available --
reading is what the runtime is for. The platform tools (`beehive_submit_job`,
`beehive_get_job`, `beehive_verify_artifact`, `beehive_list_assets`) are registered only
when a skill is active *and* declares both node requirements and `permission.secrets`. A
skill that declares it needs no credential never gets one, and a session with no active
skill cannot reach the platform at all.

`beehive_submit_job` derives the workflow node from the declared node id
(`generate:minimax-h3` -> `type=generate`, `provider=minimax-h3`), defaults
duration/resolution/ratio from the skill's `plan`, and refuses a node id the manifest does
not declare.

### 5. The agent never touches money (D3), enforced twice

* The scope gate refuses `admin/*` and `billing/*` paths client-side, before any HTTP
  request, for both AK/SK and bearer-authenticated clients -- `bypass_scope` exists only
  for the `authz-probe` diagnostic that measures what the *platform* enforces.
* Every run carries a job budget (`--max-jobs`, default 1). A second paid submission in
  the same run is refused with an explicit message. Server-side scope middleware does not
  exist yet (pv-beehive-core#585 T3), so these two guards are the only protection today.

### 6. Progressive disclosure, three layers

The system prompt carries one line per installed skill (`skill_id@version: description`).
Full instructions arrive only when the model calls `read_skill`; individual package files
only when it calls `read_skill_file`. Injecting full bodies up front is what makes a
runtime stop scaling at a handful of skills, so it is not what we do. The prompt also
carries the rules the platform depends on: report URLs and errors verbatim, never silently
change a render tier, and the credential cannot reach billing.

### 7. Failures are visible

`stop_reason` distinguishes `completed` from `max_turns` and `llm_error`; the CLI exits
non-zero on the latter two and the session status records which happened. Tool refusals go
back to the model as error text (with the refusing layer in the message) and into the
trace. Artifact URLs are HEAD-verified before the runtime tells the model a job succeeded,
because a job reporting `completed` is not proof that the file is downloadable.

### 8. Sessions are files

One directory per conversation: `session.json` (metadata, usage, status),
`messages.jsonl` (append-only, OpenAI-shaped), `trace.jsonl` (every LLM turn and tool
execution), `artifacts.json`. Metadata is rewritten atomically; a torn final JSONL line
from a hard crash is dropped without losing the rest of the transcript; a session is
resumable by id.

## Consequences

* The runtime can be tested offline: the chat client, the Beehive client and the artifact
  verifier all take injectable transports, so the 85-test suite runs in under a second and
  never touches the network.
* `poc-path-a/` is frozen historical evidence. `skills/official/` is the live copy of the
  official bundle (its `content_digest` is filled by `skilyst digest --write`).
* Streaming output goes to stderr and machine-readable JSON to stdout, so `skilyst run`
  can be piped.
