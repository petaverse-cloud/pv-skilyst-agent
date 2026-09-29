#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // #25: on macOS a debug build must live inside a registered bundle for
    // LaunchServices to route skilyst:// to it; the call exec's in place on
    // success, so everything below still runs in the same PID/stdio. On other
    // platforms (and release builds) it is compiled out entirely.
    #[cfg(all(target_os = "macos", debug_assertions))]
    skilyst_agent_lib::bootstrap_dev_url_scheme();

    skilyst_agent_lib::run()
}
