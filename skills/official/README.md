# Official skills bundle (`skilyst-official`)

The skills an agent gets **preloaded at install time** — the supply chain's
smallest trusted surface (manifest draft §6.1/§6.4). Community skills are never
bundled here; they are installed from the market on demand.

## What is in it

| directory | skill_id | version | nodes it needs |
| --- | --- | --- | --- |
| `doctor/` | `skilyst/doctor` | 1.0.0 | — (environment self-check) |
| `embed-video/` | `skilyst/embed-video` | 1.0.0 | `generate:minimax-h3` (req), `generate:nb2-image` (opt) |
| `lipsync-audio-refs/` | `skilyst/lipsync-audio-refs` | 1.0.0 | `generate:tts-minimax-hd` (req), `generate:minimax-h3` (req) |
| `prompt-craft/` | `skilyst/prompt-craft` | 1.0.0 | — (pure methodology) |
| `video-15s/` | `skilyst/video-15s` | 1.1.0 | `generate:minimax-h3` (req) |

Every package carries the dual-file pair: `SKILL.md` (agentskills.io community
subset only) plus the `manifest.json` sidecar (v0.2 structured blocks).

## The two bundle manifests

- **`index.json` — the bundle manifest (authoritative).** One entry per skill with
  `skill_id` / `version` / `kind` / `content_digest` / `tree_digest`, so a registry
  or reviewer can see the exact content coordinates of what ships without
  unpacking every package. The preloader reads this file and verifies each entry's
  digest against the package on disk before installing anything.
- **`bundle.json` — the install view (derived).** Same skills in the older
  installer shape (`path` + `version` + digest), for tooling that already reads it.
  When both files exist they must agree, otherwise the preload refuses the bundle
  instead of guessing which one is right.

Both are written **only** by the packer, so they cannot drift:

```sh
python3 tools/pack_official_bundle.py          # refresh derived fields + both manifests
python3 tools/pack_official_bundle.py --check   # CI: exit 1 on any drift, writes nothing
```

The packer also records the two content-derived fields inside each package's own
sidecar: `content_digest` (sha256 over everything except `manifest.json`) and
`supply_chain.signatures[].digest`. The signature value itself stays
`platform-signature-pending` until the key ceremony lands — an obviously pending
placeholder beats a fake signature.

Edit a package by hand, then forget to re-pack, and the loader will refuse it:
the declared digest no longer matches the content. That is the intended failure.

## Preload

```sh
./bin/skilyst preload skills/official     # installs every entry of index.json
./bin/skilyst list                        # what the agent can activate
./bin/skilyst doctor skilyst/embed-video  # integrity + live node preflight
```

A bundle entry must be `kind: official-bundle` and must carry a platform
signature entry; the preloader refuses anything else rather than silently
installing an unsigned skill.
