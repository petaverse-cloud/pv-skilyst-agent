# Skilyst Agent desktop shell (Tauri v2)

The GUI for the Skilyst agent runtime. React 19 + Mantine + Vite in the window,
Rust in `src-tauri/` owning one thing: the runtime **process**.

## Why a sidecar process

The runtime is Python (`../src/`, standard library only). Phase 1 does not embed it:

| option | why not (yet) |
|---|---|
| PyO3 / in-process | commits the runtime to a CPython ABI and turns every runtime crash into a dead window |
| PyInstaller sidecar | one build per platform, and the first thing that breaks when the runtime gains a dependency |
| **spawn `skilyst serve`** | no embedding, the two lifecycles stay independent, and the same code path the CLI uses is exercised |

So the shell runs `python3 ../bin/skilyst serve --port 0 --dry-run`, reads the single
**ready line** it prints on stdout (`{"event":"ready","port":…,"token":…}`), and the
frontend then talks HTTP to `http://127.0.0.1:<port>` with that bearer token. The
contract is documented in `../src/serve.py`.

Stop is deliberately two steps: the UI calls `POST /shutdown` (graceful) and then
`runtime_stop` (reap the process). Closing the window also stops the runtime, and if the
shell is killed outright the runtime stops itself: it is started with `--orphan-guard`,
which watches its stdin for EOF and (on POSIX) its parent pid, so a killed shell cannot
leave an orphaned agent behind. Both signals exist because the stdin pipe alone did not
fire in the real shell — `docs/a2-verification.md` records what was observed.

## Run it

```bash
cd desktop
npm install
npm run tauri:dev          # builds the Rust shell, starts vite, opens the window
```

`npm run tauri dev` works too (the `tauri` script is an alias for the CLI).

The shell needs a runtime checkout: it looks at `$SKILYST_LAUNCHER`, then a bundled
copy in the app resources (phase 2), then the sibling checkout this repo is. Python is
`$SKILYST_PYTHON`, `python3`, or `python` on Windows. The runtime needs credentials in
`~/.skilyst/env` (see the runtime README) — without them the window opens, the session
list works, and the first message reports the exact missing variable instead of
pretending to answer.

Two smoke scripts prove the wiring without a human at the keyboard:

```bash
tools/desktop_smoke.sh              # window up -> runtime spawned -> /sessions fetched -> nothing orphaned
PLAYWRIGHT_CORE_ROOT=~/some/install node desktop/scripts/ui-smoke.mjs http://localhost:1420 "hello"
```

`ui-smoke.mjs` drives the same frontend in Chrome (type, send, read the transcript out of
the DOM) because the Tauri window cannot be scripted from a headless agent; it needs
`playwright-core` from any project that has it, plus a dev server started with
`VITE_SKILYST_RUNTIME` (below).

Browser-only development (no Rust, no window):

```bash
cd desktop && npm run dev
VITE_SKILYST_RUNTIME=http://127.0.0.1:8765#<token>   # a runtime you started yourself
```

## What is in the window (phase 1 = skeleton)

- **Session list** (left): `GET /sessions`, i.e. `~/.skilyst/sessions/*/session.json`.
- **Conversation**: `GET /session/<id>` → the `messages.jsonl` transcript, plus
  artifacts and a progress panel.
- **Composer**: streams a turn (`POST /message` with `stream: true`) — model text
  arrives as `delta` events, tool/progress lines as `note` events, and the run summary
  as `done`. A failed run shows `ok: false` with the runtime's own error text.
- **Settings**: the runtime's resolved model routing and credential status (redacted
  `GET /config`, `GET /doctor`) and a per-message model override. The shell never
  stores or writes credentials — there is exactly one place that holds them, and it is
  the runtime's env file.

Not in phase 1, on purpose: markdown rendering, workflow/asset browsers, sandbox
approval prompts, the updater (`tauri-plugin-updater` needs a signing key pair —
`TAURI_SIGNING_PRIVATE_KEY` + the pubkey in `tauri.conf.json`; that is a credential
decision, so it is a separate change), and bundling the Python runtime into the app.

## Icons

```bash
npm run icons        # scripts/make_icons.py -> src-tauri/icons/ (PNG + .icns + .ico)
```

The placeholder mark is drawn in code with the standard library only, so the binaries
are regenerable. Tauri requires RGBA PNGs (colour type 6) — the script writes those.

## Packaging and the Apple signing chain

`tauri.conf.json` carries no identity: Tauri v2 reads it from the environment, so the
same file builds an unsigned local app and a signed CI release. The Pawly Studio chain
this repo follows:

| variable | value / source |
|---|---|
| `APPLE_SIGNING_IDENTITY` | `Developer ID Application: … (54MY86G6NP)` |
| `APPLE_TEAM_ID` | `54MY86G6NP` |
| `APPLE_CERTIFICATE` | base64 of the `.p12` — **CI secret**, never in the repo |
| `APPLE_CERTIFICATE_PASSWORD` | CI secret |
| `KEYCHAIN_PASSWORD` | CI secret (throwaway keychain) |
| `APPLE_ID` / `APPLE_PASSWORD` | notarization; `APPLE_PASSWORD` is an app-specific password |

`npm run tauri:build` with none of them set still produces a working unsigned bundle —
that is the local path. CI (`.github/workflows/ci.yml`) currently builds Linux only
(`--no-bundle`, a smoke compile): the three-platform matrix belongs with the release
workflow, where the secrets exist. Local macOS build:

```bash
npm run tauri:build            # .app/.dmg under src-tauri/target/release/bundle
```

Note `cargo build` alone needs `dist/` to exist (`npm run build` first) — the Tauri
build script embeds the frontend at compile time.

### macOS: what actually builds where (measured 2026-09-24, Apple Silicon)

| step | unsigned local build | note |
|---|---|---|
| `npm run build` (tsc + vite) | ✅ | |
| `cargo build --release` + `.app` | ✅ | `--bundles app` is enough to get a runnable bundle |
| `.dmg` (`bundle_dmg.sh`) | ⚠️ needs an interactive session | the script drives Finder by AppleScript to lay out the volume; from a non-GUI/automation-restricted shell that AppleScript times out (`-1712`) and the dmg step fails *after* the `.app` was produced |

So a headless/local smoke is `npm run tauri:build -- --bundles app`, and the dmg is a
job for an interactive session or CI (which also has the notarization secrets). The
unsigned `.app` runs on the build machine (no quarantine: it was never downloaded) and
starts its runtime exactly like the dev shell — it is the artifact to click when
verifying a packaging change on macOS.
