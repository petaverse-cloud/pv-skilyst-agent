# A2 phase 1 verification -- desktop shell + serve mode + CI

Date: 2026-09-23 (UTC+8). Branch `platform/a2-desktop-shell`. Evidence: `evidence/a2-desktop/`,
`evidence/a2-serve/`.

Scope of this phase: a Tauri v2 shell that runs the A1 runtime and shows a session list, a
transcript and a composer; the loopback control plane in the runtime that makes that possible
(`skilyst serve`); and CI for both halves. Streaming markdown, workflow/asset browsers, sandbox
approval UX, the updater and bundling Python into the app are later phases.

## Architecture decision: sidecar process, not embedding

The runtime is Python; the shell is Rust/JS. Phase 1 spawns `skilyst serve` as a child process
and talks HTTP to it. Rejected for now: PyO3/in-process (commits the runtime to a CPython ABI
and turns a runtime crash into a dead window) and a PyInstaller sidecar (one build per platform,
and the first new dependency breaks it). The sidecar keeps the two lifecycles independent and
means the GUI exercises the same `agent.runner.open_run` path the CLI does -- verified by the
shared runner module, not by inspection.

## Acceptance lines

| # | Line | Result |
|---|------|--------|
| 1 | `npm run tauri dev` opens a window that connects to serve mode | PASS (`evidence/a2-desktop/desktop-smoke.log`) |
| 2 | A message sent from the UI reaches the agent loop and its answer renders | PASS (`evidence/a2-desktop/ui-smoke.log`, `ui-window.png`) |
| 3 | The shell does not outlive its runtime, and the runtime does not outlive the shell | PASS (orphan guard, `desktop-smoke.log`) |
| 4 | Runtime suite + shell unit tests + frontend build are green locally | PASS (122 python, 3 rust, `tsc && vite build`) |
| 5 | Secrets scan finds nothing (repo is public, history included) | PASS (`gitleaks detect` over 12 commits) |

### Line 1 -- window up, runtime connected

`tools/desktop_smoke.sh` (evidence `desktop-smoke.log`), on macOS arm64:

```
shell pid=80282  runtime pid=80463
[runtime] orphan guard: watching stdin for EOF
[runtime] http 127.0.0.1 "OPTIONS /sessions HTTP/1.1" 204 -
[runtime] http 127.0.0.1 "OPTIONS /sessions HTTP/1.1" 204 -
[runtime] http 127.0.0.1 "GET /sessions HTTP/1.1" 200 -
```

The shell spawned `python3 ../bin/skilyst serve --port 0 --orphan-guard --dry-run`, parsed the
one ready line (port + token), and the webview then fetched the session list over loopback --
`OPTIONS` is the CORS preflight from `tauri://localhost`, answered only for webview/dev origins.
The doubled requests are React StrictMode's double effect in development.

### Line 2 -- one message round-trip through the UI

The Tauri window cannot be scripted from a headless agent, so `desktop/scripts/ui-smoke.mjs`
drives the same frontend (same bundle, same runtime contract) in Chrome and reads the rendered
transcript back out of the DOM. Evidence `ui-smoke.log`:

```
app mounted; sessions in the sidebar before: 11
runtime badge: DRY RUN · PORT 8899
--- transcript (rendered) ---
You Say hello in three words.
Skilyst Hello there, friend.
sessions in the sidebar after: 12
error banners: 0
page problems: none
screenshot: ../evidence/a2-desktop/ui-window.png
```

The turn went out as `POST /message {"stream": true}`, the model text arrived as `delta` events
and the summary as `done` (the same contract `tools/serve_smoke.sh` shows at the HTTP level in
`evidence/a2-serve/message.sse`: 5 deltas + 1 done against the real model endpoint).

### Line 3 -- process ownership

Both directions are enforced and both were observed failing before they were fixed:

* **Window close**: `on_window_event(CloseRequested)` stops the runtime (`stop_child`).
* **Shell killed without cleanup** (SIGTERM/SIGKILL -- no handler runs): the runtime's
  `--orphan-guard` stops it. `desktop-smoke.log` shows the runtime exiting ~1s after the shell
  was SIGTERMed.
* **Runtime dies first**: `runtime_status` reports `null` once the child has exited, so the UI
  shows "not connected" instead of talking to a dead port.

The guard took two attempts, both driven by real observation rather than reasoning:

1. **stdin EOF alone did not fire in the shell.** The guard armed, the shell was SIGTERMed, no
   EOF arrived -- something in the shell's process tree keeps the pipe's write end open. A
   parent-pid watch (`os.getppid()` changing means reparented, i.e. the shell is gone) was added
   as the POSIX backstop; the stdin signal is kept because it is what covers Windows.
2. **The guard logged "the shell is gone" and then died on a broken pipe.** With the shell dead,
   the runtime's stderr has no reader, so `print` raises `BrokenPipeError` -- and that exception
   was thrown *before* `shutdown()`. Fixed by making every log call non-fatal and by shutting
   down before announcing. `tests/test_serve.py` has a regression test for exactly this
   (`test_a_broken_log_pipe_does_not_prevent_the_stop`), and reverting the fix makes it fail.

## Unit tests

| Suite | Command | Result |
|---|---|---|
| Runtime | `PYTHONPATH=src python3 -m unittest discover -s tests` | **122 tests, 4.7s, offline** (was 87) |
| Shell | `cargo test --manifest-path desktop/src-tauri/Cargo.toml` | 3 tests (ready-line parsing: real line, log noise, live mode) |
| Frontend | `cd desktop && npm run build` | `tsc` clean under `strict`, vite build 413KB js / 216KB css |

`tests/test_serve.py` drives real HTTP over loopback against a real `RuntimeAPI`, store and
session directory; only the model and the platform are stubbed (injected chat transport, the
same pattern as the other suites). Covered: token required on every route including `/health`,
redaction over the wire, session create/list/read/delete, one message turn end-to-end including
what lands in `messages.jsonl`, SSE delta/done/error, a failed run reported as `ok:false`,
credential refusal (409 `ConfigError` naming the variable), session-id traversal refusal,
CORS for webview origins only, connection reuse (a body is always drained), dry-run by default
and the per-message model override, the ready line, graceful shutdown, and both orphan-guard
signals.

## What the shell deliberately does not do yet

* **No credential input in the UI.** Settings shows the runtime's resolved routing and the
  doctor result; the single place that holds credentials stays `~/.skilyst/env`. Writing a key
  from the GUI is a separate change with its own review.
* **No updater.** `tauri-plugin-updater` needs a signing key pair (private key as a CI secret,
  pubkey in `tauri.conf.json`); the shell is unsigned locally and CI only smoke-builds Linux.
* **No bundled runtime.** The shell finds `$SKILYST_LAUNCHER`, a bundled copy in the app
  resources (the hook exists), or the development checkout. Shipping Python inside the app is
  its own decision (size, signing, update channel).
* **Three-platform matrix not wired.** `.github/workflows/ci.yml` runs the runtime tests,
  gitleaks and a Linux `tauri build --no-bundle` smoke on hosted runners (the shared
  `sea-aks-runner` is deliberately not used). macOS arm64/x64 with the Apple chain
  (Team 54MY86G6NP) and Windows belong to the release workflow, where the secrets exist.

## CI

`.github/workflows/ci.yml`, on push and pull request to `main`:

* `runtime-tests` -- `PYTHONPATH=src python3 -m unittest discover -s tests`.
* `secrets-scan` -- the gitleaks binary (8.24.3), `detect --source . --redact --exit-code 1`,
  full history; the same rule set as pv-beehive-core's PR gates. Local run over the branch:
  `no leaks found`.
* `desktop-build` -- node 22 + rust stable + webview deps, `npm ci`, `npm run build`,
  `cargo test`, `cargo build`, `npx tauri build --no-bundle`.
