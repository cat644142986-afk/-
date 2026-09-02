use super::{
    acquire_candidate_isolation_guards, candidate_protected_roots, default_knowledge_vault_roots,
    default_legacy_config_directory, select_candidate_isolation, take_isolated_window_config,
    wait_for_server, CandidateIsolationConfig, CONFIG_FILE_NAME, DISABLED_KNOWLEDGE_BASE_NAME,
    DISABLED_LEGACY_CONFIG_NAME, FIXTURE_MANIFEST_NAME, LEDGER_FILE_NAME, WEBVIEW_DATA_DIR_NAME,
};
#[cfg(windows)]
use super::{windows_file_information, WEBVIEW_ISOLATION_GUARD_NAME};
use std::ffi::{OsStr, OsString};
#[cfg(windows)]
use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tauri::utils::config::WindowConfig;

static TEMP_SEQUENCE: AtomicU64 = AtomicU64::new(0);

struct TestDirectory {
    path: PathBuf,
}

impl TestDirectory {
    fn new(label: &str) -> Self {
        Self::with_prefix("ProductAtelier-launch-and-shoot-", label)
    }

    fn with_prefix(prefix: &str, label: &str) -> Self {
        let sequence = TEMP_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "{prefix}rust{:08x}{sequence:08x}-{label}",
            std::process::id(),
        ));
        std::fs::create_dir(&path).unwrap();
        std::fs::create_dir(path.join(DISABLED_KNOWLEDGE_BASE_NAME)).unwrap();
        Self { path }
    }

    fn child(&self, name: &str) -> PathBuf {
        self.path.join(name)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        assert!(self.path.starts_with(std::env::temp_dir()));
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

fn legacy_sentinel(data_root: &Path) -> PathBuf {
    data_root.join(DISABLED_LEGACY_CONFIG_NAME)
}

fn knowledge_base(data_root: &Path) -> PathBuf {
    data_root.join(DISABLED_KNOWLEDGE_BASE_NAME)
}

fn select(
    data_root: Option<&Path>,
    webview: Option<&Path>,
    legacy: Option<&Path>,
    protected_roots: &[PathBuf],
) -> Result<Option<CandidateIsolationConfig>, String> {
    select_candidate_isolation(
        Some(OsString::from("1")),
        data_root.map(|path| path.as_os_str().to_owned()),
        webview.map(|path| path.as_os_str().to_owned()),
        legacy.map(|path| path.as_os_str().to_owned()),
        data_root.map(|path| knowledge_base(path).into_os_string()),
        protected_roots,
    )
}

fn select_with_marker(
    marker: Option<&OsStr>,
    data_root: Option<&Path>,
    webview: Option<&Path>,
    legacy: Option<&Path>,
    protected_roots: &[PathBuf],
) -> Result<Option<CandidateIsolationConfig>, String> {
    select_candidate_isolation(
        marker.map(OsStr::to_owned),
        data_root.map(|path| path.as_os_str().to_owned()),
        webview.map(|path| path.as_os_str().to_owned()),
        legacy.map(|path| path.as_os_str().to_owned()),
        data_root.map(|path| knowledge_base(path).into_os_string()),
        protected_roots,
    )
}

#[test]
fn no_isolation_overrides_preserve_formal_mode() {
    assert_eq!(
        select_with_marker(None, None, None, None, &[]).unwrap(),
        None
    );
}

#[test]
fn formal_data_and_legacy_overrides_do_not_request_candidate_isolation() {
    let cases = [
        (Some(OsString::from("relative-formal-data")), None),
        (Some(OsString::new()), None),
        (None, Some(OsString::from("relative-formal-legacy.json"))),
        (None, Some(OsString::new())),
        (
            Some(OsString::from("relative-formal-data")),
            Some(OsString::new()),
        ),
    ];
    for (data, legacy) in cases {
        let selected = select_candidate_isolation(None, data, None, legacy, None, &[])
            .expect("formal overrides must bypass strict candidate validation");
        assert_eq!(selected, None);
    }
}

#[test]
fn webview_override_requests_strict_isolation_without_a_marker() {
    let data_root = TestDirectory::new("webview-trigger");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    let selected = select_with_marker(
        None,
        Some(&data_root.path),
        Some(&webview),
        Some(&legacy),
        &[],
    )
    .unwrap();

    assert!(selected.is_some());
}

#[test]
fn invalid_candidate_markers_fail_closed() {
    let data_root = TestDirectory::new("invalid-marker");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    for marker in ["", "0", "true", " 1", "1 "] {
        let error = select_with_marker(
            Some(OsStr::new(marker)),
            Some(&data_root.path),
            Some(&webview),
            Some(&legacy),
            &[],
        )
        .expect_err("an invalid explicit candidate marker must fail closed");
        assert!(
            error.contains("must be exactly 1"),
            "unexpected error for {marker:?}: {error}"
        );
    }
}

#[test]
fn isolation_requires_all_four_path_environment_values() {
    let data_root = TestDirectory::new("complete-group");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    for (data, web, old_config, missing_name) in [
        (
            None,
            Some(webview.as_path()),
            Some(legacy.as_path()),
            "PRODUCT_ATELIER_DATA_DIR",
        ),
        (
            Some(data_root.path.as_path()),
            None,
            Some(legacy.as_path()),
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR",
        ),
        (
            Some(data_root.path.as_path()),
            Some(webview.as_path()),
            None,
            "PRODUCT_ATELIER_LEGACY_CONFIG",
        ),
    ] {
        let error = select(data, web, old_config, &[])
            .expect_err("an incomplete isolation group must fail closed");
        assert!(error.contains(missing_name), "unexpected error: {error}");
    }

    let error = select_candidate_isolation(
        Some(OsString::from("1")),
        Some(data_root.path.clone().into_os_string()),
        Some(webview.into_os_string()),
        Some(legacy.into_os_string()),
        None,
        &[],
    )
    .expect_err("a missing candidate knowledge base must fail closed");
    assert!(
        error.contains("PRODUCT_ATELIER_KNOWLEDGE_BASE"),
        "unexpected error: {error}"
    );
}

#[test]
fn isolation_rejects_empty_environment_values() {
    let data_root = TestDirectory::new("empty-env");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    let cases = [
        (
            Some(OsString::from("   ")),
            Some(webview.clone().into_os_string()),
            Some(legacy.clone().into_os_string()),
            "PRODUCT_ATELIER_DATA_DIR",
        ),
        (
            Some(data_root.path.clone().into_os_string()),
            Some(OsString::new()),
            Some(legacy.clone().into_os_string()),
            "PRODUCT_ATELIER_WEBVIEW_DATA_DIR",
        ),
        (
            Some(data_root.path.clone().into_os_string()),
            Some(webview.clone().into_os_string()),
            Some(OsString::new()),
            "PRODUCT_ATELIER_LEGACY_CONFIG",
        ),
    ];
    for (data, web, old_config, env_name) in cases {
        let error = select_candidate_isolation(
            Some(OsString::from("1")),
            data,
            web,
            old_config,
            Some(knowledge_base(&data_root.path).into_os_string()),
            &[],
        )
        .expect_err("empty isolation values must fail closed");
        assert!(error.contains(env_name), "unexpected error: {error}");
        assert!(error.contains("empty"), "unexpected error: {error}");
    }

    let error = select_candidate_isolation(
        Some(OsString::from("1")),
        Some(data_root.path.clone().into_os_string()),
        Some(webview.into_os_string()),
        Some(legacy.into_os_string()),
        Some(OsString::new()),
        &[],
    )
    .expect_err("an empty candidate knowledge base must fail closed");
    assert!(
        error.contains("PRODUCT_ATELIER_KNOWLEDGE_BASE") && error.contains("empty"),
        "unexpected error: {error}"
    );
}

#[test]
fn isolation_rejects_relative_paths_for_every_environment_value() {
    let data_root = TestDirectory::new("relative-env");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    let cases = [
        (
            Some(OsString::from("relative-data")),
            Some(webview.clone().into_os_string()),
            Some(legacy.clone().into_os_string()),
        ),
        (
            Some(data_root.path.clone().into_os_string()),
            Some(OsString::from(WEBVIEW_DATA_DIR_NAME)),
            Some(legacy.clone().into_os_string()),
        ),
        (
            Some(data_root.path.clone().into_os_string()),
            Some(webview.clone().into_os_string()),
            Some(OsString::from(DISABLED_LEGACY_CONFIG_NAME)),
        ),
    ];
    for (data, web, old_config) in cases {
        let error = select_candidate_isolation(
            Some(OsString::from("1")),
            data,
            web,
            old_config,
            Some(knowledge_base(&data_root.path).into_os_string()),
            &[],
        )
        .expect_err("relative isolation paths must fail closed");
        assert!(error.contains("absolute path"), "unexpected error: {error}");
    }

    let error = select_candidate_isolation(
        Some(OsString::from("1")),
        Some(data_root.path.clone().into_os_string()),
        Some(webview.into_os_string()),
        Some(legacy.into_os_string()),
        Some(OsString::from(DISABLED_KNOWLEDGE_BASE_NAME)),
        &[],
    )
    .expect_err("a relative candidate knowledge base must fail closed");
    assert!(
        error.contains("PRODUCT_ATELIER_KNOWLEDGE_BASE") && error.contains("absolute path"),
        "unexpected error: {error}"
    );
}

#[test]
fn existing_canonical_direct_children_are_selected() {
    let data_root = TestDirectory::new("valid");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();

    assert_eq!(
        selected.data_root,
        std::fs::canonicalize(&data_root.path).unwrap()
    );
    assert_eq!(
        selected.webview_data_dir,
        std::fs::canonicalize(webview).unwrap()
    );
    assert_eq!(
        selected.legacy_config_sentinel,
        selected.data_root.join(DISABLED_LEGACY_CONFIG_NAME)
    );
    assert_eq!(
        selected.knowledge_base_dir,
        selected.data_root.join(DISABLED_KNOWLEDGE_BASE_NAME)
    );
}

#[test]
fn isolation_rejects_external_or_misdirected_knowledge_base() {
    let data_root = TestDirectory::new("knowledge-contract");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);

    let external = TestDirectory::new("knowledge-external");
    let error = select_candidate_isolation(
        Some(OsString::from("1")),
        Some(data_root.path.clone().into_os_string()),
        Some(webview.clone().into_os_string()),
        Some(legacy.clone().into_os_string()),
        Some(knowledge_base(&external.path).into_os_string()),
        &[],
    )
    .expect_err("an external candidate knowledge base must fail closed");
    assert!(
        error.contains("PRODUCT_ATELIER_KNOWLEDGE_BASE") && error.contains("direct"),
        "unexpected error: {error}"
    );

    let wrong_name = data_root.child("knowledge");
    std::fs::create_dir(&wrong_name).unwrap();
    let error = select_candidate_isolation(
        Some(OsString::from("1")),
        Some(data_root.path.clone().into_os_string()),
        Some(webview.into_os_string()),
        Some(legacy.into_os_string()),
        Some(wrong_name.into_os_string()),
        &[],
    )
    .expect_err("a misnamed candidate knowledge base must fail closed");
    assert!(
        error.contains(DISABLED_KNOWLEDGE_BASE_NAME),
        "unexpected error: {error}"
    );
}

#[test]
fn every_official_launcher_temp_prefix_is_accepted() {
    for prefix in [
        "ProductAtelier-launch-and-shoot-",
        "ProductAtelier-packaged-schema-upgrade-",
        "ProductAtelier-app-test-",
    ] {
        let data_root = TestDirectory::with_prefix(prefix, "official-prefix");
        let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
        std::fs::create_dir(&webview).unwrap();
        let legacy = legacy_sentinel(&data_root.path);

        assert!(
            select(Some(&data_root.path), Some(&webview), Some(&legacy), &[],)
                .unwrap()
                .is_some()
        );
    }
}

#[test]
fn strict_isolation_rejects_unofficial_or_nested_temp_roots() {
    let unofficial = TestDirectory::with_prefix("ProductAtelier-unofficial-", "wrong-prefix");
    let unofficial_webview = unofficial.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&unofficial_webview).unwrap();
    let unofficial_legacy = legacy_sentinel(&unofficial.path);
    let error = select(
        Some(&unofficial.path),
        Some(&unofficial_webview),
        Some(&unofficial_legacy),
        &[],
    )
    .unwrap_err();
    assert!(
        error.contains("official randomized"),
        "unexpected error: {error}"
    );

    let holder = TestDirectory::new("nested-holder");
    let nested = holder.child("ProductAtelier-launch-and-shoot-abcdefgh");
    let nested_webview = nested.join(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir_all(&nested_webview).unwrap();
    let nested_legacy = legacy_sentinel(&nested);
    let error = select(
        Some(&nested),
        Some(&nested_webview),
        Some(&nested_legacy),
        &[],
    )
    .unwrap_err();
    assert!(
        error.contains("official randomized"),
        "unexpected error: {error}"
    );
}

#[test]
fn protected_roots_include_default_legacy_and_knowledge_locations() {
    let protected = candidate_protected_roots();
    if let Some(legacy_directory) = default_legacy_config_directory() {
        assert!(protected.contains(&legacy_directory));
    }
    for knowledge_root in default_knowledge_vault_roots() {
        assert!(protected.contains(&knowledge_root));
    }
    #[cfg(windows)]
    if Path::new("D:/").is_dir() {
        assert!(protected.contains(&PathBuf::from("D:/\u{77e5}\u{8bc6}\u{5e93}")));
    }
}

#[test]
fn isolation_requires_existing_regular_directories() {
    let data_root = TestDirectory::new("directory-contract");
    let missing_root = data_root.child("missing-root");
    let missing_webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    let legacy = legacy_sentinel(&data_root.path);

    let missing_root_error = select(
        Some(&missing_root),
        Some(&missing_webview),
        Some(&legacy),
        &[],
    )
    .unwrap_err();
    assert!(missing_root_error.contains("existing directory"));

    std::fs::write(&missing_webview, b"not a directory").unwrap();
    let file_error = select(
        Some(&data_root.path),
        Some(&missing_webview),
        Some(&legacy),
        &[],
    )
    .unwrap_err();
    assert!(file_error.contains("directory"));
}

#[test]
fn isolation_rejects_wrong_name_nested_and_external_webview_directories() {
    let data_root = TestDirectory::new("boundary");
    let legacy = legacy_sentinel(&data_root.path);
    let wrong_name = data_root.child("webview-cache");
    std::fs::create_dir(&wrong_name).unwrap();
    assert!(
        select(Some(&data_root.path), Some(&wrong_name), Some(&legacy), &[])
            .unwrap_err()
            .contains(WEBVIEW_DATA_DIR_NAME)
    );

    let nested = data_root.child("nested").join(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir_all(&nested).unwrap();
    assert!(
        select(Some(&data_root.path), Some(&nested), Some(&legacy), &[])
            .unwrap_err()
            .contains("direct")
    );

    let external_root = TestDirectory::new("external");
    let external = external_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&external).unwrap();
    assert!(
        select(Some(&data_root.path), Some(&external), Some(&legacy), &[])
            .unwrap_err()
            .contains("direct")
    );
}

#[test]
fn isolation_rejects_existing_or_misdirected_legacy_config() {
    let data_root = TestDirectory::new("legacy-contract");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    std::fs::write(&legacy, b"must not be read").unwrap();
    assert!(
        select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
            .unwrap_err()
            .contains("non-existing sentinel")
    );
    std::fs::remove_file(&legacy).unwrap();

    let wrong_name = data_root.child("legacy.json");
    assert!(select(
        Some(&data_root.path),
        Some(&webview),
        Some(&wrong_name),
        &[]
    )
    .unwrap_err()
    .contains(DISABLED_LEGACY_CONFIG_NAME));

    let external = TestDirectory::new("legacy-external");
    let external_legacy = legacy_sentinel(&external.path);
    assert!(select(
        Some(&data_root.path),
        Some(&webview),
        Some(&external_legacy),
        &[],
    )
    .unwrap_err()
    .contains("direct"));
}

#[test]
fn isolation_rejects_protected_root_descendants() {
    let data_root = TestDirectory::new("protected-descendant");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    let protected = std::fs::canonicalize(std::env::temp_dir()).unwrap();

    let error = select(
        Some(&data_root.path),
        Some(&webview),
        Some(&legacy),
        std::slice::from_ref(&protected),
    )
    .unwrap_err();
    assert!(error.contains("protected"));
}

#[test]
fn isolation_rejects_roots_that_contain_protected_data() {
    let broad_root = TestDirectory::new("broad-root");
    let protected = broad_root.child("protected-formal-data");
    std::fs::create_dir(&protected).unwrap();
    let webview = broad_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&broad_root.path);

    let error = select(
        Some(&broad_root.path),
        Some(&webview),
        Some(&legacy),
        std::slice::from_ref(&protected),
    )
    .unwrap_err();
    assert!(error.contains("protected"));
}

#[cfg(windows)]
fn create_junction(link: &Path, target: &Path) {
    let output = std::process::Command::new("cmd")
        .args(["/d", "/c", "mklink", "/J"])
        .arg(link)
        .arg(target)
        .output()
        .expect("Windows cmd must be available for the junction test");
    assert!(
        output.status.success(),
        "could not create test junction: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[cfg(windows)]
struct SeededBusinessTree {
    webview: PathBuf,
    config: PathBuf,
    ledger: PathBuf,
    manifest: PathBuf,
    asset: PathBuf,
    output: PathBuf,
    assets_directory: PathBuf,
}

#[cfg(windows)]
fn seed_completed_business_tree(data_root: &Path) -> SeededBusinessTree {
    let webview = data_root.join(WEBVIEW_DATA_DIR_NAME);
    let assets_directory = data_root.join("assets");
    let output_directory = data_root.join("output");
    let nested_output = output_directory.join("review");
    std::fs::create_dir(&webview).unwrap();
    std::fs::create_dir(&assets_directory).unwrap();
    std::fs::create_dir(&output_directory).unwrap();
    std::fs::create_dir(&nested_output).unwrap();

    let config = data_root.join(CONFIG_FILE_NAME);
    let ledger = data_root.join(LEDGER_FILE_NAME);
    let manifest = data_root.join(FIXTURE_MANIFEST_NAME);
    let asset = assets_directory.join("source.png");
    let output = nested_output.join("result.png");
    std::fs::write(&config, b"{}\n").unwrap();
    std::fs::write(&ledger, b"seeded sqlite fixture").unwrap();
    std::fs::write(&manifest, b"{}\n").unwrap();
    std::fs::write(&asset, b"seeded source asset").unwrap();
    std::fs::write(&output, b"seeded result asset").unwrap();
    SeededBusinessTree {
        webview,
        config,
        ledger,
        manifest,
        asset,
        output,
        assets_directory,
    }
}

#[cfg(windows)]
#[test]
fn startup_rejects_hardlinks_injected_after_seeding() {
    for (index, target_name) in [
        CONFIG_FILE_NAME,
        LEDGER_FILE_NAME,
        FIXTURE_MANIFEST_NAME,
        "assets/source.png",
        "output/review/result.png",
    ]
    .into_iter()
    .enumerate()
    {
        let data_root = TestDirectory::new(&format!("post-seed-hardlink-{index}"));
        let tree = seed_completed_business_tree(&data_root.path);
        let legacy = legacy_sentinel(&data_root.path);
        let selected = select(
            Some(&data_root.path),
            Some(&tree.webview),
            Some(&legacy),
            &[],
        )
        .unwrap()
        .unwrap();

        let target = data_root.path.join(target_name);
        let attacker_source = data_root.child(&format!("attacker-source-{index}"));
        std::fs::write(&attacker_source, b"attacker-controlled bytes").unwrap();
        std::fs::remove_file(&target).unwrap();
        std::fs::hard_link(&attacker_source, &target).unwrap();

        let error = acquire_candidate_isolation_guards(&selected)
            .expect_err("a post-seeder hardlink must fail closed before startup");
        assert!(error.contains("single-link"), "unexpected error: {error}");
    }
}

#[cfg(windows)]
#[test]
fn startup_guards_pin_the_seeded_business_tree_without_blocking_writes() {
    let data_root = TestDirectory::new("seeded-business-pins");
    let tree = seed_completed_business_tree(&data_root.path);
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(
        Some(&data_root.path),
        Some(&tree.webview),
        Some(&legacy),
        &[],
    )
    .unwrap()
    .unwrap();
    let guards = acquire_candidate_isolation_guards(&selected).unwrap();

    for path in [
        &tree.config,
        &tree.ledger,
        &tree.manifest,
        &tree.asset,
        &tree.output,
    ] {
        let moved = path.with_extension("moved");
        std::fs::rename(path, &moved)
            .expect_err("a pinned startup business file must not be replaceable");
    }
    let moved_assets = data_root.child("assets-moved");
    std::fs::rename(&tree.assets_directory, &moved_assets)
        .expect_err("a pinned business directory must not be replaceable");

    std::fs::write(
        &tree.ledger,
        b"sqlite-style in-place write remains available",
    )
    .unwrap();
    let runtime_output = data_root.child("output").join("runtime-created.png");
    std::fs::write(&runtime_output, b"runtime output").unwrap();

    guards.release_startup_replaceable_files();
    let moved_config = data_root.child("config-after-startup.json");
    std::fs::rename(&tree.config, &moved_config)
        .expect("config replacement must be restored after startup reads complete");
    std::fs::rename(&moved_config, &tree.config).unwrap();
    std::fs::rename(&tree.ledger, tree.ledger.with_extension("moved"))
        .expect_err("non-replaceable seeded business files stay pinned for the process lifetime");
}

#[cfg(windows)]
#[test]
fn business_tree_guards_support_restart_reacquisition() {
    let data_root = TestDirectory::new("business-restart");
    let tree = seed_completed_business_tree(&data_root.path);
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(
        Some(&data_root.path),
        Some(&tree.webview),
        Some(&legacy),
        &[],
    )
    .unwrap()
    .unwrap();

    let first = acquire_candidate_isolation_guards(&selected).unwrap();
    drop(first);
    let restart_output = data_root.child("output").join("restart-created.png");
    std::fs::write(&restart_output, b"created between launches").unwrap();

    let second = acquire_candidate_isolation_guards(&selected)
        .expect("an unchanged business tree must be reacquirable after restart");
    let moved = restart_output.with_extension("moved");
    std::fs::rename(&restart_output, &moved)
        .expect_err("files present at restart must be pinned by the new process");
    drop(second);
    std::fs::rename(&restart_output, &moved).unwrap();
    std::fs::rename(&moved, &restart_output).unwrap();
}

#[cfg(windows)]
#[test]
fn missing_business_files_remain_runtime_creatable_inside_the_random_root() {
    let data_root = TestDirectory::new("missing-business-files");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();
    let guards = acquire_candidate_isolation_guards(&selected).unwrap();

    let config = data_root.child(CONFIG_FILE_NAME);
    let ledger = data_root.child(LEDGER_FILE_NAME);
    std::fs::write(&config, b"{}\n").unwrap();
    std::fs::write(&ledger, b"new sqlite database").unwrap();
    let moved_config = data_root.child("runtime-config.json");
    std::fs::rename(&config, &moved_config).unwrap();
    std::fs::rename(&moved_config, &config).unwrap();
    drop(guards);
}

#[test]
fn server_wait_releases_startup_pins_before_the_health_request() {
    use std::io::{Read, Write};

    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let released = Arc::new(AtomicBool::new(false));
    let server_released = Arc::clone(&released);
    let server = std::thread::spawn(move || {
        let (probe, _) = listener.accept().unwrap();
        drop(probe);
        let (mut health, _) = listener.accept().unwrap();
        let mut request = [0_u8; 1024];
        let count = health.read(&mut request).unwrap();
        assert!(String::from_utf8_lossy(&request[..count]).contains("GET /api/health"));
        assert!(
            server_released.load(Ordering::SeqCst),
            "startup pins must be released before health can atomically update config"
        );
        health
            .write_all(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok")
            .unwrap();
    });

    let callback_released = Arc::clone(&released);
    assert!(wait_for_server(port, 2, move || {
        callback_released.store(true, Ordering::SeqCst);
    }));
    server.join().unwrap();
    assert!(released.load(Ordering::SeqCst));
}

#[cfg(windows)]
#[test]
fn isolation_rejects_a_data_root_junction_even_when_it_resolves_safely() {
    let holder = TestDirectory::new("root-junction-holder");
    let target = TestDirectory::new("root-junction-target");
    let webview_target = target.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview_target).unwrap();
    let link = holder.child("candidate-data-link");
    create_junction(&link, &target.path);
    let webview_link = link.join(WEBVIEW_DATA_DIR_NAME);
    let legacy_link = legacy_sentinel(&link);

    let error = select(Some(&link), Some(&webview_link), Some(&legacy_link), &[])
        .expect_err("a root junction must fail closed before canonicalization");
    assert!(error.contains("non-reparse"), "unexpected error: {error}");
    std::fs::remove_dir(&link).unwrap();
}

#[cfg(windows)]
#[test]
fn isolation_rejects_a_webview_junction_escape() {
    let data_root = TestDirectory::new("webview-junction-root");
    let external_root = TestDirectory::new("webview-junction-target");
    let external = external_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&external).unwrap();
    let link = data_root.child(WEBVIEW_DATA_DIR_NAME);
    create_junction(&link, &external);
    let legacy = legacy_sentinel(&data_root.path);

    let error = select(Some(&data_root.path), Some(&link), Some(&legacy), &[])
        .expect_err("a WebView junction must fail closed");
    assert!(error.contains("non-reparse"), "unexpected error: {error}");
    std::fs::remove_dir(&link).unwrap();
}

#[cfg(windows)]
#[test]
fn isolation_guards_block_runtime_root_replacement() {
    let data_root = TestDirectory::new("runtime-lock");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();
    let guards = acquire_candidate_isolation_guards(&selected).unwrap();
    let moved = data_root.path.with_extension("moved");
    let moved_webview = data_root.child("webview-moved");
    let probe = webview.join("write-probe");
    let renamed_probe = webview.join("renamed-probe");

    std::fs::write(&probe, b"candidate child writes remain available").unwrap();
    std::fs::rename(&probe, &renamed_probe).unwrap();
    std::fs::remove_file(&renamed_probe).unwrap();

    let sentinel = webview.join(WEBVIEW_ISOLATION_GUARD_NAME);
    assert!(sentinel.is_file());
    std::fs::remove_file(&sentinel).expect_err("the held WebView sentinel must not be removable");

    std::fs::rename(&webview, &moved_webview)
        .expect_err("a locked candidate WebView directory must not be replaceable");
    assert!(webview.is_dir());

    std::fs::rename(&data_root.path, &moved)
        .expect_err("a locked candidate data root must not be replaceable");
    assert!(data_root.path.is_dir());

    drop(guards);
    std::fs::remove_file(&sentinel).unwrap();
    std::fs::rename(&data_root.path, &moved).unwrap();
    std::fs::rename(&moved, &data_root.path).unwrap();
}

#[cfg(windows)]
#[test]
fn isolation_guards_reuse_the_same_sentinel_across_restarts() {
    let data_root = TestDirectory::new("restartable-guard");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();
    let sentinel = webview.join(WEBVIEW_ISOLATION_GUARD_NAME);

    let first = acquire_candidate_isolation_guards(&selected).unwrap();
    let first_file = File::open(&sentinel).unwrap();
    let first_information =
        windows_file_information(&first_file, "first restart sentinel").unwrap();
    let first_identity = (
        first_information.volume_serial,
        first_information.file_index,
    );
    assert_eq!(first_information.number_of_links, 1);
    assert_eq!(std::fs::read(&sentinel).unwrap(), b"");
    drop(first);

    let second = acquire_candidate_isolation_guards(&selected)
        .expect("an unchanged isolation directory must be restartable");
    let second_file = File::open(&sentinel).unwrap();
    let second_information =
        windows_file_information(&second_file, "second restart sentinel").unwrap();
    assert_eq!(
        (
            second_information.volume_serial,
            second_information.file_index,
        ),
        first_identity
    );
    assert_eq!(second_information.number_of_links, 1);
    assert_eq!(std::fs::read(&sentinel).unwrap(), b"");
    std::fs::remove_file(&sentinel)
        .expect_err("the sentinel must remain undeletable during the second lifetime");

    drop(second);
    std::fs::remove_file(&sentinel).unwrap();
}

#[cfg(windows)]
#[test]
fn isolation_guard_never_truncates_an_existing_sentinel() {
    let data_root = TestDirectory::new("sentinel-no-truncate");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let sentinel = webview.join(WEBVIEW_ISOLATION_GUARD_NAME);
    let original = b"untrusted existing sentinel bytes";
    std::fs::write(&sentinel, original).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();

    let error = acquire_candidate_isolation_guards(&selected)
        .expect_err("a non-empty existing sentinel must fail closed");
    assert!(error.contains("empty regular"), "unexpected error: {error}");
    assert_eq!(std::fs::read(&sentinel).unwrap(), original);
}

#[cfg(windows)]
#[test]
fn isolation_guard_rejects_a_hardlinked_existing_sentinel() {
    let data_root = TestDirectory::new("sentinel-hardlink");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let external = data_root.child("external-empty-file");
    std::fs::write(&external, b"").unwrap();
    let sentinel = webview.join(WEBVIEW_ISOLATION_GUARD_NAME);
    std::fs::hard_link(&external, &sentinel).unwrap();
    let hardlinked = File::open(&sentinel).unwrap();
    assert_eq!(
        windows_file_information(&hardlinked, "hardlinked sentinel")
            .unwrap()
            .number_of_links,
        2
    );
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();

    let error = acquire_candidate_isolation_guards(&selected)
        .expect_err("a hardlinked sentinel must fail closed");
    assert!(error.contains("single-link"), "unexpected error: {error}");
    assert_eq!(std::fs::read(&external).unwrap(), b"");
}

#[cfg(windows)]
#[test]
fn isolation_guard_rejects_a_root_changed_to_a_junction_after_selection() {
    let data_root = TestDirectory::new("post-selection-swap");
    let webview = data_root.child(WEBVIEW_DATA_DIR_NAME);
    std::fs::create_dir(&webview).unwrap();
    let legacy = legacy_sentinel(&data_root.path);
    let selected = select(Some(&data_root.path), Some(&webview), Some(&legacy), &[])
        .unwrap()
        .unwrap();
    let moved = data_root.path.with_extension("original");
    let junction_target = TestDirectory::new("post-selection-target");

    std::fs::rename(&data_root.path, &moved).unwrap();
    create_junction(&data_root.path, &junction_target.path);
    let error = acquire_candidate_isolation_guards(&selected)
        .expect_err("a post-selection junction swap must fail closed");
    assert!(error.contains("non-reparse"), "unexpected error: {error}");

    std::fs::remove_dir(&data_root.path).unwrap();
    std::fs::rename(&moved, &data_root.path).unwrap();
}

#[test]
fn isolation_disables_only_the_configured_main_window() {
    let mut main = WindowConfig::default();
    main.label = "main".to_string();
    main.title = "Product Atelier candidate".to_string();
    let mut secondary = WindowConfig::default();
    secondary.label = "secondary".to_string();
    let mut windows = vec![main, secondary];

    let isolated = take_isolated_window_config(&mut windows, "main").unwrap();

    assert!(isolated.create);
    assert_eq!(isolated.label, "main");
    assert_eq!(isolated.title, "Product Atelier candidate");
    assert!(!windows[0].create);
    assert!(windows[1].create);
}

#[test]
fn isolation_requires_an_auto_created_main_window() {
    let mut window = WindowConfig::default();
    window.label = "main".to_string();
    window.create = false;

    let error = take_isolated_window_config(std::slice::from_mut(&mut window), "main")
        .expect_err("disabled main window must not silently fall back");

    assert!(error.contains("auto-created"));
}
