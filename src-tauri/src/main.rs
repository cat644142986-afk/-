// Product Atelier - Tauri Desktop Backend
// Manages window, Python sidecar, file dialogs, and config persistence.
// Hide console window on Windows release builds
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// A release binary without Tauri's custom protocol opens build.devUrl and
// fails as soon as the Vite server is absent. Refuse to create such a binary.
#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!("Product Atelier release builds require --features custom-protocol; use `npx tauri build --no-bundle`");

use std::ffi::{OsStr, OsString};
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
const CANDIDATE_ISOLATION_ENV: &str = "PRODUCT_ATELIER_CANDIDATE_ISOLATION";
const APP_DATA_DIR_ENV: &str = "PRODUCT_ATELIER_DATA_DIR";
const WEBVIEW_DATA_DIR_ENV: &str = "PRODUCT_ATELIER_WEBVIEW_DATA_DIR";
const LEGACY_CONFIG_ENV: &str = "PRODUCT_ATELIER_LEGACY_CONFIG";
const KNOWLEDGE_BASE_ENV: &str = "PRODUCT_ATELIER_KNOWLEDGE_BASE";
const WEBVIEW_DATA_DIR_NAME: &str = "webview2-user-data";
const DISABLED_LEGACY_CONFIG_NAME: &str = "no-legacy-config.json";
const DISABLED_KNOWLEDGE_BASE_NAME: &str = "no-knowledge-vault";
const CONFIG_FILE_NAME: &str = "config.json";
const LEDGER_FILE_NAME: &str = "atelier.sqlite3";
const FIXTURE_MANIFEST_NAME: &str = "formal-webview-fixture.json";
const BUSINESS_DIRECTORY_NAMES: [&str; 2] = ["assets", "output"];
const OFFICIAL_CANDIDATE_DATA_PREFIXES: [&str; 3] = [
    "ProductAtelier-launch-and-shoot-",
    "ProductAtelier-packaged-schema-upgrade-",
    "ProductAtelier-app-test-",
];
#[cfg(windows)]
const WINDOWS_REPARSE_POINT_ATTRIBUTE: u32 = 0x400;
#[cfg(windows)]
const WINDOWS_FILE_SHARE_READ: u32 = 0x1;
#[cfg(windows)]
const WINDOWS_FILE_SHARE_WRITE: u32 = 0x2;
#[cfg(windows)]
const WINDOWS_FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x02000000;
#[cfg(windows)]
const WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x00200000;
#[cfg(windows)]
const WEBVIEW_ISOLATION_GUARD_NAME: &str = ".product-atelier-isolation.guard";

#[derive(Clone, Debug, Eq, PartialEq)]
struct CandidateIsolationConfig {
    data_root: PathBuf,
    webview_data_dir: PathBuf,
    legacy_config_sentinel: PathBuf,
    knowledge_base_dir: PathBuf,
}

#[derive(Debug)]
struct CandidateIsolationGuards {
    #[cfg(windows)]
    _data_root: File,
    #[cfg(windows)]
    _webview_data_dir: File,
    #[cfg(windows)]
    _webview_sentinel: File,
    #[cfg(windows)]
    business_tree: CandidateBusinessTreeGuards,
}

impl CandidateIsolationGuards {
    fn release_startup_replaceable_files(&self) {
        #[cfg(windows)]
        self.business_tree
            .startup_replaceable_files
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clear();
    }
}

#[cfg(windows)]
#[derive(Debug, Default)]
struct CandidateBusinessTreeGuards {
    _directories: Vec<File>,
    _files: Vec<File>,
    // Python persists config.json with os.replace. Hold an identity-stable
    // startup pin until the sidecar has read it, then release only this pin.
    startup_replaceable_files: Mutex<Vec<File>>,
}

#[cfg(windows)]
#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct CandidateBusinessTreePaths {
    directories: Vec<PathBuf>,
    files: Vec<PathBuf>,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsFileInformation {
    attributes: u32,
    volume_serial: u32,
    file_index: u64,
    number_of_links: u32,
    size: u64,
}

fn required_absolute_path(raw: Option<OsString>, env_name: &str) -> Result<PathBuf, String> {
    let raw = raw.ok_or_else(|| format!("{env_name} must be set for candidate isolation"))?;
    let text = raw.to_string_lossy();
    if text.trim().is_empty() {
        return Err(format!("{env_name} must not be empty"));
    }
    if text.as_ref() != text.trim() {
        return Err(format!(
            "{env_name} must not contain surrounding whitespace"
        ));
    }
    let path = PathBuf::from(raw);
    if !path.is_absolute() {
        return Err(format!(
            "{env_name} must be an absolute path: {}",
            path.display()
        ));
    }
    Ok(path)
}

fn path_is_link_like(path: &Path, env_name: &str) -> Result<bool, String> {
    let metadata = std::fs::symlink_metadata(path).map_err(|error| {
        if error.kind() == std::io::ErrorKind::NotFound {
            format!(
                "{env_name} must reference an existing directory ({}): {error}",
                path.display()
            )
        } else {
            format!("could not inspect {env_name} ({}): {error}", path.display())
        }
    })?;
    if metadata.file_type().is_symlink() {
        return Ok(true);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0 {
            return Ok(true);
        }
    }
    Ok(false)
}

#[cfg(windows)]
fn open_isolation_directory_guard(
    path: &Path,
    env_name: &str,
    share_mode: u32,
) -> Result<File, String> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    let guard = OpenOptions::new()
        .read(true)
        .share_mode(share_mode)
        .custom_flags(WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|error| {
            format!(
                "could not lock {env_name} against replacement ({}): {error}",
                path.display()
            )
        })?;
    let metadata = guard.metadata().map_err(|error| {
        format!(
            "could not inspect locked {env_name} ({}): {error}",
            path.display()
        )
    })?;
    if !metadata.is_dir() || metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0 {
        return Err(format!(
            "{env_name} must remain a regular non-reparse directory while locked: {}",
            path.display()
        ));
    }
    let canonical = std::fs::canonicalize(path).map_err(|error| {
        format!(
            "could not revalidate locked {env_name} ({}): {error}",
            path.display()
        )
    })?;
    if canonical != path {
        return Err(format!(
            "{env_name} changed identity while candidate isolation was acquired: {}",
            path.display()
        ));
    }
    let path_guard = OpenOptions::new()
        .read(true)
        .share_mode(share_mode)
        .custom_flags(WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|error| {
            format!(
                "could not re-open locked {env_name} ({}): {error}",
                path.display()
            )
        })?;
    let path_metadata = path_guard.metadata().map_err(|error| {
        format!(
            "could not inspect re-opened {env_name} ({}): {error}",
            path.display()
        )
    })?;
    let held_information = windows_file_information(&guard, &format!("held {env_name}"))?;
    let path_information = windows_file_information(&path_guard, &format!("re-opened {env_name}"))?;
    if !path_metadata.is_dir()
        || path_metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        || held_information.volume_serial != path_information.volume_serial
        || held_information.file_index != path_information.file_index
    {
        return Err(format!(
            "{env_name} changed identity while candidate isolation was acquired: {}",
            path.display()
        ));
    }
    Ok(guard)
}

#[cfg(windows)]
fn windows_file_information(file: &File, label: &str) -> Result<WindowsFileInformation, String> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::Storage::FileSystem::{
        GetFileInformationByHandle, BY_HANDLE_FILE_INFORMATION,
    };

    let mut information = BY_HANDLE_FILE_INFORMATION::default();
    unsafe { GetFileInformationByHandle(HANDLE(file.as_raw_handle()), &mut information) }
        .map_err(|error| format!("could not query {label} file identity: {error}"))?;
    Ok(WindowsFileInformation {
        attributes: information.dwFileAttributes,
        volume_serial: information.dwVolumeSerialNumber,
        file_index: ((information.nFileIndexHigh as u64) << 32) | information.nFileIndexLow as u64,
        number_of_links: information.nNumberOfLinks,
        size: ((information.nFileSizeHigh as u64) << 32) | information.nFileSizeLow as u64,
    })
}

#[cfg(windows)]
fn validate_webview_isolation_sentinel(path: &Path, guard: &File) -> Result<(), String> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    let metadata = guard.metadata().map_err(|error| {
        format!(
            "could not inspect the held WebView isolation sentinel ({}): {error}",
            path.display()
        )
    })?;
    let path_guard = OpenOptions::new()
        .read(true)
        .share_mode(WINDOWS_FILE_SHARE_READ)
        .custom_flags(WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|error| {
            format!(
                "could not re-open the held WebView isolation sentinel ({}): {error}",
                path.display()
            )
        })?;
    let path_metadata = path_guard.metadata().map_err(|error| {
        format!(
            "could not revalidate the WebView isolation sentinel path ({}): {error}",
            path.display()
        )
    })?;
    let information = windows_file_information(guard, "held WebView isolation sentinel")?;
    let path_information =
        windows_file_information(&path_guard, "re-opened WebView isolation sentinel")?;
    let same_file = information.volume_serial == path_information.volume_serial
        && information.file_index == path_information.file_index;
    if !metadata.is_file()
        || information.attributes & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        || path_metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        || information.size != 0
        || information.number_of_links != 1
        || !same_file
    {
        return Err(format!(
            "WebView isolation sentinel must remain the same empty regular non-reparse single-link file: {}",
            path.display()
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn open_webview_isolation_sentinel(webview_data_dir: &Path) -> Result<File, String> {
    use std::os::windows::fs::OpenOptionsExt;

    let path = webview_data_dir.join(WEBVIEW_ISOLATION_GUARD_NAME);
    match OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .share_mode(WINDOWS_FILE_SHARE_READ)
        .custom_flags(WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&path)
    {
        Ok(created) => drop(created),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => {
            return Err(format!(
                "could not create the WebView isolation sentinel ({}): {error}",
                path.display()
            ));
        }
    }

    // The strict WebView directory guard remains held across create/open, so
    // an existing entry cannot be replaced between these operations.
    let guard = OpenOptions::new()
        .read(true)
        .share_mode(WINDOWS_FILE_SHARE_READ)
        .custom_flags(WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&path)
        .map_err(|error| {
            format!(
                "could not open the WebView isolation sentinel without modifying it ({}): {error}",
                path.display()
            )
        })?;
    validate_webview_isolation_sentinel(&path, &guard)?;
    Ok(guard)
}

#[cfg(windows)]
fn collect_candidate_business_directory(
    path: &Path,
    paths: &mut CandidateBusinessTreePaths,
) -> Result<(), String> {
    use std::os::windows::fs::MetadataExt;

    let metadata = std::fs::symlink_metadata(path).map_err(|error| {
        format!(
            "could not inspect candidate business directory ({}): {error}",
            path.display()
        )
    })?;
    if metadata.file_type().is_symlink()
        || metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        || !metadata.is_dir()
    {
        return Err(format!(
            "candidate business directory must be regular and non-reparse: {}",
            path.display()
        ));
    }
    paths.directories.push(path.to_path_buf());

    let mut children = std::fs::read_dir(path)
        .map_err(|error| {
            format!(
                "could not enumerate candidate business directory ({}): {error}",
                path.display()
            )
        })?
        .map(|entry| {
            entry.map(|item| item.path()).map_err(|error| {
                format!(
                    "could not enumerate candidate business directory ({}): {error}",
                    path.display()
                )
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    children.sort();
    for child in children {
        let metadata = std::fs::symlink_metadata(&child).map_err(|error| {
            format!(
                "could not inspect candidate business entry ({}): {error}",
                child.display()
            )
        })?;
        if metadata.file_type().is_symlink()
            || metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        {
            return Err(format!(
                "candidate business entry must be regular and non-reparse: {}",
                child.display()
            ));
        }
        if metadata.is_dir() {
            collect_candidate_business_directory(&child, paths)?;
        } else if metadata.is_file() {
            paths.files.push(child);
        } else {
            return Err(format!(
                "candidate business entry must be a regular file or directory: {}",
                child.display()
            ));
        }
    }
    Ok(())
}

#[cfg(windows)]
fn candidate_business_tree_paths(data_root: &Path) -> Result<CandidateBusinessTreePaths, String> {
    use std::os::windows::fs::MetadataExt;

    let mut paths = CandidateBusinessTreePaths::default();
    for name in [CONFIG_FILE_NAME, LEDGER_FILE_NAME, FIXTURE_MANIFEST_NAME] {
        let path = data_root.join(name);
        match std::fs::symlink_metadata(&path) {
            Ok(metadata) => {
                if metadata.file_type().is_symlink()
                    || metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
                    || !metadata.is_file()
                {
                    return Err(format!(
                        "candidate business file must be regular and non-reparse: {}",
                        path.display()
                    ));
                }
                paths.files.push(path);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(format!(
                    "could not inspect candidate business file ({}): {error}",
                    path.display()
                ));
            }
        }
    }
    for name in BUSINESS_DIRECTORY_NAMES {
        let path = data_root.join(name);
        match std::fs::symlink_metadata(&path) {
            Ok(_) => collect_candidate_business_directory(&path, &mut paths)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(format!(
                    "could not inspect candidate business directory ({}): {error}",
                    path.display()
                ));
            }
        }
    }
    paths.directories.sort();
    paths.files.sort();
    Ok(paths)
}

#[cfg(windows)]
fn validate_candidate_business_file_guard(path: &Path, guard: &File) -> Result<(), String> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    let metadata = guard.metadata().map_err(|error| {
        format!(
            "could not inspect held candidate business file ({}): {error}",
            path.display()
        )
    })?;
    let share_mode = WINDOWS_FILE_SHARE_READ | WINDOWS_FILE_SHARE_WRITE;
    let path_guard = OpenOptions::new()
        .read(true)
        .share_mode(share_mode)
        .custom_flags(WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|error| {
            format!(
                "could not re-open held candidate business file ({}): {error}",
                path.display()
            )
        })?;
    let path_metadata = path_guard.metadata().map_err(|error| {
        format!(
            "could not inspect re-opened candidate business file ({}): {error}",
            path.display()
        )
    })?;
    let held_information = windows_file_information(guard, "held candidate business file")?;
    let path_information =
        windows_file_information(&path_guard, "re-opened candidate business file")?;
    let same_file = held_information.volume_serial == path_information.volume_serial
        && held_information.file_index == path_information.file_index;
    if !metadata.is_file()
        || !path_metadata.is_file()
        || metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        || path_metadata.file_attributes() & WINDOWS_REPARSE_POINT_ATTRIBUTE != 0
        || held_information.number_of_links != 1
        || path_information.number_of_links != 1
        || !same_file
    {
        return Err(format!(
            "candidate business file must remain the same regular non-reparse single-link file: {}",
            path.display()
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn open_candidate_business_file_guard(path: &Path) -> Result<File, String> {
    use std::os::windows::fs::OpenOptionsExt;

    let guard = OpenOptions::new()
        .read(true)
        .share_mode(WINDOWS_FILE_SHARE_READ | WINDOWS_FILE_SHARE_WRITE)
        .custom_flags(WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(|error| {
            format!(
                "could not pin candidate business file ({}): {error}",
                path.display()
            )
        })?;
    validate_candidate_business_file_guard(path, &guard)?;
    Ok(guard)
}

#[cfg(windows)]
fn acquire_candidate_business_tree_guards(
    data_root: &Path,
) -> Result<CandidateBusinessTreeGuards, String> {
    let initial_paths = candidate_business_tree_paths(data_root)?;
    let share_mode = WINDOWS_FILE_SHARE_READ | WINDOWS_FILE_SHARE_WRITE;
    let mut directory_guards = Vec::with_capacity(initial_paths.directories.len());
    for path in &initial_paths.directories {
        directory_guards.push((
            path.clone(),
            open_isolation_directory_guard(path, "candidate business directory", share_mode)?,
        ));
    }
    let mut file_guards = Vec::with_capacity(initial_paths.files.len());
    for path in &initial_paths.files {
        file_guards.push((path.clone(), open_candidate_business_file_guard(path)?));
    }

    let reacquired_paths = candidate_business_tree_paths(data_root)?;
    if reacquired_paths != initial_paths {
        return Err(
            "candidate business tree changed while startup isolation was acquired".to_string(),
        );
    }
    for (path, guard) in &file_guards {
        validate_candidate_business_file_guard(path, guard)?;
    }

    let config_path = data_root.join(CONFIG_FILE_NAME);
    let mut startup_replaceable_files = Vec::new();
    let mut pinned_files = Vec::new();
    for (path, guard) in file_guards {
        if path == config_path {
            startup_replaceable_files.push(guard);
        } else {
            pinned_files.push(guard);
        }
    }
    Ok(CandidateBusinessTreeGuards {
        _directories: directory_guards
            .into_iter()
            .map(|(_, guard)| guard)
            .collect(),
        _files: pinned_files,
        startup_replaceable_files: Mutex::new(startup_replaceable_files),
    })
}

fn acquire_candidate_isolation_guards(
    config: &CandidateIsolationConfig,
) -> Result<CandidateIsolationGuards, String> {
    #[cfg(windows)]
    {
        let strict_data_root = open_isolation_directory_guard(
            &config.data_root,
            APP_DATA_DIR_ENV,
            WINDOWS_FILE_SHARE_READ,
        )?;
        let strict_webview_data_dir = open_isolation_directory_guard(
            &config.webview_data_dir,
            WEBVIEW_DATA_DIR_ENV,
            WINDOWS_FILE_SHARE_READ,
        )?;
        let business_tree = acquire_candidate_business_tree_guards(&config.data_root)?;
        let webview_sentinel = open_webview_isolation_sentinel(&config.webview_data_dir)?;
        let runtime_share = WINDOWS_FILE_SHARE_READ | WINDOWS_FILE_SHARE_WRITE;
        let data_root =
            open_isolation_directory_guard(&config.data_root, APP_DATA_DIR_ENV, runtime_share)?;
        let webview_data_dir = open_isolation_directory_guard(
            &config.webview_data_dir,
            WEBVIEW_DATA_DIR_ENV,
            runtime_share,
        )?;
        drop(strict_webview_data_dir);
        drop(strict_data_root);
        return Ok(CandidateIsolationGuards {
            _data_root: data_root,
            _webview_data_dir: webview_data_dir,
            _webview_sentinel: webview_sentinel,
            business_tree,
        });
    }
    #[cfg(not(windows))]
    {
        let _ = config;
        Ok(CandidateIsolationGuards {})
    }
}

fn canonical_existing_regular_directory(
    raw: Option<OsString>,
    env_name: &str,
) -> Result<PathBuf, String> {
    let path = required_absolute_path(raw, env_name)?;
    if path_is_link_like(&path, env_name)? {
        return Err(format!(
            "{env_name} must be a regular non-reparse directory: {}",
            path.display()
        ));
    }
    let canonical = std::fs::canonicalize(&path).map_err(|error| {
        format!(
            "{env_name} must reference an existing directory ({}): {error}",
            path.display()
        )
    })?;
    if !canonical.is_dir() {
        return Err(format!(
            "{env_name} must reference a directory: {}",
            canonical.display()
        ));
    }
    Ok(canonical)
}

fn path_component_eq(left: &OsStr, right: &OsStr) -> bool {
    #[cfg(windows)]
    {
        left.to_string_lossy()
            .eq_ignore_ascii_case(&right.to_string_lossy())
    }
    #[cfg(not(windows))]
    {
        left == right
    }
}

fn path_starts_with(path: &Path, root: &Path) -> bool {
    let path_components = path.components().collect::<Vec<_>>();
    let root_components = root.components().collect::<Vec<_>>();
    root_components.len() <= path_components.len()
        && path_components
            .iter()
            .zip(root_components.iter())
            .all(|(left, right)| path_component_eq(left.as_os_str(), right.as_os_str()))
}

fn paths_overlap(first: &Path, second: &Path) -> bool {
    path_starts_with(first, second) || path_starts_with(second, first)
}

fn canonicalize_allow_missing(path: &Path) -> Result<PathBuf, String> {
    let mut cursor = path.to_path_buf();
    let mut missing = Vec::<OsString>::new();
    loop {
        match std::fs::canonicalize(&cursor) {
            Ok(mut canonical) => {
                for component in missing.iter().rev() {
                    canonical.push(component);
                }
                return Ok(canonical);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let name = cursor.file_name().ok_or_else(|| {
                    format!("could not canonicalize protected path {}", path.display())
                })?;
                missing.push(name.to_owned());
                cursor = cursor
                    .parent()
                    .ok_or_else(|| {
                        format!("could not canonicalize protected path {}", path.display())
                    })?
                    .to_path_buf();
            }
            Err(error) => {
                return Err(format!(
                    "could not canonicalize protected path {}: {error}",
                    path.display()
                ));
            }
        }
    }
}

fn validate_legacy_config_sentinel(
    raw: Option<OsString>,
    data_root: &Path,
) -> Result<PathBuf, String> {
    let path = required_absolute_path(raw, LEGACY_CONFIG_ENV)?;
    match std::fs::symlink_metadata(&path) {
        Ok(_) => {
            return Err(format!(
                "{LEGACY_CONFIG_ENV} must be a non-existing sentinel: {}",
                path.display()
            ));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(format!(
                "could not inspect {LEGACY_CONFIG_ENV} ({}): {error}",
                path.display()
            ));
        }
    }
    if path.file_name() != Some(OsStr::new(DISABLED_LEGACY_CONFIG_NAME)) {
        return Err(format!(
            "{LEGACY_CONFIG_ENV} must be named {DISABLED_LEGACY_CONFIG_NAME}: {}",
            path.display()
        ));
    }
    let parent = path
        .parent()
        .ok_or_else(|| format!("{LEGACY_CONFIG_ENV} must have an existing parent directory"))?;
    let canonical_parent = std::fs::canonicalize(parent).map_err(|error| {
        format!(
            "{LEGACY_CONFIG_ENV} parent must be an existing directory ({}): {error}",
            parent.display()
        )
    })?;
    if canonical_parent != data_root {
        return Err(format!(
            "{LEGACY_CONFIG_ENV} must be the direct disabled-config sentinel of {APP_DATA_DIR_ENV}"
        ));
    }
    Ok(data_root.join(DISABLED_LEGACY_CONFIG_NAME))
}

fn validate_candidate_knowledge_base(
    raw: Option<OsString>,
    data_root: &Path,
) -> Result<PathBuf, String> {
    let path = canonical_existing_regular_directory(raw, KNOWLEDGE_BASE_ENV)?;
    if path.file_name() != Some(OsStr::new(DISABLED_KNOWLEDGE_BASE_NAME)) {
        return Err(format!(
            "{KNOWLEDGE_BASE_ENV} must be named {DISABLED_KNOWLEDGE_BASE_NAME}: {}",
            path.display()
        ));
    }
    if path.parent() != Some(data_root) {
        return Err(format!(
            "{KNOWLEDGE_BASE_ENV} must resolve to the direct {DISABLED_KNOWLEDGE_BASE_NAME} child of {APP_DATA_DIR_ENV}"
        ));
    }
    Ok(path)
}

fn validate_candidate_temp_boundary(data_root: &Path) -> Result<(), String> {
    let temp_root_raw = std::env::temp_dir();
    if path_is_link_like(&temp_root_raw, "system temporary directory")? {
        return Err(format!(
            "system temporary directory must be regular and non-reparse: {}",
            temp_root_raw.display()
        ));
    }
    let temp_root = std::fs::canonicalize(&temp_root_raw).map_err(|error| {
        format!(
            "could not canonicalize system temporary directory ({}): {error}",
            temp_root_raw.display()
        )
    })?;
    let name = data_root
        .file_name()
        .and_then(OsStr::to_str)
        .ok_or_else(|| {
            format!("{APP_DATA_DIR_ENV} must have an ASCII launcher-owned directory name")
        })?;
    let suffix = OFFICIAL_CANDIDATE_DATA_PREFIXES
        .iter()
        .find_map(|prefix| name.strip_prefix(prefix));
    let valid_suffix = suffix.is_some_and(|value| {
        value.len() >= 8
            && !value.starts_with("cleanup-")
            && value.chars().all(|character| {
                character.is_ascii_alphanumeric() || matches!(character, '-' | '_')
            })
    });
    if data_root.parent() != Some(temp_root.as_path()) || !valid_suffix {
        return Err(format!(
            "{APP_DATA_DIR_ENV} must be an official randomized direct child of the system temporary directory ({})",
            temp_root.display()
        ));
    }
    Ok(())
}

fn candidate_isolation_requested(
    marker_raw: Option<OsString>,
    webview_raw: &Option<OsString>,
) -> Result<bool, String> {
    match marker_raw {
        None => Ok(webview_raw.is_some()),
        Some(value) if value.as_os_str() == OsStr::new("1") => Ok(true),
        Some(_) => Err(format!(
            "{CANDIDATE_ISOLATION_ENV} must be exactly 1 when present"
        )),
    }
}

fn select_candidate_isolation_config(
    data_root_raw: Option<OsString>,
    webview_raw: Option<OsString>,
    legacy_raw: Option<OsString>,
    knowledge_raw: Option<OsString>,
    protected_roots: &[PathBuf],
) -> Result<CandidateIsolationConfig, String> {
    let data_root = canonical_existing_regular_directory(data_root_raw, APP_DATA_DIR_ENV)?;
    // Missing business entries cannot be pinned before the sidecar creates
    // them. Restrict strict mode to the randomized private roots created by
    // official launchers so that this same-user creation race stays bounded.
    validate_candidate_temp_boundary(&data_root)?;
    for protected in protected_roots {
        let canonical_protected = canonicalize_allow_missing(protected)?;
        if paths_overlap(&data_root, &canonical_protected) {
            return Err(format!(
                "{APP_DATA_DIR_ENV} overlaps protected Product Atelier data: {}",
                canonical_protected.display()
            ));
        }
    }
    let webview = canonical_existing_regular_directory(webview_raw, WEBVIEW_DATA_DIR_ENV)?;
    if webview.file_name() != Some(OsStr::new(WEBVIEW_DATA_DIR_NAME)) {
        return Err(format!(
            "{WEBVIEW_DATA_DIR_ENV} must be named {WEBVIEW_DATA_DIR_NAME}: {}",
            webview.display()
        ));
    }
    if webview.parent() != Some(data_root.as_path()) {
        return Err(format!(
            "{WEBVIEW_DATA_DIR_ENV} must resolve to the direct {WEBVIEW_DATA_DIR_NAME} child of {APP_DATA_DIR_ENV}"
        ));
    }
    let legacy_config_sentinel = validate_legacy_config_sentinel(legacy_raw, &data_root)?;
    let knowledge_base_dir = validate_candidate_knowledge_base(knowledge_raw, &data_root)?;
    Ok(CandidateIsolationConfig {
        data_root,
        webview_data_dir: webview,
        legacy_config_sentinel,
        knowledge_base_dir,
    })
}

fn select_candidate_isolation(
    marker_raw: Option<OsString>,
    data_root_raw: Option<OsString>,
    webview_raw: Option<OsString>,
    legacy_raw: Option<OsString>,
    knowledge_raw: Option<OsString>,
    protected_roots: &[PathBuf],
) -> Result<Option<CandidateIsolationConfig>, String> {
    if !candidate_isolation_requested(marker_raw, &webview_raw)? {
        return Ok(None);
    }
    select_candidate_isolation_config(
        data_root_raw,
        webview_raw,
        legacy_raw,
        knowledge_raw,
        protected_roots,
    )
    .map(Some)
}

fn default_app_data_directory() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|path| path.join("AppData").join("Roaming")))
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
        return base.join("ProductAtelier");
    }
    #[cfg(target_os = "macos")]
    {
        return home_dir()
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
            .join("Library")
            .join("Application Support")
            .join("ProductAtelier");
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        let base = std::env::var_os("XDG_DATA_HOME")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|path| path.join(".local").join("share")))
            .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
        base.join("ProductAtelier")
    }
}

fn default_legacy_config_directory() -> Option<PathBuf> {
    home_dir().map(|path| path.join(".codex").join("skills").join("lk-ai-image"))
}

fn default_knowledge_vault_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    #[cfg(windows)]
    if Path::new("D:/").is_dir() {
        roots.push(PathBuf::from("D:/\u{77e5}\u{8bc6}\u{5e93}"));
    }
    if let Some(home) = home_dir() {
        roots.push(home.join("Documents").join("\u{77e5}\u{8bc6}\u{5e93}"));
    }
    roots
}

fn candidate_protected_roots() -> Vec<PathBuf> {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("Cargo manifest directory must have a project parent")
        .to_path_buf();
    let mut roots = vec![
        default_app_data_directory(),
        project_root.clone(),
        project_root.join("release"),
        project_root.join("release").join("ProductAtelier-Portable"),
    ];
    if let Some(legacy_directory) = default_legacy_config_directory() {
        roots.push(legacy_directory);
    }
    roots.extend(default_knowledge_vault_roots());
    roots
}

fn take_isolated_window_config(
    windows: &mut [tauri::utils::config::WindowConfig],
    label: &str,
) -> Result<tauri::utils::config::WindowConfig, String> {
    let config = windows
        .iter_mut()
        .find(|config| config.label == label && config.create)
        .ok_or_else(|| format!("could not find auto-created Tauri window `{label}`"))?;
    let isolated_config = config.clone();
    config.create = false;
    Ok(isolated_config)
}

// ---- State ----
struct AppState {
    python_child: Mutex<Option<Child>>,
    api_port: Mutex<u16>,
    config: Mutex<AppConfig>,
    started_at: Instant,
    sidecar_starting: AtomicBool,
    shutting_down: AtomicBool,
    _candidate_isolation_guards: Option<Arc<CandidateIsolationGuards>>,
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
    if let Some(override_dir) = std::env::var_os(APP_DATA_DIR_ENV) {
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

fn read_app_config(path: &Path) -> Option<AppConfig> {
    if path.exists() {
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(cfg) = serde_json::from_str::<AppConfig>(&text) {
                return Some(cfg);
            }
        }
    }
    None
}

fn resolve_legacy_config_path(
    override_path: Option<OsString>,
    home: Option<PathBuf>,
) -> Option<PathBuf> {
    match override_path {
        // An explicitly empty override disables legacy fallback. Candidate
        // verification uses this contract to avoid reading a real user key.
        Some(value) if value.is_empty() => None,
        Some(value) => Some(PathBuf::from(value)),
        None => home.map(|path| path.join(r".codex\skills\lk-ai-image\config.json")),
    }
}

fn legacy_config_path() -> Option<PathBuf> {
    resolve_legacy_config_path(
        std::env::var_os("PRODUCT_ATELIER_LEGACY_CONFIG"),
        home_dir(),
    )
}

fn load_config_from_paths(path: &Path, legacy: Option<&Path>) -> AppConfig {
    if let Some(config) = read_app_config(path) {
        return config;
    }
    if let Some(legacy) = legacy {
        if let Ok(text) = std::fs::read_to_string(legacy) {
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
                if let Some(key) = value.get("api_key").and_then(|item| item.as_str()) {
                    return AppConfig {
                        api_key: key.to_string(),
                        ..Default::default()
                    };
                }
            }
        }
    }
    AppConfig::default()
}

fn load_config_for_runtime(
    path: &Path,
    legacy: Option<&Path>,
    candidate_isolation: bool,
) -> AppConfig {
    load_config_from_paths(path, if candidate_isolation { None } else { legacy })
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
    std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .ok()
        .map(PathBuf::from)
}

fn find_free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .ok()
        .and_then(|l| l.local_addr().ok().map(|a| a.port()))
        .unwrap_or(8765)
}

fn current_exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
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
    log_msg(&format!(
        "[ProductAtelier] Found server at: {} (exe={})",
        server_path.display(),
        is_exe
    ));

    if is_exe {
        let mut cmd = Command::new(&server_path);
        cmd.arg(port.to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null());
        apply_no_window_flags(&mut cmd);
        if let Ok(child) = cmd.spawn() {
            log_msg(&format!(
                "[ProductAtelier] Started compiled server (pid={}, port={})",
                child.id(),
                port
            ));
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
                log_msg(&format!(
                    "[ProductAtelier] Started Python via {} (pid={}, port={})",
                    python_cmd,
                    child.id(),
                    port
                ));
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

fn wait_for_server<F>(port: u16, timeout_secs: u64, on_listening: F) -> bool
where
    F: FnOnce(),
{
    let start = std::time::Instant::now();
    let mut on_listening = Some(on_listening);
    while start.elapsed().as_secs() < timeout_secs {
        if TcpStream::connect(format!("127.0.0.1:{}", port)).is_ok() {
            if let Some(callback) = on_listening.take() {
                // Binding happens after Python module initialization has read
                // the startup config. Release its no-replace pin before the
                // health endpoint performs an atomic config update.
                callback();
            }
            if let Ok(mut stream) = TcpStream::connect(format!("127.0.0.1:{}", port)) {
                let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(3)));
                let req = format!(
                    "GET /api/health HTTP/1.0\r\nHost:127.0.0.1:{}\r\nConnection:close\r\n\r\n",
                    port
                );
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
fn ensure_python_sidecar(app: tauri::AppHandle, state: State<AppState>) -> Result<u16, String> {
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
fn save_base64_image(
    app: tauri::AppHandle,
    suggested_name: String,
    data_b64: String,
) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;
    let bytes = base64_decode(&data_b64).map_err(|e| e.to_string())?;
    let ext = if suggested_name.ends_with(".png") {
        "png"
    } else {
        "jpg"
    };
    let (tx, rx) = std::sync::mpsc::channel();
    let tx = Arc::new(Mutex::new(Some(tx)));
    app.dialog()
        .file()
        .set_file_name(&suggested_name)
        .add_filter("图片文件", &[ext])
        .save_file(move |path| {
            let _ = tx
                .lock()
                .unwrap()
                .take()
                .unwrap()
                .send(path.map(|p| p.to_string()));
        });
    let path_str = rx
        .recv()
        .map_err(|e| e.to_string())?
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
        p.parent()
            .map(|d| d.to_path_buf())
            .unwrap_or_else(|| p.clone())
    } else {
        p.clone()
    };
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer")
            .arg(target)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("-R")
            .arg(&p)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        Command::new("xdg-open")
            .arg(target)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn select_folder_dialog(app: tauri::AppHandle) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = std::sync::mpsc::channel();
    let tx = Arc::new(Mutex::new(Some(tx)));
    app.dialog().file().pick_folder(move |path| {
        let _ = tx
            .lock()
            .unwrap()
            .take()
            .unwrap()
            .send(path.map(|p| p.to_string()));
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
    use base64::{engine::general_purpose::STANDARD, Engine};
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
    let monitor = win
        .current_monitor()
        .map_err(|e| e.to_string())?
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
    win.set_always_on_top(always_on_top)
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn set_window_pos_size(
    app: tauri::AppHandle,
    x: i32,
    y: i32,
    w: u32,
    h: u32,
) -> Result<(), String> {
    let win = app.get_webview_window("main").ok_or("Window not found")?;
    // get_monitor_info/get_window_position/get_window_size all report physical
    // pixels. Keep this paired setter in the same coordinate space so docking
    // remains correct on 125%/150% and mixed-DPI monitors.
    win.set_size(Size::Physical(PhysicalSize {
        width: w,
        height: h,
    }))
    .map_err(|e| e.to_string())?;
    win.set_position(Position::Physical(PhysicalPosition { x, y }))
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(windows)]
fn apply_windows_window_chrome<R: tauri::Runtime>(window: &tauri::Window<R>) -> Result<(), String> {
    use std::ffi::c_void;
    use windows::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND,
    };
    use windows::Win32::Graphics::Gdi::SetWindowRgn;

    let hwnd = window.hwnd().map_err(|error| error.to_string())?;
    // Clear the legacy GDI region first. HRGN clipping is binary and produced
    // visible stair-step edges at fractional DPI. Windows 11 DWM owns the
    // composited outer edge; older Windows versions safely fall back to a
    // rectangular opaque window if the corner attribute is unsupported.
    unsafe {
        SetWindowRgn(hwnd, None, true);
    }
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
fn apply_windows_window_chrome<R: tauri::Runtime>(
    _window: &tauri::Window<R>,
) -> Result<(), String> {
    Ok(())
}

fn log_window_metrics<R: tauri::Runtime>(window: &tauri::Window<R>) {
    let Ok(scale) = window.scale_factor() else {
        return;
    };
    let Ok(size) = window.outer_size() else {
        return;
    };
    let logical_width = size.width as f64 / scale;
    let logical_height = size.height as f64 / scale;
    log_msg(&format!(
        "[ProductAtelier] Window metrics: scale={scale:.2}, physical={}x{}, logical={logical_width:.1}x{logical_height:.1}",
        size.width, size.height
    ));
}

fn main() {
    let process_started_at = Instant::now();
    let mut context = tauri::generate_context!();
    let candidate_isolation = select_candidate_isolation(
        std::env::var_os(CANDIDATE_ISOLATION_ENV),
        std::env::var_os(APP_DATA_DIR_ENV),
        std::env::var_os(WEBVIEW_DATA_DIR_ENV),
        std::env::var_os(LEGACY_CONFIG_ENV),
        std::env::var_os(KNOWLEDGE_BASE_ENV),
        &candidate_protected_roots(),
    )
    .unwrap_or_else(|error| panic!("invalid candidate isolation: {error}"));
    let candidate_isolation_guards = candidate_isolation
        .as_ref()
        .map(acquire_candidate_isolation_guards)
        .transpose()
        .unwrap_or_else(|error| panic!("could not lock candidate isolation: {error}"))
        .map(Arc::new);
    if let Some(isolation) = &candidate_isolation {
        std::env::set_var(CANDIDATE_ISOLATION_ENV, "1");
        std::env::set_var(APP_DATA_DIR_ENV, &isolation.data_root);
        std::env::set_var(WEBVIEW_DATA_DIR_ENV, &isolation.webview_data_dir);
        std::env::set_var(LEGACY_CONFIG_ENV, &isolation.legacy_config_sentinel);
        std::env::set_var(KNOWLEDGE_BASE_ENV, &isolation.knowledge_base_dir);
    }
    let candidate_isolation_enabled = candidate_isolation.is_some();
    let isolated_main_window = candidate_isolation.as_ref().map(|isolation| {
        let window_config =
            take_isolated_window_config(&mut context.config_mut().app.windows, "main")
                .unwrap_or_else(|error| panic!("could not isolate candidate WebView: {error}"));
        (window_config, isolation.webview_data_dir.clone())
    });
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            if let Some((window_config, data_directory)) = &isolated_main_window {
                tauri::WebviewWindowBuilder::from_config(app.handle(), window_config)?
                    .data_directory(data_directory.clone())
                    .build()?;
                log_msg(&format!(
                    "[ProductAtelier] Candidate WebView data directory: {}",
                    data_directory.display()
                ));
            }
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
                    log_msg(&format!(
                        "[ProductAtelier] WARNING: Could not apply Windows chrome: {error}"
                    ));
                }
                log_window_metrics(&window);
            }
            let config = config_path();
            let legacy = legacy_config_path();
            let cfg =
                load_config_for_runtime(&config, legacy.as_deref(), candidate_isolation_enabled);
            let port = find_free_port();
            app.manage(AppState {
                python_child: Mutex::new(None),
                api_port: Mutex::new(port),
                config: Mutex::new(cfg),
                started_at: process_started_at,
                sidecar_starting: AtomicBool::new(true),
                shutting_down: AtomicBool::new(false),
                _candidate_isolation_guards: candidate_isolation_guards.clone(),
            });
            let handle = app.handle().clone();
            let startup_isolation_guards = candidate_isolation_guards.clone();
            std::thread::spawn(move || {
                let release_startup_replaceable_files = || {
                    if let Some(guards) = &startup_isolation_guards {
                        guards.release_startup_replaceable_files();
                    }
                };
                if handle
                    .state::<AppState>()
                    .shutting_down
                    .load(Ordering::SeqCst)
                {
                    release_startup_replaceable_files();
                    handle
                        .state::<AppState>()
                        .sidecar_starting
                        .store(false, Ordering::SeqCst);
                    return;
                }
                let Some(mut child) = start_python_sidecar(port, &handle) else {
                    release_startup_replaceable_files();
                    log_msg("[ProductAtelier] WARNING: Failed to start Python sidecar");
                    handle
                        .state::<AppState>()
                        .sidecar_starting
                        .store(false, Ordering::SeqCst);
                    return;
                };
                let state = handle.state::<AppState>();
                let mut child_slot = state.python_child.lock().unwrap();
                if state.shutting_down.load(Ordering::SeqCst) {
                    drop(child_slot);
                    let _ = child.kill();
                    let _ = child.wait();
                    release_startup_replaceable_files();
                    state.sidecar_starting.store(false, Ordering::SeqCst);
                    return;
                }
                *child_slot = Some(child);
                drop(child_slot);
                state.sidecar_starting.store(false, Ordering::SeqCst);
                log_msg(&format!(
                    "[ProductAtelier] Waiting for backend on port {}...",
                    port
                ));
                let server_ready = wait_for_server(port, 45, || {
                    release_startup_replaceable_files();
                });
                release_startup_replaceable_files();
                if !server_ready {
                    log_msg("[ProductAtelier] WARNING: Backend not ready after 45s");
                } else {
                    log_msg(&format!("[ProductAtelier] Backend ready on port {}", port));
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_api_port,
            ensure_python_sidecar,
            report_startup_milestone,
            get_app_config,
            set_app_config,
            save_base64_image,
            save_binary_asset,
            open_in_folder,
            select_folder_dialog,
            verify_folder_exists,
            close_app,
            complete_close_app,
            get_window_position,
            get_window_size,
            get_monitor_info,
            get_window_metrics,
            set_window_always_on_top,
            set_window_pos_size
        ])
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::Destroyed if window.label() == "main" => {
                let state = window.state::<AppState>();
                shutdown_sidecar(&state);
            }
            tauri::WindowEvent::Focused(_) => {}
            _ => {}
        })
        .run(context)
        .expect("error while running Product Atelier");
}

#[cfg(test)]
mod binary_asset_export_tests;

#[cfg(test)]
mod webview_isolation_tests;

#[cfg(test)]
mod config_isolation_tests;
