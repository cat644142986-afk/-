use super::*;

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn create(label: &str) -> Self {
        let sequence = TEMP_FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "product-atelier-config-{label}-{}-{sequence}",
            std::process::id()
        ));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn explicit_legacy_override_never_falls_back_to_the_user_home() {
    let home = PathBuf::from(r"C:\Users\real-user");
    let sentinel = PathBuf::from(r"D:\candidate-data\no-legacy-config.json");

    assert_eq!(
        resolve_legacy_config_path(Some(sentinel.clone().into_os_string()), Some(home.clone()),),
        Some(sentinel)
    );
    assert_eq!(
        resolve_legacy_config_path(Some(OsString::new()), Some(home)),
        None
    );
}

#[test]
fn legacy_fallback_is_used_only_without_an_explicit_override() {
    let home = PathBuf::from(r"C:\Users\real-user");

    assert_eq!(
        resolve_legacy_config_path(None, Some(home.clone())),
        Some(home.join(r".codex\skills\lk-ai-image\config.json"))
    );
    assert_eq!(resolve_legacy_config_path(None, None), None);
}

#[test]
fn isolated_missing_legacy_path_produces_an_empty_default_config() {
    let directory = TestDirectory::create("missing-legacy");
    let config = load_config_for_runtime(
        &directory.0.join("config.json"),
        Some(&directory.0.join("no-legacy-config.json")),
        true,
    );

    assert!(config.api_key.is_empty());
    assert_eq!(config.default_model, "gpt-image-2");
}

#[test]
fn isolated_primary_config_wins_over_an_explicit_legacy_file() {
    let directory = TestDirectory::create("primary-wins");
    let primary = directory.0.join("config.json");
    let legacy = directory.0.join("legacy.json");
    std::fs::write(
        &primary,
        r#"{"api_key":"candidate-key","default_model":"candidate-model"}"#,
    )
    .unwrap();
    std::fs::write(&legacy, r#"{"api_key":"real-user-key"}"#).unwrap();

    let config = load_config_for_runtime(&primary, Some(&legacy), true);

    assert_eq!(config.api_key, "candidate-key");
    assert_eq!(config.default_model, "candidate-model");
}

#[test]
fn isolated_runtime_never_reads_an_existing_legacy_api_key() {
    let directory = TestDirectory::create("legacy-disabled");
    let primary = directory.0.join("config.json");
    let legacy = directory.0.join("real-user-legacy.json");
    std::fs::write(&legacy, r#"{"api_key":"must-not-be-read"}"#).unwrap();

    let config = load_config_for_runtime(&primary, Some(&legacy), true);

    assert!(config.api_key.is_empty());
    assert_eq!(config.default_model, "gpt-image-2");
}

#[test]
fn formal_runtime_preserves_the_explicit_legacy_migration_path() {
    let directory = TestDirectory::create("formal-legacy");
    let primary = directory.0.join("config.json");
    let legacy = directory.0.join("legacy.json");
    std::fs::write(&legacy, r#"{"api_key":"formal-migration-key"}"#).unwrap();

    let config = load_config_for_runtime(&primary, Some(&legacy), false);

    assert_eq!(config.api_key, "formal-migration-key");
}
