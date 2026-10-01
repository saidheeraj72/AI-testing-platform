// AI Tester desktop shell: starts the Python engine (the PyInstaller-built sidecar shipped as a
// resource) on a free local port with a fresh token, shows the React UI, and stops the engine on quit.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

struct Engine {
    url: String,
    token: String,
    child: Mutex<Option<Child>>,
}

#[derive(serde::Serialize)]
struct ApiConfig {
    url: String,
    token: String,
}

/// The UI asks for the engine's address and token (it never sees them in a URL).
#[tauri::command]
fn api_config(engine: tauri::State<Engine>) -> ApiConfig {
    ApiConfig { url: engine.url.clone(), token: engine.token.clone() }
}

fn new_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("no system randomness");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(8765)
}

/// Ask the engine to stop (it saves the running test's trace and report), then make sure it does.
fn stop(child: &mut Child) {
    #[cfg(unix)]
    unsafe {
        libc::kill(child.id() as i32, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let port = free_port();
            let token = new_token();
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;
            let exe = if cfg!(windows) { "ai-tester-engine.exe" } else { "ai-tester-engine" };
            let engine_path = app.path().resource_dir()?.join("ai-tester-engine").join(exe);
            let log = std::fs::File::create(data_dir.join("engine.log"))?;
            let child = Command::new(&engine_path)
                .arg("--port")
                .arg(port.to_string())
                .env("AI_TESTER_TOKEN", &token)
                .env("AI_TESTER_DATA_DIR", &data_dir)
                .stdout(Stdio::from(log.try_clone()?))
                .stderr(Stdio::from(log))
                .spawn()
                .map_err(|e| format!("could not start the engine at {}: {e}", engine_path.display()))?;
            app.manage(Engine {
                url: format!("http://127.0.0.1:{port}"),
                token,
                child: Mutex::new(Some(child)),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![api_config])
        .build(tauri::generate_context!())
        .expect("error while building AI Tester")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Engine>().child.lock().unwrap().take() {
                    stop(&mut child);
                }
            }
        });
}
