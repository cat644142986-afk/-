// Product Atelier - Tauri Desktop Backend
// Manages window, Python sidecar, file dialogs, and config persistence.
// Hide console window on Windows release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// A release binary without Tauri's custom protocol opens build.devUrl and
// fails as soon as the Vite server is absent. Refuse to create such a binary.
#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!("Product Atelier release builds require --features custom-protocol; use `npx tauri build --no-bundle`");

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Manager, PhysicalPosition, PhysicalSize, Position, Size, State};

use serde::{Deserialize, Serialize};

const MAX_HTTP_HEADER_BYTES: usize = 64 * 1024;
const ASSET_DOWNLOAD_IDLE_TIMEOUT: Duration = Duration::from_secs(60);
static TEMP_FILE_SEQUENCE: AtomicU64 = AtomicU64::new(0);

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

fn validate_asset_id(asset_id: &str) -> Result<(), String> {
    let bytes = asset_id.as_bytes();
    if bytes.len() != 36
        || !asset_id.starts_with("ast_")
        || !bytes[4..]
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err(
            "ASSET_EXPORT_INVALID_ID: asset_id must be ast_ followed by 32 lowercase hexadecimal characters"
                .to_string(),
        );
    }
    Ok(())
}

fn validate_suggested_name(suggested_name: &str) -> Result<(), String> {
    let invalid_character = |character: char| {
        character.is_control()
            || matches!(
                character,
                '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'
            )
    };
    if suggested_name.is_empty()
        || suggested_name == "."
        || suggested_name == ".."
        || suggested_name.trim() != suggested_name
        || suggested_name.encode_utf16().count() > 240
        || suggested_name.chars().any(invalid_character)
        || suggested_name.ends_with('.')
    {
        return Err(
            "ASSET_EXPORT_INVALID_NAME: suggested_name must be a safe file name with at most 240 UTF-16 code units"
                .to_string(),
        );
    }
    Ok(())
}

fn read_http_header_line<R: BufRead>(
    reader: &mut R,
    header_bytes: &mut usize,
) -> Result<Vec<u8>, String> {
    let mut line = Vec::new();
    let allowance = MAX_HTTP_HEADER_BYTES
        .saturating_sub(*header_bytes)
        .saturating_add(1);
    let count = (&mut *reader)
        .take(allowance as u64)
        .read_until(b'\n', &mut line)
        .map_err(|error| format!("ASSET_EXPORT_NETWORK: could not read local response: {error}"))?;
    if count == 0 {
        return Err(
            "ASSET_EXPORT_TRUNCATED: local service closed before response headers completed"
                .to_string(),
        );
    }
    *header_bytes = header_bytes.saturating_add(count);
    if *header_bytes > MAX_HTTP_HEADER_BYTES {
        return Err("ASSET_EXPORT_PROTOCOL: response headers are too large".to_string());
    }
    if !line.ends_with(b"\n") {
        return Err("ASSET_EXPORT_TRUNCATED: response header line was truncated".to_string());
    }
    Ok(line)
}

fn trim_http_line(line: &[u8]) -> &[u8] {
    line.strip_suffix(b"\r\n")
        .or_else(|| line.strip_suffix(b"\n"))
        .unwrap_or(line)
}

fn download_asset_from_sidecar<W: Write>(
    port: u16,
    asset_id: &str,
    writer: &mut W,
) -> Result<u64, String> {
    validate_asset_id(asset_id)?;
    if port == 0 {
        return Err("ASSET_EXPORT_SIDECAR: local service port is unavailable".to_string());
    }

    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream =
        TcpStream::connect_timeout(&address, Duration::from_secs(5)).map_err(|error| {
            format!("ASSET_EXPORT_NETWORK: could not connect to local service: {error}")
        })?;
    stream
        .set_read_timeout(Some(ASSET_DOWNLOAD_IDLE_TIMEOUT))
        .map_err(|error| format!("ASSET_EXPORT_NETWORK: could not set read timeout: {error}"))?;
    stream
        .set_write_timeout(Some(Duration::from_secs(10)))
        .map_err(|error| format!("ASSET_EXPORT_NETWORK: could not set write timeout: {error}"))?;

    let request = format!(
        "GET /api/assets/{asset_id}/content?download=true HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/octet-stream\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .and_then(|_| stream.flush())
        .map_err(|error| format!("ASSET_EXPORT_NETWORK: could not request local asset: {error}"))?;

    let mut reader = BufReader::new(stream);
    let mut header_bytes = 0usize;
    let status_line = read_http_header_line(&mut reader, &mut header_bytes)?;
    let status_text = std::str::from_utf8(trim_http_line(&status_line))
        .map_err(|_| "ASSET_EXPORT_PROTOCOL: response status is not ASCII".to_string())?;
    let mut status_parts = status_text.split_whitespace();
    let http_version = status_parts.next().unwrap_or_default();
    let status_code = status_parts
        .next()
        .ok_or_else(|| "ASSET_EXPORT_PROTOCOL: response status code is missing".to_string())?
        .parse::<u16>()
        .map_err(|_| "ASSET_EXPORT_PROTOCOL: response status code is invalid".to_string())?;
    if !matches!(http_version, "HTTP/1.0" | "HTTP/1.1") {
        return Err("ASSET_EXPORT_PROTOCOL: unsupported HTTP response version".to_string());
    }
    if status_code != 200 {
        return Err(format!(
            "ASSET_EXPORT_HTTP_STATUS: local service returned HTTP {status_code}; redirects and partial responses are refused"
        ));
    }

    let mut content_length = None;
    let mut transfer_encoding = false;
    let mut content_encoding = None;
    loop {
        let line = read_http_header_line(&mut reader, &mut header_bytes)?;
        let line = trim_http_line(&line);
        if line.is_empty() {
            break;
        }
        if matches!(line.first(), Some(b' ' | b'\t')) {
            return Err("ASSET_EXPORT_PROTOCOL: folded response headers are refused".to_string());
        }
        let separator = line
            .iter()
            .position(|byte| *byte == b':')
            .ok_or_else(|| "ASSET_EXPORT_PROTOCOL: malformed response header".to_string())?;
        let name = std::str::from_utf8(&line[..separator])
            .map_err(|_| "ASSET_EXPORT_PROTOCOL: response header name is not ASCII".to_string())?;
        let value = std::str::from_utf8(&line[separator + 1..])
            .map_err(|_| "ASSET_EXPORT_PROTOCOL: response header value is not ASCII".to_string())?
            .trim();
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() || value.starts_with('+') {
                return Err(
                    "ASSET_EXPORT_PROTOCOL: ambiguous Content-Length is refused".to_string()
                );
            }
            content_length = Some(value.parse::<u64>().map_err(|_| {
                "ASSET_EXPORT_PROTOCOL: invalid Content-Length from local service".to_string()
            })?);
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            transfer_encoding = true;
        } else if name.eq_ignore_ascii_case("content-encoding") {
            content_encoding = Some(value.to_ascii_lowercase());
        }
    }

    if transfer_encoding {
        return Err(
            "ASSET_EXPORT_PROTOCOL: Transfer-Encoding is refused for exact binary export"
                .to_string(),
        );
    }
    if content_encoding
        .as_deref()
        .is_some_and(|encoding| !encoding.is_empty() && encoding != "identity")
    {
        return Err(
            "ASSET_EXPORT_PROTOCOL: encoded response bodies are refused for exact binary export"
                .to_string(),
        );
    }
    let expected = content_length.ok_or_else(|| {
        "ASSET_EXPORT_PROTOCOL: Content-Length is required to detect truncated exports".to_string()
    })?;

    let mut remaining = expected;
    let mut buffer = [0u8; 64 * 1024];
    while remaining > 0 {
        let chunk_size =
            usize::try_from(remaining.min(buffer.len() as u64)).unwrap_or(buffer.len());
        let count = reader.read(&mut buffer[..chunk_size]).map_err(|error| {
            format!("ASSET_EXPORT_NETWORK: could not read local asset: {error}")
        })?;
        if count == 0 {
            return Err(format!(
                "ASSET_EXPORT_TRUNCATED: expected {expected} bytes but received {}",
                expected - remaining
            ));
        }
        writer.write_all(&buffer[..count]).map_err(|error| {
            format!("ASSET_EXPORT_WRITE: could not write temporary file: {error}")
        })?;
        remaining -= count as u64;
    }
    Ok(expected)
}

fn create_export_temp_file(target: &Path) -> Result<(PathBuf, File), String> {
    let parent = target
        .parent()
        .filter(|path| path.is_dir())
        .ok_or_else(|| {
            "ASSET_EXPORT_PATH: selected destination directory does not exist".to_string()
        })?;
    for _ in 0..100 {
        let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let temp_path = parent.join(format!(
            ".product-atelier-export-{}-{sequence}.tmp",
            std::process::id()
        ));
        match OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&temp_path)
        {
            Ok(file) => return Ok((temp_path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(format!(
                    "ASSET_EXPORT_WRITE: could not create temporary file: {error}"
                ));
            }
        }
    }
    Err("ASSET_EXPORT_WRITE: could not allocate a unique temporary file".to_string())
}

#[cfg(windows)]
fn replace_export_file(temp_path: &Path, target: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows::core::PCWSTR;
    use windows::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let temp_wide: Vec<u16> = temp_path.as_os_str().encode_wide().chain(Some(0)).collect();
    let target_wide: Vec<u16> = target.as_os_str().encode_wide().chain(Some(0)).collect();
    unsafe {
        MoveFileExW(
            PCWSTR(temp_wide.as_ptr()),
            PCWSTR(target_wide.as_ptr()),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    }
    .map_err(|error| format!("ASSET_EXPORT_REPLACE: could not replace destination: {error}"))
}

#[cfg(not(windows))]
fn replace_export_file(temp_path: &Path, target: &Path) -> Result<(), String> {
    std::fs::rename(temp_path, target)
        .map_err(|error| format!("ASSET_EXPORT_REPLACE: could not replace destination: {error}"))
}

fn download_asset_to_path(port: u16, asset_id: &str, target: &Path) -> Result<u64, String> {
    validate_asset_id(asset_id)?;
    if target.is_dir() {
        return Err("ASSET_EXPORT_PATH: selected destination is a directory".to_string());
    }
    if target
        .symlink_metadata()
        .is_ok_and(|metadata| !metadata.file_type().is_file())
    {
        return Err("ASSET_EXPORT_PATH: existing destination must be a regular file".to_string());
    }

    let (temp_path, mut temp_file) = create_export_temp_file(target)?;
    let download_result =
        download_asset_from_sidecar(port, asset_id, &mut temp_file).and_then(|size| {
            temp_file.flush().map_err(|error| {
                format!("ASSET_EXPORT_WRITE: could not flush temporary file: {error}")
            })?;
            temp_file.sync_all().map_err(|error| {
                format!("ASSET_EXPORT_WRITE: could not sync temporary file: {error}")
            })?;
            Ok(size)
        });
    drop(temp_file);

    let size = match download_result {
        Ok(size) => size,
        Err(error) => {
            let _ = std::fs::remove_file(&temp_path);
            return Err(error);
        }
    };
    if let Err(error) = replace_export_file(&temp_path, target) {
        let _ = std::fs::remove_file(&temp_path);
        return Err(error);
    }
    Ok(size)
}

#[tauri::command]
async fn save_binary_asset(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    asset_id: String,
    suggested_name: String,
) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;

    validate_asset_id(&asset_id)?;
    validate_suggested_name(&suggested_name)?;
    if state.shutting_down.load(Ordering::SeqCst) {
        return Err("ASSET_EXPORT_SIDECAR: application is shutting down".to_string());
    }
    {
        let mut child_slot = state.python_child.lock().unwrap();
        let child = child_slot
            .as_mut()
            .ok_or_else(|| "ASSET_EXPORT_SIDECAR: local service is not running".to_string())?;
        match child.try_wait() {
            Ok(None) => {}
            Ok(Some(_)) => {
                return Err("ASSET_EXPORT_SIDECAR: local service has stopped".to_string());
            }
            Err(error) => {
                return Err(format!(
                    "ASSET_EXPORT_SIDECAR: could not verify local service: {error}"
                ));
            }
        }
    }
    let port = *state.api_port.lock().unwrap();

    let (sender, receiver) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_file_name(&suggested_name)
        .save_file(move |path| {
            let _ = sender.send(path);
        });
    let selected = tauri::async_runtime::spawn_blocking(move || receiver.recv())
        .await
        .map_err(|error| format!("ASSET_EXPORT_DIALOG: save dialog task failed: {error}"))?
        .map_err(|error| format!("ASSET_EXPORT_DIALOG: save dialog failed: {error}"))?
        .ok_or_else(|| "ASSET_EXPORT_CANCELLED: save was cancelled".to_string())?;
    let target = selected
        .into_path()
        .map_err(|error| format!("ASSET_EXPORT_PATH: selected path is invalid: {error}"))?;
    let result_path = target.clone();

    tauri::async_runtime::spawn_blocking(move || download_asset_to_path(port, &asset_id, &target))
        .await
        .map_err(|error| format!("ASSET_EXPORT_TASK: export task failed: {error}"))??;

    Ok(result_path.to_string_lossy().into_owned())
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
fn close_app(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "Window not found".to_string())?;
    window.close().map_err(|error| error.to_string())
}

fn stop_sidecar_in_slot(state: &AppState) {
    if let Some(mut child) = state.python_child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn shutdown_sidecar(state: &AppState) {
    state.shutting_down.store(true, Ordering::SeqCst);
    stop_sidecar_in_slot(state);

    let deadline = Instant::now() + Duration::from_secs(5);
    while state.sidecar_starting.load(Ordering::SeqCst) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    stop_sidecar_in_slot(state);
    if state.sidecar_starting.load(Ordering::SeqCst) {
        log_msg("[ProductAtelier] WARNING: Sidecar startup did not settle before shutdown");
    }
}

#[tauri::command]
fn complete_close_app(app: tauri::AppHandle, state: State<AppState>) {
    shutdown_sidecar(&state);
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
            save_base64_image, save_binary_asset, open_in_folder,
            select_folder_dialog, verify_folder_exists,
            close_app, complete_close_app,
            get_window_position, get_window_size, get_monitor_info,
            get_window_metrics, set_window_always_on_top, set_window_pos_size
        ])
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::Destroyed if window.label() == "main" => {
                    let state = window.state::<AppState>();
                    shutdown_sidecar(&state);
                }
                tauri::WindowEvent::Focused(_) => {}
                _ => {}
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Product Atelier");
}

#[cfg(test)]
mod binary_asset_export_tests;
