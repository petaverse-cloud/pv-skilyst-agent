//! Skilyst Agent desktop shell: a Tauri window over the local Python runtime.
//!
//! The shell does not contain the agent. It owns the runtime *process*: it spawns
//! `skilyst serve` (see `src/serve.py` in the runtime repo), reads the single ready
//! line that reports the loopback port and the per-process token, and then the
//! frontend talks HTTP to that port. Three consequences worth stating out loud:
//!
//!   * the UI and the agent have independent lifecycles -- reloading the window does
//!     not restart a run, and a runtime crash is a reconnect, not a blank app;
//!   * phase 1 commits to no Python embedding (no PyO3 ABI, no PyInstaller build per
//!     platform): the runtime is found as a launcher script, and bundling it is a
//!     later, separate decision;
//!   * the token never reaches the webview's own storage in the shell's control path
//!     -- it is held in Rust state and handed to the frontend only to sign requests.
//!
//! Stop is two steps on purpose: the frontend calls `POST /shutdown` so the runtime
//! stops gracefully, and `runtime_stop` reaps the process (and is the backstop when
//! the runtime is already wedged).

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_deep_link::DeepLinkExt;

/// #25 (macOS dev only): point LaunchServices at the running debug binary for
/// the skilyst:// scheme. `tauri dev` runs a bare binary with no Info.plist, so
/// nothing else claims the scheme and browser auth callbacks die with
/// kLSApplicationNotFoundErr. LSSetDefaultHandlerForURLScheme alone is NOT
/// enough (verified live): LaunchServices refuses to route a URL to a bundle
/// id it has never seen — the binary must exist as a registered .app. We
/// shell out to scripts/dev-scheme-register.sh, which wraps the running
/// binary in a throwaway .app stub (symlinked executable + minimal
/// Info.plist with the skilyst scheme) and `lsregister -f`s it once.
/// Production bundles get the scheme from the real Info.plist and never run
/// this (debug_assertions-gated at the call site).
#[cfg(all(target_os = "macos", debug_assertions))]
fn register_macos_dev_scheme(app: &tauri::App) -> Result<(), String> {
    let manifest = std::env::var("CARGO_MANIFEST_DIR")
        .unwrap_or_else(|_| concat!(env!("CARGO_MANIFEST_DIR")).to_string());
    let script = std::path::Path::new(&manifest)
        .join("..")
        .join("scripts")
        .join("dev-scheme-register.sh");
    let binary = std::env::current_exe()
        .map_err(|exc| format!("current_exe: {exc}"))?;
    let output = std::process::Command::new(script)
        .arg(&binary)
        .arg(app.config().identifier.clone())
        .output()
        .map_err(|exc| format!("running dev-scheme-register.sh: {exc}"))?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!(
            "dev-scheme-register.sh failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ))
    }
}

/// How long to wait for the runtime's ready line before giving up on it.
const READY_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeInfo {
    pub port: u16,
    pub token: String,
    pub base_url: String,
    pub pid: u32,
    pub dry_run: bool,
    pub sessions_dir: String,
    pub store_dir: String,
    pub workspace_dir: String,
}

#[derive(Default)]
pub struct RuntimeState {
    child: Mutex<Option<Child>>,
    /// Held open for the runtime's lifetime: the runtime exits on stdin EOF, so this
    /// handle closing is what tells it the shell is gone.
    stdin: Mutex<Option<ChildStdin>>,
    info: Mutex<Option<RuntimeInfo>>,
}

fn field(value: &serde_json::Value, key: &str) -> String {
    value
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

/// Parse the one line `skilyst serve` prints on stdout when it is listening.
///
/// Returns `None` for anything that is not a ready line, so ordinary log output on
/// the same stream is ignored rather than misread.
fn parse_ready_line(line: &str) -> Option<RuntimeInfo> {
    let value: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    if value.get("event")?.as_str()? != "ready" {
        return None;
    }
    let port = value.get("port")?.as_u64()? as u16;
    Some(RuntimeInfo {
        port,
        token: value.get("token")?.as_str()?.to_string(),
        base_url: format!("http://127.0.0.1:{port}"),
        pid: value.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32,
        dry_run: value
            .get("dry_run")
            .and_then(|v| v.as_bool())
            .unwrap_or(true),
        sessions_dir: field(&value, "sessions_dir"),
        store_dir: field(&value, "store_dir"),
        workspace_dir: field(&value, "workspace_dir"),
    })
}

fn python_command() -> String {
    if let Ok(explicit) = std::env::var("SKILYST_PYTHON") {
        if !explicit.is_empty() {
            return explicit;
        }
    }
    if cfg!(windows) {
        "python".to_string()
    } else {
        "python3".to_string()
    }
}

/// Where the runtime lives, in priority order: an explicit override, the bundled
/// copy inside the app (phase 2), then the development checkout this shell was built
/// from. A missing launcher is an error, never a silent "no runtime".
fn launcher_path(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(explicit) = std::env::var("SKILYST_LAUNCHER") {
        let path = PathBuf::from(&explicit);
        if path.is_file() {
            return Ok(path);
        }
        return Err(format!(
            "SKILYST_LAUNCHER points at {explicit}, which is not a file"
        ));
    }
    if let Ok(resources) = app.path().resource_dir() {
        let bundled = resources.join("runtime").join("bin").join("skilyst");
        if bundled.is_file() {
            return Ok(bundled);
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("bin")
        .join("skilyst");
    if dev.is_file() {
        return Ok(dev.canonicalize().unwrap_or(dev));
    }
    Err(format!(
        "no runtime launcher found. Looked for a bundled runtime in the app resources and for \
         {} . Set SKILYST_LAUNCHER=/path/to/pv-skilyst-agent/bin/skilyst to point at a checkout.",
        dev.display()
    ))
}

fn stop_child(state: &RuntimeState) {
    state.stdin.lock().unwrap().take(); // EOF: the runtime stops itself
    if let Some(mut child) = state.child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    *state.info.lock().unwrap() = None;
}

#[tauri::command]
fn runtime_start(
    app: AppHandle,
    state: State<'_, RuntimeState>,
    live: Option<bool>,
) -> Result<RuntimeInfo, String> {
    if let Some(info) = state.info.lock().unwrap().clone() {
        return Ok(info);
    }
    let launcher = launcher_path(&app)?;
    let mut command = Command::new(python_command());
    command
        .arg(&launcher)
        .arg("serve")
        .arg("--port")
        .arg("0")
        // A piped stdin, held by the shell: if the shell dies without running its
        // cleanup (SIGKILL, crash), the pipe closes and the runtime stops itself
        // instead of living on as an orphan.
        .arg("--orphan-guard")
        .arg(if live.unwrap_or(false) {
            "--live"
        } else {
            "--dry-run"
        })
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().map_err(|exc| {
        format!(
            "failed to start the runtime ({} {}): {exc}",
            python_command(),
            launcher.display()
        )
    })?;

    let stdin = child
        .stdin
        .take()
        .ok_or("the runtime's stdin could not be piped")?;
    let stdout = child
        .stdout
        .take()
        .ok_or("the runtime produced no stdout")?;
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[runtime] {line}");
            }
        });
    }
    let (tx, rx) = mpsc::channel::<Result<RuntimeInfo, String>>();
    std::thread::spawn(move || {
        let mut announced = false;
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if !announced {
                if let Some(info) = parse_ready_line(&line) {
                    announced = true;
                    let _ = tx.send(Ok(info));
                    continue;
                }
            }
            eprintln!("[runtime] {line}");
        }
        if !announced {
            let _ = tx.send(Err(
                "the runtime exited before it reported ready".to_string()
            ));
        }
    });

    match rx.recv_timeout(READY_TIMEOUT) {
        Ok(Ok(info)) => {
            state.child.lock().unwrap().replace(child);
            state.stdin.lock().unwrap().replace(stdin);
            *state.info.lock().unwrap() = Some(info.clone());
            Ok(info)
        }
        Ok(Err(message)) => {
            let _ = child.kill();
            Err(message)
        }
        Err(_) => {
            let _ = child.kill();
            Err(format!(
                "the runtime did not report ready within {}s",
                READY_TIMEOUT.as_secs()
            ))
        }
    }
}

#[tauri::command]
fn runtime_stop(state: State<'_, RuntimeState>) -> Result<(), String> {
    stop_child(&state);
    Ok(())
}

/// Open the system browser at the login URL the runtime returned. The browser
/// step of the deep-link flow MUST happen outside the webview: the console
/// session lives in the user's browser, never in the app's own storage.
#[tauri::command]
fn shell_open(url: String) -> Result<(), String> {
    tauri_plugin_opener::open_url(url, None::<&str>).map_err(|exc| exc.to_string())
}

/// Extract the one-time code from a `petaverse.skilyst://callback?code=<one-time-code>`
/// or legacy `skilyst://callback?code=…` URL (transition window, #43: the
/// reverse-domain scheme is the RFC 8252 §7.1 compliant primary; the bare
/// word stays accepted until the beehive console drops it).
fn code_from_url(url: &tauri::Url) -> Option<String> {
    const LEGACY_SCHEME: &str = "skilyst";
    const PRIMARY_SCHEME: &str = "petaverse.skilyst";
    let scheme_ok = url.scheme() == PRIMARY_SCHEME || url.scheme() == LEGACY_SCHEME;
    if !scheme_ok || url.host_str() != Some("callback") {
        return None;
    }
    url.query_pairs()
        .find(|(key, _)| key == "code")
        .map(|(_, value)| value.to_string())
}

/// True when the given executable path sits inside a macOS `.app` bundle
/// (…/Something.app/Contents/MacOS/binary). Pure — unit-tested on every CI
/// platform, not just macOS.
#[cfg_attr(not(all(target_os = "macos", debug_assertions)), allow(dead_code))]
fn inside_app_bundle(exe: &std::path::Path) -> bool {
    exe.ancestors()
        .skip(1)
        .any(|ancestor| ancestor.extension().is_some_and(|ext| ext == "app"))
}

/// The desktop deep-link schemes from tauri.conf.json — the single source of
/// truth the plugin's register_all() also reads at runtime. Parsed from the
/// file embedded at compile time so the dev wrapper can never drift from the
/// packaged bundle's declaration. `None` means "no schemes configured".
#[cfg_attr(not(all(target_os = "macos", debug_assertions)), allow(dead_code))]
fn configured_schemes() -> Option<Vec<String>> {
    let raw = include_str!("../tauri.conf.json");
    let value: serde_json::Value = serde_json::from_str(raw).ok()?;
    let schemes = value
        .get("plugins")?
        .get("deep-link")?
        .get("desktop")?
        .get("schemes")?
        .as_array()?
        .iter()
        .filter_map(|scheme| scheme.as_str().map(str::to_string))
        .collect::<Vec<_>>();
    (!schemes.is_empty()).then_some(schemes)
}

/// A top-level key from tauri.conf.json (identifier / productName), with a
/// fallback for the unlikely case the file stops parsing.
#[cfg_attr(not(all(target_os = "macos", debug_assertions)), allow(dead_code))]
fn configured_string(key: &str) -> String {
    let raw = include_str!("../tauri.conf.json");
    serde_json::from_str::<serde_json::Value>(raw)
        .ok()
        .and_then(|value| {
            value
                .get(key)
                .and_then(|value| value.as_str())
                .map(str::to_string)
        })
        .unwrap_or_default()
}

/// The Info.plist for the dev wrapper bundle. Mirrors what the bundler writes
/// for a packaged build (CFBundleURLTypes), under a `.dev`-suffixed bundle id
/// so a dev checkout never masquerades as the installed production app.
/// `exec_name` must be the name bootstrap_dev_url_scheme symlinks the binary
/// as — the plist's CFBundleExecutable and the symlink are the same contract.
/// Pure — unit-tested on every CI platform.
#[cfg_attr(not(all(target_os = "macos", debug_assertions)), allow(dead_code))]
fn dev_wrapper_plist(
    identifier: &str,
    product_name: &str,
    exec_name: &str,
    schemes: &[String],
) -> String {
    let scheme_entries = schemes
        .iter()
        .map(|scheme| format!("        <string>{scheme}</string>"))
        .collect::<Vec<_>>()
        .join("\n");
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{product_name} Dev</string>
  <key>CFBundleDisplayName</key><string>{product_name} Dev</string>
  <key>CFBundleIdentifier</key><string>{identifier}.dev</string>
  <key>CFBundleExecutable</key><string>{exec_name}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key><string>{identifier}.dev</string>
      <key>CFBundleURLSchemes</key>
      <array>
{scheme_entries}
      </array>
    </dict>
  </array>
</dict>
</plist>
"#
    )
}

/// #25 (macOS dev mode): `tauri dev` runs the bare target/debug binary, which
/// has no Info.plist, so LaunchServices has no bundle to bind `skilyst://`
/// to — `open "skilyst://callback?code=…"` answers kLSApplicationNotFoundErr
/// and the browser auth callback dies silently. The deep-link plugin cannot
/// fix this at runtime (register_all() answers UnsupportedPlatform on macOS):
/// LaunchServices only routes URL events to a process it can associate with a
/// registered *bundle*.
///
/// So a debug build bootstraps its own wrapper bundle: synthesize
/// `target/debug/SkilystDev.app` (a symlink to this very binary plus an
/// Info.plist declaring the configured schemes), register it with
/// LaunchServices, and `exec` ourselves from inside the bundle. exec keeps
/// the same PID and stdio, so `tauri dev` keeps streaming logs and reaping
/// the right process, while LaunchServices now has a bundle to deliver GURL
/// Apple Events to. Verified live: a bare-path process never receives the
/// event; the same binary started from the wrapper does.
///
/// Failure is loud but never fatal — the app still runs, only skilyst://
/// delivery is affected. Release builds skip this entirely (the bundler
/// writes the real Info.plist).
#[cfg(all(target_os = "macos", debug_assertions))]
pub fn bootstrap_dev_url_scheme() {
    use std::os::unix::process::CommandExt;

    const BOOTSTRAPPED: &str = "SKILYST_DEV_BUNDLE";
    // Second generation: we already re-exec'd from inside the wrapper.
    if std::env::var_os(BOOTSTRAPPED).is_some() {
        return;
    }
    let Ok(exe) = std::env::current_exe() else {
        eprintln!(
            "[deep-link] dev bootstrap: current_exe unavailable — skilyst:// will not reach this process"
        );
        return;
    };
    // Launched from a real bundle (cold start via `open`): nothing to do.
    if inside_app_bundle(&exe) {
        return;
    }
    let Some(schemes) = configured_schemes() else {
        return; // no schemes configured: nothing to claim
    };
    let Some(target_debug) = exe.parent() else {
        return;
    };
    let wrapper = target_debug.join("SkilystDev.app");
    let macos_dir = wrapper.join("Contents").join("MacOS");
    if let Err(exc) = std::fs::create_dir_all(&macos_dir) {
        eprintln!(
            "[deep-link] dev bootstrap: could not create {}: {exc}",
            macos_dir.display()
        );
        return;
    }
    let binary_name = exe.file_name().map_or_else(
        || "skilyst-agent".to_string(),
        |name| name.to_string_lossy().into_owned(),
    );
    let linked = macos_dir.join(&binary_name);
    // Recreate on every launch: cheap, and always points at the newest build.
    let _ = std::fs::remove_file(&linked);
    if let Err(exc) = std::os::unix::fs::symlink(&exe, &linked) {
        eprintln!(
            "[deep-link] dev bootstrap: could not link {}: {exc}",
            linked.display()
        );
        return;
    }
    let identifier = configured_string("identifier");
    let product_name = configured_string("productName");
    let identifier = if identifier.is_empty() {
        "skilyst-agent".into()
    } else {
        identifier
    };
    let product_name = if product_name.is_empty() {
        "Skilyst Agent".into()
    } else {
        product_name
    };
    let plist = dev_wrapper_plist(&identifier, &product_name, &binary_name, &schemes);
    if let Err(exc) = std::fs::write(wrapper.join("Contents").join("Info.plist"), plist) {
        eprintln!("[deep-link] dev bootstrap: could not write the wrapper Info.plist: {exc}");
        return;
    }
    let lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";
    if !std::path::Path::new(lsregister).is_file() {
        eprintln!("[deep-link] dev bootstrap: lsregister not found at the expected path");
        return;
    }
    match std::process::Command::new(lsregister)
        .arg("-f")
        .arg(&wrapper)
        .status()
    {
        Ok(status) if status.success() => {}
        outcome => {
            eprintln!("[deep-link] dev bootstrap: lsregister failed: {outcome:?}");
            return;
        }
    }
    eprintln!(
        "[deep-link] dev wrapper registered ({}) — re-exec from inside it so LaunchServices can route the configured schemes to this process",
        wrapper.display()
    );
    let mut command = std::process::Command::new(&linked);
    command.env(BOOTSTRAPPED, "1");
    // exec() only returns on failure: on success this image is replaced.
    let exc = command.exec();
    eprintln!("[deep-link] dev bootstrap: re-exec failed: {exc}");
}

/// The browser callback lands as a `skilyst://callback?code=<one-time-code>`
/// deep link. The shell does not talk to the runtime itself (the webview owns
/// the token and the HTTP client), so the code is forwarded as an event the
/// login screen turns into POST /auth/deliver-code.
fn handle_deep_link(app: &AppHandle, url: &tauri::Url) {
    let Some(code) = code_from_url(url) else {
        eprintln!("[deep-link] not a sign-in callback (ignored): {url}");
        return;
    };
    if let Err(exc) = app.emit("auth-code", serde_json::json!({ "code": code })) {
        eprintln!("[deep-link] could not deliver the code to the window: {exc}");
    } else {
        // Success is logged without the code itself: the one-time code is a
        // credential, so it must never appear in logs or any committed output.
        eprintln!("[deep-link] sign-in callback received, code delivered to the window");
    }
}

/// `None` means "not connected"; a runtime whose process has exited is reported as
/// gone rather than as a stale port the frontend would keep talking to.
#[tauri::command]
fn runtime_status(state: State<'_, RuntimeState>) -> Option<RuntimeInfo> {
    let alive = {
        let mut guard = state.child.lock().unwrap();
        match guard.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(Some(_)) => {
                    *guard = None;
                    false
                }
                _ => true,
            },
            None => false,
        }
    };
    if !alive {
        *state.info.lock().unwrap() = None;
        return None;
    }
    state.info.lock().unwrap().clone()
}

pub fn run() {
    tauri::Builder::default()
        // #25: a second instance launched by a skilyst:// cold start (the dev
        // stub app, or a duplicate double-click) must hand its URL to the
        // running instance and exit, not race a second runtime child.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            // The deep-link plugin parses argv in the second instance and
            // forwards to the primary through its deep-link feature.
            eprintln!("[single-instance] secondary launch forwarded: {argv:?}");
            let _ = app;
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_opener::init())
        .manage(RuntimeState::default())
        .invoke_handler(tauri::generate_handler![
            runtime_start,
            runtime_stop,
            runtime_status,
            shell_open
        ])
        .setup(|app| {
            // #25: macOS LaunchServices learns the skilyst:// scheme from the
            // app bundle's Info.plist — which a `tauri dev` run does not have
            // (bare target/debug binary, and the plugin's register_all() is
            // Windows/Linux-only). In dev we register the running binary with
            // LaunchServices directly; production bundles take the plist path.
            #[cfg(all(target_os = "macos", debug_assertions))]
            {
                if let Err(exc) = register_macos_dev_scheme(app) {
                    // Loud but non-fatal: the app still runs, deep links just
                    // keep dying silently (the original #25 symptom) — the log
                    // line is the difference between a mystery and a hint.
                    eprintln!("[deep-link] dev scheme registration failed: {exc}");
                }
            }
            // Windows/Linux dev runs register through the plugin.
            #[cfg(any(target_os = "linux", all(debug_assertions, windows)))]
            if let Err(exc) = app.deep_link().register_all() {
                eprintln!("[deep-link] runtime scheme registration failed: {exc}");
            }
            // #20: the browser callback arrives as a skilyst:// deep link. Two
            // cases: the app was already running (on_open_url) or the link
            // launched it (get_current, macOS/Windows cold start) — both must
            // reach the webview as the same auth-code event.
            //
            // #25: claim the scheme at runtime BEFORE arming the listener.
            // register_all() is the runtime registration that makes dev mode
            // work on Windows (registry) and Linux (desktop entry); a packaged
            // build already owns the scheme through its bundle metadata. The
            // plugin has NO runtime registration on macOS (it answers
            // UnsupportedPlatform — verified against 2.5.0 source), so on
            // macOS the scheme is claimed by the bundle Info.plist instead:
            // packaged builds get it from the bundler, dev builds from the
            // wrapper bootstrap in main(). A failure here is logged loudly
            // rather than aborting startup: the listener and the cold-start
            // path still work for whichever mechanism did claim the scheme.
            if let Err(exc) = app.deep_link().register_all() {
                eprintln!("[deep-link] runtime scheme registration unavailable: {exc}");
            }
            let handle = app.handle().clone();
            app.deep_link().on_open_url({
                let handle = handle.clone();
                move |event| {
                    for url in event.urls() {
                        handle_deep_link(&handle, &url);
                    }
                }
            });
            if let Ok(Some(urls)) = app.deep_link().get_current() {
                for url in &urls {
                    handle_deep_link(&handle, url);
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // Closing the window must not leave an orphaned agent process behind.
                if let Some(state) = window.try_state::<RuntimeState>() {
                    stop_child(&state);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::*;

    const READY: &str = r#"{"event": "ready", "api_version": 1, "host": "127.0.0.1", "port": 62461,
        "token": "tok", "pid": 17116, "dry_run": true, "skill": null,
        "sessions_dir": "/tmp/sessions", "store_dir": "/tmp/store", "workspace_dir": "/tmp/ws"}"#;

    #[test]
    fn a_ready_line_becomes_the_runtime_location() {
        let info = parse_ready_line(READY).expect("ready line should parse");
        assert_eq!(info.port, 62461);
        assert_eq!(info.base_url, "http://127.0.0.1:62461");
        assert_eq!(info.token, "tok");
        assert_eq!(info.sessions_dir, "/tmp/sessions");
        assert!(info.dry_run);
    }

    #[test]
    fn ordinary_log_output_is_not_mistaken_for_a_ready_line() {
        assert!(parse_ready_line("http 127.0.0.1 \"GET /health HTTP/1.1\" 200 -").is_none());
        assert!(parse_ready_line(r#"{"event": "log", "port": 1}"#).is_none());
        assert!(parse_ready_line(r#"{"event": "ready"}"#).is_none()); // no port: not usable
    }

    #[test]
    fn a_live_runtime_is_reported_as_not_dry_run() {
        let line = READY.replace("\"dry_run\": true", "\"dry_run\": false");
        assert!(!parse_ready_line(&line).unwrap().dry_run);
    }

    #[test]
    fn the_sign_in_callback_yields_its_one_time_code() {
        // #43: the reverse-domain scheme is the primary (RFC 8252 §7.1).
        let url = tauri::Url::parse("petaverse.skilyst://callback?code=oc_abc123").unwrap();
        assert_eq!(code_from_url(&url).as_deref(), Some("oc_abc123"));
    }

    #[test]
    fn the_legacy_bare_scheme_still_yields_its_code_during_the_window() {
        let url = tauri::Url::parse("skilyst://callback?code=oc_abc123").unwrap();
        assert_eq!(code_from_url(&url).as_deref(), Some("oc_abc123"));
    }

    #[test]
    fn a_stranger_reverse_domain_scheme_is_ignored() {
        // Scheme collision protection (#43): only OUR reverse domain is ours.
        assert_eq!(
            code_from_url(&tauri::Url::parse("evil.skilyst://callback?code=x").unwrap()),
            None
        );
    }

    #[test]
    fn a_callback_without_a_code_yields_none() {
        let url = tauri::Url::parse("skilyst://callback").unwrap();
        assert_eq!(code_from_url(&url), None);
    }

    #[test]
    fn foreign_schemes_and_hosts_are_ignored() {
        // The scheme is the app's, but the host is not the sign-in callback.
        assert_eq!(
            code_from_url(&tauri::Url::parse("skilyst://open?code=x").unwrap()),
            None
        );
        // Some other app's deep link must never be read as ours.
        assert_eq!(
            code_from_url(&tauri::Url::parse("https://bee.verse4.pet/callback?code=x").unwrap()),
            None
        );
    }

    // -- #25: the macOS dev wrapper bootstrap ---------------------------------

    #[test]
    fn a_bare_dev_binary_is_recognized_as_outside_any_bundle() {
        assert!(!inside_app_bundle(std::path::Path::new(
            "/repo/desktop/src-tauri/target/debug/skilyst-agent"
        )));
        assert!(!inside_app_bundle(std::path::Path::new("skilyst-agent")));
    }

    #[test]
    fn a_binary_inside_an_app_bundle_is_recognized() {
        // The dev wrapper's own launch path, and a packaged install.
        assert!(inside_app_bundle(std::path::Path::new(
            "/repo/desktop/src-tauri/target/debug/SkilystDev.app/Contents/MacOS/skilyst-agent"
        )));
        assert!(inside_app_bundle(std::path::Path::new(
            "/Applications/Skilyst Agent.app/Contents/MacOS/skilyst-agent"
        )));
    }

    #[test]
    fn the_configured_schemes_come_from_tauri_conf_json() {
        // The wrapper and the packaged bundle must declare the same schemes;
        // this pins the single source of truth actually being read. #43:
        // reverse-domain primary first, bare word legacy during the window.
        assert_eq!(
            configured_schemes(),
            Some(vec![
                "petaverse.skilyst".to_string(),
                "skilyst".to_string()
            ])
        );
    }

    #[test]
    fn the_wrapper_plist_declares_the_dev_bundle_id_and_scheme() {
        let plist = dev_wrapper_plist(
            "com.petaverse.skilyst-agent",
            "Skilyst Agent",
            "skilyst-agent",
            &["skilyst".to_string()],
        );
        // Dev bundle id: a checkout must never pose as the installed app.
        assert!(plist.contains("<string>com.petaverse.skilyst-agent.dev</string>"));
        assert!(plist.contains("<string>skilyst</string>"));
        assert!(plist.contains("<key>CFBundleURLTypes</key>"));
        // The exec name must match what bootstrap_dev_url_scheme symlinks.
        assert!(plist.contains("<key>CFBundleExecutable</key><string>skilyst-agent</string>"));
    }
}
