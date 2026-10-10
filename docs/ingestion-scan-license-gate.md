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
review record, never an automatic one. The scan enforces the declared surface.

**Detection is content-sniffing FIRST; file extensions are only an
acceleration path** (security review R2, 2026-10-10): an extension tells us
where to look fast, but a polyglot (script content inside `.md`/`.txt`, no
shebang) is caught by content, not by name.

| rule | detection | disposition |
|---|---|---|
| EX-1 | **content-sniffed** executable file anywhere in the package: shebang (`#!`) in the first line, `chmod +x` mode bit, OR script-shaped content (parseable as shell/python source with executable semantics — the polyglot case) — regardless of extension. Extension list used only for scan prioritization: `.sh` `.bash` `.zsh` `.ps1` `.bat` `.cmd` `.rb` `.pl` `.lua` `.py` `.js` `.ts`, plus JVM/native containers `jar` `class` `wasm` | outside `references/` + `permission.exec=none` → reject (mismatch: content is executable, permission says none) |
| EX-2 | any sniffed-executable file inside `references/` when `permission.exec=none` | mark `exec_content=true` on the review record — reviewer must delete the file or explicitly accept |
| EX-3 | `scripts-whitelist` permission in a synthesized manifest | **never synthesized** — synthesized manifests always carry `exec: none`; whitelist is a hand-authored-only surface |
| EX-4 | binary blobs (non-text, >64KB) anywhere | reject (no binary payload has ever been needed by a knowledge skill) |
| EX-5 | symlink chains escaping the package root (a→b→escape, resolved fully) | reject (path traversal) |
| EX-6 | `node_modules/`, `.git/`, build output dirs | strip + warn (hygiene, not rejection) |

Rationale: the sandbox (`src/sandbox/gate.py`) enforces permission at RUN
time; the absorption scan enforces the same boundary at INGEST time so a
mismatch never reaches the store. Run-time gate stays authoritative — ingest
scan is the first line, not a replacement.

### 2.1b Prompt-injection scan (PI — security review R1, 2026-10-10)

Absorbed markdown is read BY THE AGENT AS INSTRUCTIONS — community content
differs from in-house content precisely here. A third rule class, marked for
review (never auto-reject: patterns are false-positive-heavy and the package
is not yet executable at ingest):

| rule | detection | disposition |
|---|---|---|
| PI-1 | instruction-injection patterns in any text asset (SKILL.md, references/*.md, prompts): role-play overrides ("ignore previous instructions", "you are now"), privilege-escalation asks ("change your permission", "enable exec", "run this"), fetch-then-extract chains (an instruction that sends fetched content to an external URL) | mark `injection_suspect=true` + matched excerpts on the review record; reviewer must clear it explicitly before PUBLISH |
| PI-2 | hidden-control-character content: zero-width/unicode homoglyph sequences inside instruction-looking lines (bypass attempts against PI-1 matching) | mark for review (same flow) |

### 2.2 Sensitive-information scan (R3: gitleaks ruleset, not hand-written)

The SI layer **reuses the gitleaks ruleset** — the same rules all 8 repos run
in CI — plus a custom allowlist, instead of hand-written patterns (security
review R2/Q2: hand-written AK-/sk- shapes are our own token forms; GitHub
source carries `ghp_`/`gho_`/`github_pat_`, `AKIA`, `xox`, `eyJ`, and more;
gitleaks already knows all of them and is maintained).

| rule | detection | disposition |
|---|---|---|
| SI-1 | **gitleaks ruleset** (default rules + custom allowlist for known-good patterns like our own digest forms) | any finding → reject |
| SI-2 | `.env`, `credentials*`, `*secret*`, `*token*`, `*.pem`, `*.key` filenames | reject |
| SI-3 | hardcoded API endpoints with embedded keys (`https://user:pass@`) | reject (covered by gitleaks rules; kept as an explicit contract line) |
| SI-4 | high-entropy strings (Shannon >4.5 over ≥32 chars, excluding known hashes our own format declares) | mark for review — false-positive-heavy, human eyes. Retained from v0; the only hand-written heuristic that survives |

Detection corpus note: the **canary corpus** is CI-gated, not a one-off —
rules and corpus live in the same repo at the same version, every rule change
runs the corpus (security review Q5), and it includes adversarial samples
(polyglot files, unicode-homoglyph SI bypasses, symlink chains). A coverage
report (each rule: ≥1 true positive + ≥1 benign false-positive example) is
produced before the first live absorption.

### 2.3 Reuse of existing machinery

- `classify_resources` (sandbox/gate.py) inventories in-package / remote /
  missing references — used for **path enumeration only**. It does NOT feed
  `permission.egress` (R4: see §4).
- Manifest `supply_chain.static_scan` field already exists in the schema
  (`scanner`, `status`, `scanned_at`) — absorption writes the real scan result
  there (today's official bundles carry hand-stamped values).
- The gitleaks binary/ruleset already runs in all 8 repos' CI (R3): the SI
  layer is a library-level reuse of the same rules, versioned with the
  scanner.

## 3. License gate (machine gate 2)

SPDX identification, per Wesley's ruling: **no identified license → reject**.

| rule | disposition |
|---|---|
| LG-1 | LICENSE/LICENSE.md/COPYING present? absent → reject (no license = all-rights-reserved = cannot redistribute) |
| LG-2 | SPDX identifier resolvable (`licensee`-style heuristics: full-text match against the SPDX license list; confidence threshold) |
| LG-3 | Resolved SPDX **whitelist** (allow): MIT, Apache-2.0, BSD-2/3-Clause, CC0-1.0, CC-BY-4.0, Unlicense |
| LG-4 | Resolved but **not whitelisted** (GPL family, AGPL, unknown custom) → **flat-reject for v1** (security review Q3, accepted 2026-10-10: the initial 50-100 curated queue gets enough supply from the whitelist alone; a GPL exception channel opens only with legal input) |
| LG-5 | Multi-licensing (LICENSE + LICENSE.alt / "or later" suffixes) → resolve to the most restrictive candidate for gate purposes; ambiguity → reject in v1 (same flat-reject rationale) |
| LG-6 | Synthesized manifest `license.spdx` carries the RESOLVED id (not what the repo claims); `license.file` points at the vendored license text |
| LG-7 | Frontmatter `license:` (community layer) must agree with the resolved id or the package carries a warning — community layer is untrusted input |

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
  "permission": { "egress": "none",
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
- **`permission.egress: "none"` always — hosts are added ONLY by an explicit
  review decision** (R4, 2026-10-10). No automatic derivation from body URLs:
  a hostile repo author planting one URL in the skill body would otherwise
  legalize a runtime exfiltration channel at ingest time, and
  `classify_resources` only scans the markdown body — URLs can hide in
  config/plan data the classifier never reads. Egress widening = a review
  record entry naming the host and the reason, exactly like `exec` widening.
- FETCH records the tag→sha resolution into `supply_chain.source` (the
  `commit` field IS the resolved sha; a tag reference is stored alongside in
  `source.ref` when the operator referenced a tag) — download integrity is
  traceable to the exact tree.
- `content_digest` anchored post-synthesis over the final staged tree.

## 5. Review queue (process sketch)

Staged packages await human/AI-assisted review: license compliance (LG-4/5
rejects are terminal in v1 — no queue route), scan flags (EX-2, PI-1/PI-2,
SI-4 marks), quality screening, and any `exec`/`egress` widening request.
Initial volume target: 50-100 curated head entries, no web crawling. Review
decisions are recorded on the review record with reviewer identity — an
audit trail, not a rubber stamp.

**STAGE→REVIEW→PUBLISH ordering is enforced, not assumed**: the master
library's store write requires an approved review record as a precondition
(the write path refuses a package whose review record is missing or not
approved — no race between staging and review).

## 6. What security should specifically review (v1 draft — answered 2026-10-10)

1. EX-1..EX-6 completeness: what executable-content shape is missing?
2. SI-1..SI-4: false-negative risk of the credential patterns; is the
   high-entropy threshold sane?
3. LG-3 whitelist: GPL-family policy (review-routed today) — keep or reject?
4. The synthesized `permission.egress` from `classify_resources`: is
   auto-deriving egress from body URLs acceptable, or should egress default
   to `none` + explicit review?
5. Canary corpus design (§2.3): sufficient before first live absorption?

All five were answered by the security review of 2026-10-10
(REQUEST_CHANGES on PR #69 — full text on the PR). The answers are folded
into the sections above:

| question | answer | where |
|---|---|---|
| Q1 EX completeness | PI rule class added; content-sniffing first; extended lists | §2.1, §2.1b |
| Q2 SI false-negatives | gitleaks ruleset reuse + allowlist; SI-4 entropy retained | §2.2 |
| Q3 GPL policy | flat-reject for v1 | §3 LG-4/LG-5 |
| Q4 auto-egress | removed — egress `none` + review-added hosts only | §4 invariants |
| Q5 canary corpus | CI-gated, adversarial samples, coverage report | §2.2 corpus note |

**For re-review**: the four REQUIRED items are R1 (PI-1/PI-2 §2.1b), R2
(content-sniffing + extended lists §2.1), R3 (gitleaks §2.2), R4 (egress
`none` §4) — all addressed in this revision; the three non-blocking
suggestions (GPL flat-reject, tag→sha, STAGE precondition) are also folded
in (§3, §4, §5). @security please re-review for the APPROVE.
