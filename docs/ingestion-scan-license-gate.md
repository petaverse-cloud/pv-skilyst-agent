# Ingestion Pipeline — Scan Rules & License Gate (Draft for security review)

- Issue: #67 (r:platform, by Wesley's 2026-10-10 closed-ecosystem ruling)
- Status: **design draft** — the rules and gates below are the platform half;
  skilyst-security reviews them (core#693 scope addition, planner comment
  2026-10-10) before any absorption tool lands.
- Audience: skilyst-security (review), planner (alignment), platform (impl).
- Related: #56 (lifecycle CLI, same family), #55 (skeleton — upgrade workbench
  depends on it), skills-as-product-core.md §2 (dual-file package, manifests).

## 0. Positioning

The master library is a closed content-commodity system (browse/clone/execute/
preview). GitHub/public source is an **internal raw-material import channel**,
not a user feature. Everything below is an internal ops tool; nothing here
enters the user command surface (`skilyst-admin absorb ...` is operator-only).

## 1. Absorption pipeline (end-to-end)

```
github:owner/repo#ref
   │
   1 FETCH       internal clone, shallow, pinned to commit sha
   2 SCAN        static scan rules (§2)          ── FAIL → reject + reason
   3 LICENSE     SPDX identification (§3)        ── FAIL → reject + reason
   4 SYNTHESIZE  manifest synthesis (§4)
   5 DIGEST      content_digest anchor
   6 STAGE       internal store (staging, not master library)
   7 REVIEW      human/AI-assisted queue (§5)    ── 50-100 curated head entries
   8 PUBLISH     promotion into the master library
```

Steps 2-3 are machine gates and the subject of this draft; steps 6-8 are
process (review queue) and only sketched here.

## 2. Static scan rules (machine gate 1)

Two scan layers, both run over the fetched tree BEFORE any manifest synthesis.

### 2.1 Executable-content scan (the sandbox boundary's teeth)

The absorbed package's `permission.exec` is **`none` by default and cannot be
widened by synthesis** — widening is a review-queue decision recorded on the
review record, never an automatic one. The scan enforces the declared surface:

| rule | detection | disposition |
|---|---|---|
| EX-1 | any `*.sh`, `*.py`, `*.js`, `*.ts`, executables (`chmod +x` or shebang) outside `references/` with `permission.exec=none` | reject (mismatch: content is executable, permission says none) |
| EX-2 | shebang-bearing file inside `references/` when `permission.exec=none` | mark `exec_content=true` on the review record — reviewer must delete the file or explicitly accept |
| EX-3 | `scripts-whitelist` permission in a synthesized manifest | **never synthesized** — synthesized manifests always carry `exec: none`; whitelist is a hand-authored-only surface |
| EX-4 | binary blobs (non-text, >64KB) anywhere | reject (no binary payload has ever been needed by a knowledge skill) |
| EX-5 | symlink escaping the package root | reject (path traversal) |
| EX-6 | `node_modules/`, `.git/`, build output dirs | strip + warn (hygiene, not rejection) |

Rationale: the sandbox (`src/sandbox/gate.py`) enforces permission at RUN
time; the absorption scan enforces the same boundary at INGEST time so a
mismatch never reaches the store. Run-time gate stays authoritative — ingest
scan is the first line, not a replacement.

### 2.2 Sensitive-information scan

| rule | detection | disposition |
|---|---|---|
| SI-1 | credential-shaped strings: `AK-`/`sk-`-prefixed tokens ≥20 chars, PEM blocks, `BEGIN ... PRIVATE KEY` | reject |
| SI-2 | `.env`, `credentials*`, `*secret*`, `*token*`, `*.pem`, `*.key` filenames | reject |
| SI-3 | hardcoded API endpoints with embedded keys (`https://user:pass@`) | reject |
| SI-4 | high-entropy strings (Shannon >4.5 over ≥32 chars, excluding known hashes our own format declares) | mark for review — false-positive-heavy, human eyes |

Detection corpus note: rules must be tested against a **canary corpus** (a
small fixture set with one known-bad package per rule) before the first real
absorption — never discover rule gaps on live imports.

### 2.3 Reuse of existing machinery

- `classify_resources` (sandbox/gate.py) already inventories in-package /
  remote / missing references — the scan consumes its inventory for EX/SI path
  enumeration rather than re-walking the tree.
- Manifest `supply_chain.static_scan` field already exists in the schema
  (`scanner`, `status`, `scanned_at`) — absorption writes the real scan result
  there (today's official bundles carry hand-stamped values).

## 3. License gate (machine gate 2)

SPDX identification, per Wesley's ruling: **no identified license → reject**.

| rule | disposition |
|---|---|
| LG-1 | LICENSE/LICENSE.md/COPYING present? absent → reject (no license = all-rights-reserved = cannot redistribute) |
| LG-2 | SPDX identifier resolvable (`licensee`-style heuristics: full-text match against the SPDX license list; confidence threshold) |
| LG-3 | Resolved SPDX **whitelist** (allow): MIT, Apache-2.0, BSD-2/3-Clause, CC0-1.0, CC-BY-4.0, Unlicense |
| LG-4 | Resolved but **not whitelisted** (e.g. GPL family, AGPL, unknown custom) → route to review queue with the SPDX id + match confidence; human decides (GPL infectivity vs. our closed-commodity model is a legal question, not a machine one) |
| LG-5 | Multi-licensing (LICENSE + LICENSE.alt / "or later" suffixes) → resolve to the most restrictive candidate for gate purposes; ambiguity → review |
| LG-6 | Synthesized manifest `license.spdx` carries the RESOLVED id (not what the repo claims); `license.file` points at the vendored license text |
| LG-7 | Frontmatter `license:` (community layer) must agree with the resolved id or the package carries a warning — community layer is untrusted input |

Boundary with security: rules above are platform's machine-gate draft; whether
the whitelist (LG-3) is complete and whether GPL-family should be flat-reject
instead of review-routed is **explicitly a security/legal review question**,
flagged as such.

## 4. Manifest synthesis

After both gates pass, the synthesizer emits:

```json
{
  "skill_id": "absorbed/<repo-short-name>",
  "kind": "knowledge",
  "upstream": null,
  "supply_chain": {
    "source": {
      "origin": "github",
      "repo": "owner/repo",
      "commit": "<pinned sha>"
    },
    "static_scan": { "status": "passed", "scanner": "skilyst-scanner@0.2", "scanned_at": "..." }
  },
  "permission": { "egress": ["<hosts the body actually references — from classify_resources>"],
                  "filesystem": "skill-dir", "exec": "none", "secrets": false },
  "license": { "spdx": "<resolved>", "file": "LICENSE" },
  "requires": { "nodes": [] }
}
```

Invariants (deliberate, matching the closed-ecosystem ruling):

- `kind=knowledge` — absorbed raw skills are knowledge only; upgrading to
  methodology (requires.nodes + workflow_skeleton + plan) is the upgrade
  workbench's job (#57 family), a separate, deliberate act.
- `upstream=null` declares "original" — absorbed GitHub content IS the
  original as far as our fork chain is concerned; provenance lives in
  `supply_chain.source`, not the fork chain.
- `exec: none` always (EX-3); `secrets: false` always (absorbed skills never
  hold credentials).
- `content_digest` anchored post-synthesis over the final staged tree.

## 5. Review queue (process sketch)

Staged packages await human/AI-assisted review: license compliance (LG-4/5
routes), scan flags (EX-2, SI-4 marks), quality screening, and any `exec`
widening request. Initial volume target: 50-100 curated head entries, no web
crawling. Review decisions are recorded on the review record with reviewer
identity — an audit trail, not a rubber stamp.

## 6. What security should specifically review

1. EX-1..EX-6 completeness: what executable-content shape is missing?
2. SI-1..SI-4: false-negative risk of the credential patterns; is the
   high-entropy threshold sane?
3. LG-3 whitelist: GPL-family policy (review-routed today) — keep or reject?
4. The synthesized `permission.egress` from `classify_resources`: is
   auto-deriving egress from body URLs acceptable, or should egress default
   to `none` + explicit review?
5. Canary corpus design (§2.3): sufficient before first live absorption?
