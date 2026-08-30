// Product Atelier - Tauri Desktop Backend
// Manages window, Python sidecar, file dialogs, and config persistence.
// Hide console window on Windows release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// A release binary without Tauri's custom protocol opens build.devUrl and
// fails as soon as the Vite server is absent. Refuse to create such a binary.
#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!("Product Atelier release builds require --features custom-protocol; use `npx tauri build --no-bundle`");

use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::path::PathBuf;
use std::net::TcpStream;
use std::io::{Read, Write};
use std::time::Instant;
use tauri::{Manager, PhysicalPosition, PhysicalSize, Position, Size, State};

use serde::{Deserialize, Serialize};

// ---- State ----
struct AppState {
    python_child: Mutex<Option<Child>>,
    api_port: Mutex<u16>,
    config: Mutex<AppConfig>,
    started_at: Instant,
    sidecar_starting: AtomicBool,
    shutting_down: AtomicBool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(default)]
struct AppConfig {
    api_key: String,
    default_model: String,
    default_platter: String,
    default_angle: String,
    default_fidelity: i32,
    auto_refine: bool,
    knowledge_base_path: String,
    output_root: String,
    known_output_roots: Vec<String>,
    grounding_runtime_root: String,
    grounding_model_root: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            api_key: String::new(),
            default_model: "gpt-image-2".to_string(),
            default_platter: "auto".to_string(),
            default_angle: "auto".to_string(),
            default_fidelity: 40,
            auto_refine: true,
            knowledge_base_path: String::new(),
            output_root: String::new(),
            known_output_roots: Vec::new(),
            grounding_runtime_root: String::new(),
            grounding_model_root: String::new(),
        }
    }
}

fn app_data_dir() -> PathBuf {
    if let Ok(override_dir) = std::env::var("PRODUCT_ATELIER_DATA_DIR") {
        let override_dir = override_dir.trim();
        if !override_dir.is_empty() {
            let path = PathBuf::from(override_dir);
            std::fs::create_dir_all(&path).ok();
            return path;
        }
    }
    let appdata = std::env::var("APPDATA")
        .or_else(|_| std::env::var("HOME").map(|h| h + "/.config"))
        .unwrap_or_else(|_| ".".to_string());
    let p = PathBuf::from(appdata).join("ProductAtelier");
    std::fs::create_dir_all(&p).ok();
    p
}

fn config_path() -> PathBuf {
    app_data_dir().join("config.json")
}

fn load_config() -> AppConfig {
    let path = config_path();
    if path.exists() {
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(cfg) = serde_json::from_str::<AppConfig>(&text) {
                return cfg;
            }
        }
    }
    // Legacy config location
    if let Some(home) = home_dir() {
        let legacy = home.join(r".codex\skills\lk-ai-image\config.json");
        if legacy.exists() {
            if let Ok(text) = std::fs::read_to_string(&legacy) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(key) = v.get("api_key").and_then(|k| k.as_str()) {
                        return AppConfig { api_key: key.to_string(), ..Default::default() };
                    }
                }
            }
        }
    }
    AppConfig::default()
}

fn save_config(cfg: &AppConfig) -> Result<(), String> {
    let path = config_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(&path, text).map_err(|e| e.to_string())?;
    Ok(())
}

fn home_dir() -> Option<PathBuf> {
    std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")).ok().map(PathBuf::from)
}

fn find_free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .ok()
        .and_then(|l| l.local_addr().ok().map(|a| a.port()))
        .unwrap_or(8765)
}

fn current_exe_dir() -> Option<PathBuf> {
    std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.to_path_buf()))
}

fn find_server_path<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Option<(PathBuf, bool)> {
    let exe_dir = current_exe_dir();
    let mut candidates: Vec<(PathBuf, bool)> = Vec::new();

    // Development must run the current source tree instead of a stale resource
    // copy left in target/debug by an earlier PyInstaller build.
    #[cfg(debug_assertions)]
    {
        candidates.push((
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../python/server.py"),
            false,
        ));
        candidates.push((PathBuf::from("python/server.py"), false));
        if let Some(d) = &exe_dir {
            candidates.push((d.join("../../../python/server.py"), false));
            candidates.push((d.join("../../../../python/server.py"), false));
            candidates.push((d.join("../../python/server.py"), false));
        }
    }

    // Production: compiled python-server directory (Tauri resources / portable)
    if let Some(d) = &exe_dir {
        candidates.push((d.join("python-server/python-server.exe"), true));
        candidates.push((d.join("python-server.exe"), true));
    }
    if let Ok(res) = app.path().resource_dir() {
        candidates.push((res.join("python-server/python-server.exe"), true));
        candidates.push((res.join("python-server.exe"), true));
    }

    // Dev mode: python/server.py
    if let Some(d) = &exe_dir {
        candidates.push((d.join("../../../../python/server.py"), false));
        candidates.push((d.join("../../python/server.py"), false));
        candidates.push((d.join("../python/server.py"), false));
        candidates.push((d.join("python/server.py"), false));
    }
    if let Ok(res) = app.path().resource_dir() {
        candidates.push((res.join("python/server.py"), false));
    }
    candidates.push((PathBuf::from("python/server.py"), false));

    candidates.into_iter().filter(|(p, _)| p.exists()).next()
}

/// Apply platform-specific flags to prevent console window from appearing.
/// On Windows: sets CREATE_NO_WINDOW (0x08000000) to suppress conhost.exe window.
/// On other platforms: no-op.
fn apply_no_window_flags(_cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW = 0x08000000 — prevents the process from inheriting/creating a console
        _cmd.creation_flags(0x08000000);
    }
}

fn start_python_sidecar<R: tauri::Runtime>(port: u16, app: &tauri::AppHandle<R>) -> Option<Child> {
    let (server_path, is_exe) = find_server_path(app)?;
    log_msg(&format!("[ProductAtelier] Found server at: {} (exe={})", server_path.display(), is_exe));

    if is_exe {
        let mut cmd = Command::new(&server_path);
        cmd.arg(port.to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null());
        apply_no_window_flags(&mut cmd);
        if let Ok(child) = cmd.spawn() {
            log_msg(&format!("[ProductAtelier] Started compiled server (pid={}, port={})", child.id(), port));
            return Some(child);
        }
    } else {
        for python_cmd in &["python", "python3", "py"] {
            let mut cmd = Command::new(python_cmd);
            cmd.arg(&server_path)
                .arg(port.to_string())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .stdin(Stdio::null());
            apply_no_window_flags(&mut cmd);
            if let Ok(child) = cmd.spawn() {
                log_msg(&format!("[ProductAtelier] Started Python via {} (pid={}, port={})", python_cmd, child.id(), port));
                return Some(child);
            }
        }
    }
    log_msg("[ProductAtelier] ERROR: Could not start backend server");
    None
}

/// Write log messages to a file in AppData (no console available in release builds).
fn log_msg(msg: &str) {
    #[cfg(debug_assertions)]
    {
        eprintln!("{}", msg);
    }
    #[cfg(not(debug_assertions))]
    {
        let log_path = app_data_dir().join("app.log");
        let line = format!("{} {}\n", chrono_local(), msg);
        let _ = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .and_then(|mut f| std::io::Write::write_all(&mut f, line.as_bytes()));
    }
}

#[cfg(not(debug_assertions))]
fn chrono_local() -> String {
    // Simple timestamp without external chrono crate dependency
    let dur = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = dur.as_secs();
    let hours = (secs % 86400) / 3600;
    let mins = (secs % 3600) / 60;
    let s = secs % 60;
    format!("{:02}:{:02}:{:02}", hours, mins, s)
}

fn wait_for_server(port: u16, timeout_secs: u64) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed().as_secs() < timeout_secs {
        if TcpStream::connect(format!("127.0.0.1:{}", port)).is_ok() {
            if let Ok(mut stream) = TcpStream::connect(format!("127.0.0.1:{}", port)) {
                let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(3)));
                let req = format!("GET /api/health HTTP/1.0\r\nHost:127.0.0.1:{}\r\nConnection:close\r\n\r\n", port);
                if stream.write_all(req.as_bytes()).is_ok() {
                    let mut resp = Vec::new();
                    if stream.read_to_end(&mut resp).is_ok() {
                        let text = String::from_utf8_lossy(&resp);
                        if text.contains("200") && text.contains("ok") {
                            return true;
                        }
                    }
                }
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(500));
    }
    false
}

// ---- Tauri Commands ----

#[tauri::command]
fn get_api_port(state: State<AppState>) -> u16 {
    *state.api_port.lock().unwrap()
}

#[tauri::command]
fn ensure_python_sidecar(
    app: tauri::AppHandle,
    state: State<AppState>,
) -> Result<u16, String> {
    if state.shutting_down.load(Ordering::SeqCst) {
        return Err("Application is shutting down".to_string());
    }

    {
        let mut child_slot = state.python_child.lock().unwrap();
        if let Some(child) = child_slot.as_mut() {
            match child.try_wait() {
                Ok(None) => return Ok(*state.api_port.lock().unwrap()),
                Ok(Some(status)) => {
                    log_msg(&format!(
                        "[ProductAtelier] Sidecar exited unexpectedly ({status}); restarting"
                    ));
                }
                Err(error) => {
                    log_msg(&format!(
                        "[ProductAtelier] Could not inspect sidecar process ({error}); restarting"
                    ));
                }
            }
        }
        if let Some(mut stale_child) = child_slot.take() {
            let _ = stale_child.kill();
            let _ = stale_child.wait();
        }
    }

    if state
        .sidecar_starting
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        return Ok(*state.api_port.lock().unwrap());
    }

    let port = find_free_port();
    *state.api_port.lock().unwrap() = port;
    let result = match start_python_sidecar(port, &app) {
        Some(mut child) => {
            if state.shutting_down.load(Ordering::SeqCst) {
                let _ = child.kill();
                let _ = child.wait();
                Err("Application is shutting down".to_string())
            } else {
                let pid = child.id();
                *state.python_child.lock().unwrap() = Some(child);
                log_msg(&format!(
                    "[ProductAtelier] Sidecar recovery started (pid={pid}, port={port})"
                ));
                Ok(port)
            }
        }
        None => Err("Could not restart the local service".to_string()),
    };
    state.sidecar_starting.store(false, Ordering::SeqCst);
    result
}

#[tauri::command]
fn report_startup_milestone(state: State<AppState>, milestone: String) -> Result<(), String> {
    const ALLOWED: &[&str] = &[
        "dom-ready",
        "first-paint",
        "backend-connecting",
        "backend-ready",
        "workspace-ready",
        "backend-unavailable",
    ];
    if !ALLOWED.contains(&milestone.as_str()) {
        return Err("Unknown startup milestone".to_string());
    }
    log_msg(&format!(
        "[ProductAtelier] Startup milestone: {milestone} at {}ms",
        state.started_at.elapsed().as_millis()
    ));
    Ok(())
}

#[tauri::command]
fn get_app_config(state: State<AppState>) -> AppConfig {
    state.config.lock().unwrap().clone()
}

#[tauri::command]
fn set_app_config(state: State<AppState>, config: AppConfig) -> Result<AppConfig, String> {
    save_config(&config)?;
    *state.config.lock().unwrap() = config.clone();
    Ok(config)
}

#[tauri::command]
fn save_base64_image(app: tauri::AppHandle, suggested_name: String, data_b64: String) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;
    let bytes = base64_decode(&data_b64).map_err(|e| e.to_string())?;
    let ext = if suggested_name.ends_with(".png") { "png" } else { "jpg" };
    let (tx, rx) = std::sync::mpsc::channel();
    let tx = Arc::new(Mutex::new(Some(tx)));
    app.dialog().file()
        .set_file_name(&suggested_name)
        .add_filter("图片文件", &[ext])
        .save_file(move |path| {
            let _ = tx.lock().unwrap().take().unwrap().send(path.map(|p| p.to_string()));
        });
    let path_str = rx.recv().map_err(|e| e.to_string())?
        .ok_or_else(|| "保存已取消".to_string())?;
    std::fs::write(&path_str, &bytes).map_err(|e| e.to_string())?;
    Ok(path_str)
}

#[tauri::command]
fn open_in_folder(path: String) -> Result<(), String> {
    let p = PathBuf::from(&path);
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    let target = if p.is_file() {
        p.parent().map(|d| d.to_path_buf()).unwrap_or_else(|| p.clone())
    } else {
        p.clone()
    };
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer").arg(target).spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open").arg("-R").arg(&p).spawn().map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        Command::new("xdg-open").arg(target).spawn().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn select_folder_dialog(app: tauri::AppHandle) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = std::sync::mpsc::channel();
    let tx = Arc::new(Mutex::new(Some(tx)));
    app.dialog().file().pick_folder(move |path| {
        let _ = tx.lock().unwrap().take().unwrap().send(path.map(|p| p.to_string()));
    });
    let path_str = rx.recv().map_err(|e| e.to_string())?;
    path_str.ok_or_else(|| "已取消".to_string())
}

#[tauri::command]
fn verify_folder_exists(path: String) -> bool {
    let p = PathBuf::from(&path);
    p.exists() && p.is_dir()
}

#[tauri::command]
fn close_app(app: tauri::AppHandle, state: State<AppState>) {
    state.shutting_down.store(true, Ordering::SeqCst);
    if let Some(mut child) = state.python_child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    app.exit(0);
}

fn base64_decode(s: &str) -> Result<Vec<u8>, base64::DecodeError> {
    use base64::{Engine, engine::general_purpose::STANDARD};
    STANDARD.decode(s)
}

// ---- Main ----

// ---- Docking / Edge Snap Commands ----

#[tauri::command]
fn get_window_position(app: tauri::AppHandle) -> Result<(i32, i32), String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    let pos = win.outer_position().map_err(|e| e.to_string())?;
    Ok((pos.x, pos.y))
}

#[tauri::command]
fn get_window_size(app: tauri::AppHandle) -> Result<(u32, u32), String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    let sz = win.outer_size().map_err(|e| e.to_string())?;
    Ok((sz.width, sz.height))
}

#[tauri::command]
fn get_monitor_info(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    let monitor = win.current_monitor().map_err(|e| e.to_string())?
        .ok_or("No monitor found")?;
    let sz = monitor.size();
    let pos = monitor.position();
    let ws = monitor.work_area();
    let scale = monitor.scale_factor();
    Ok(serde_json::json!({
        "coordinate_space": "physical",
        "width": sz.width,
        "height": sz.height,
        "x": pos.x,
        "y": pos.y,
        "work_x": ws.position.x,
        "work_y": ws.position.y,
        "work_width": ws.size.width,
        "work_height": ws.size.height,
        "scale_factor": scale,
        "logical": {
            "width": sz.width as f64 / scale,
            "height": sz.height as f64 / scale,
            "x": pos.x as f64 / scale,
            "y": pos.y as f64 / scale,
            "work_x": ws.position.x as f64 / scale,
            "work_y": ws.position.y as f64 / scale,
            "work_width": ws.size.width as f64 / scale,
            "work_height": ws.size.height as f64 / scale,
        },
    }))
}

#[tauri::command]
fn get_window_metrics(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    let scale = win.scale_factor().map_err(|e| e.to_string())?;
    let size = win.outer_size().map_err(|e| e.to_string())?;
    let position = win.outer_position().map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "scale_factor": scale,
        "physical": {
            "x": position.x,
            "y": position.y,
            "width": size.width,
            "height": size.height,
        },
        "logical": {
            "x": position.x as f64 / scale,
            "y": position.y as f64 / scale,
            "width": size.width as f64 / scale,
            "height": size.height as f64 / scale,
        },
    }))
}

#[tauri::command]
fn set_window_always_on_top(app: tauri::AppHandle, always_on_top: bool) -> Result<(), String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    win.set_always_on_top(always_on_top).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn set_window_pos_size(app: tauri::AppHandle, x: i32, y: i32, w: u32, h: u32) -> Result<(), String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    // get_monitor_info/get_window_position/get_window_size all report physical
    // pixels. Keep this paired setter in the same coordinate space so docking
    // remains correct on 125%/150% and mixed-DPI monitors.
    win.set_size(Size::Physical(PhysicalSize { width: w, height: h })).map_err(|e| e.to_string())?;
    win.set_position(Position::Physical(PhysicalPosition { x, y })).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(windows)]
fn apply_windows_window_chrome<R: tauri::Runtime>(window: &tauri::Window<R>) -> Result<(), String> {
    use std::ffi::c_void;
    use windows::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_WINDOW_CORNER_PREFERENCE,
        DWMWCP_ROUND,
    };
    use windows::Win32::Graphics::Gdi::SetWindowRgn;

    let hwnd = window.hwnd().map_err(|error| error.to_string())?;
    // Clear the legacy GDI region first. HRGN clipping is binary and produced
    // visible stair-step edges at fractional DPI. Windows 11 DWM owns the
    // composited outer edge; older Windows versions safely fall back to a
    // rectangular opaque window if the corner attribute is unsupported.
    unsafe { SetWindowRgn(hwnd, None, true); }
    let corner_preference = DWMWCP_ROUND;
    let border_color = 0xFFFF_FFFEu32;
    unsafe {
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            std::ptr::from_ref(&corner_preference).cast::<c_void>(),
            std::mem::size_of_val(&corner_preference) as u32,
        );
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_BORDER_COLOR,
            std::ptr::from_ref(&border_color).cast::<c_void>(),
            std::mem::size_of_val(&border_color) as u32,
        );
    }
    Ok(())
}

#[cfg(not(windows))]
fn apply_windows_window_chrome<R: tauri::Runtime>(_window: &tauri::Window<R>) -> Result<(), String> {
    Ok(())
}

fn log_window_metrics<R: tauri::Runtime>(window: &tauri::Window<R>) {
    let Ok(scale) = window.scale_factor() else { return; };
    let Ok(size) = window.outer_size() else { return; };
    let logical_width = size.width as f64 / scale;
    let logical_height = size.height as f64 / scale;
    log_msg(&format!(
        "[ProductAtelier] Window metrics: scale={scale:.2}, physical={}x{}, logical={logical_width:.1}x{logical_height:.1}",
        size.width, size.height
    ));
}

fn main() {
    let process_started_at = Instant::now();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            log_msg(&format!(
                "[ProductAtelier] Frontend mode: {}",
                if cfg!(feature = "custom-protocol") {
                    "embedded-custom-protocol"
                } else {
                    "development-server"
                }
            ));
            if let Some(webview_window) = app.get_webview_window("main") {
                let window = webview_window.as_ref().window();
                if let Err(error) = apply_windows_window_chrome(&window) {
                    log_msg(&format!("[ProductAtelier] WARNING: Could not apply Windows chrome: {error}"));
                }
                log_window_metrics(&window);
            }
            let cfg = load_config();
            let port = find_free_port();
            app.manage(AppState {
                python_child: Mutex::new(None),
                api_port: Mutex::new(port),
                config: Mutex::new(cfg),
                started_at: process_started_at,
                sidecar_starting: AtomicBool::new(true),
                shutting_down: AtomicBool::new(false),
            });
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if handle.state::<AppState>().shutting_down.load(Ordering::SeqCst) {
                    handle.state::<AppState>().sidecar_starting.store(false, Ordering::SeqCst);
                    return;
                }
                let Some(mut child) = start_python_sidecar(port, &handle) else {
                    log_msg("[ProductAtelier] WARNING: Failed to start Python sidecar");
                    handle.state::<AppState>().sidecar_starting.store(false, Ordering::SeqCst);
                    return;
                };
                let state = handle.state::<AppState>();
                let mut child_slot = state.python_child.lock().unwrap();
                if state.shutting_down.load(Ordering::SeqCst) {
                    drop(child_slot);
                    let _ = child.kill();
                    let _ = child.wait();
                    state.sidecar_starting.store(false, Ordering::SeqCst);
                    return;
                }
                *child_slot = Some(child);
                drop(child_slot);
                state.sidecar_starting.store(false, Ordering::SeqCst);
                log_msg(&format!("[ProductAtelier] Waiting for backend on port {}...", port));
                if !wait_for_server(port, 45) {
                    log_msg("[ProductAtelier] WARNING: Backend not ready after 45s");
                } else {
                    log_msg(&format!("[ProductAtelier] Backend ready on port {}", port));
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_api_port, ensure_python_sidecar, report_startup_milestone, get_app_config, set_app_config,
            save_base64_image, open_in_folder,
            select_folder_dialog, verify_folder_exists,
            close_app,
            get_window_position, get_window_size, get_monitor_info,
            get_window_metrics, set_window_always_on_top, set_window_pos_size
        ])
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { .. } => {
                    let state_mutex = window.state::<AppState>();
                    state_mutex.shutting_down.store(true, Ordering::SeqCst);
                    let mut child_opt = state_mutex.python_child.lock().unwrap();
                    if let Some(mut child) = child_opt.take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
                tauri::WindowEvent::Focused(_) => {}
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Product Atelier");
}
