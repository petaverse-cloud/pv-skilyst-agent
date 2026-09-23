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
use tauri::{AppHandle, Manager, State};

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
        .manage(RuntimeState::default())
        .invoke_handler(tauri::generate_handler![
            runtime_start,
            runtime_stop,
            runtime_status
        ])
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
}
