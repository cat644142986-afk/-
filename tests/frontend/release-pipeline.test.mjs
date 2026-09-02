import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), 'utf8');

test('formal portable promotion is candidate-first and rollback-capable', () => {
  const script = read('tools/dev.ps1');
  const stage = script.indexOf('$PromotionTool stage');
  const reviewedIdentity = script.indexOf('$reviewedCandidateIdentitySha256 =');
  const candidateSmoke = script.indexOf('-PortableDir $CandidateDir');
  const begin = script.indexOf('$PromotionTool begin');
  const boundIdentity = script.indexOf(
    '--candidate-identity-sha256 $reviewedCandidateIdentitySha256',
  );
  const formalSmoke = script.indexOf('-PortableDir $PortableDir', candidateSmoke + 1);
  const rollback = script.indexOf('$PromotionTool rollback');
  const finalize = script.indexOf('$PromotionTool finalize');
  const shortcut = script.indexOf('CreateShortcut($temporaryShortcut)');

  assert.ok(stage >= 0, 'candidate staging must be present');
  assert.ok(reviewedIdentity > stage, 'candidate stage output must publish the reviewed identity');
  assert.ok(candidateSmoke > reviewedIdentity, 'candidate smoke must use the captured identity');
  assert.ok(candidateSmoke > stage, 'candidate smoke must follow candidate staging');
  assert.ok(begin > candidateSmoke, 'formal promotion must follow candidate smoke');
  assert.ok(boundIdentity > begin, 'promotion must require the exact reviewed candidate identity');
  assert.ok(formalSmoke > begin, 'formal smoke must follow promotion');
  assert.ok(rollback > begin, 'promotion failures must have a rollback path');
  assert.ok(finalize > formalSmoke, 'finalization must follow formal smoke');
  assert.ok(shortcut > finalize, 'desktop entry must be published after finalization');
  assert.match(script, /transaction-id \$promotionTransactionId/);
  assert.match(script, /identity_receipt\.sha256/);
  assert.match(script, /File\]::Replace\(\$temporaryShortcut, \$desktopShortcut, \$shortcutBackup, \$true\)/);
  assert.doesNotMatch(script, /File\]::Replace\(\$temporaryShortcut, \$desktopShortcut, \$null\)/);
  assert.match(script, /previous desktop shortcut was restored/i);
  assert.match(script, /\$keepShortcutBackup = \$true/);
  assert.match(script, /published desktop shortcut does not target the finalized formal directory/i);
  assert.match(script, /-Quick and -SkipSidecar are not allowed/);
  assert.match(script, /Start-Process \$TargetExe -WorkingDirectory \$PortableDir -PassThru/);
  assert.match(script, /screenshotScript --pid \$finalAppProcess\.Id/);
  assert.doesNotMatch(script, /Build-Sidecar\.ps1"?\s+-DeployPortable/i);
});

test('release evidence captures the finalized app by process identity', () => {
  const screenshot = read('tools/screenshot.py');

  assert.match(screenshot, /def find_window_by_pid\(process_id\):/);
  assert.match(screenshot, /GetWindowThreadProcessId/);
  assert.match(screenshot, /owner_pid\.value != process_id/);
  assert.match(screenshot, /parser\.add_argument\("--pid"/);
  assert.match(screenshot, /ShowWindow\(hwnd, SW_RESTORE\)/);
  assert.match(screenshot, /SetForegroundWindow\(hwnd\)/);
});

test('sidecar build cannot overwrite the formal portable release', () => {
  const script = read('tools/Build-Sidecar.ps1');
  const spec = read('python-server.spec');

  assert.doesNotMatch(script, /DeployPortable/);
  assert.doesNotMatch(script, /release\\ProductAtelier-Portable/);
  assert.match(script, /"python\/command_registry\.py"/);
  assert.match(script, /"python\/canvas_export\.py"/);
  assert.match(script, /"python\/local_edit_contract\.py"/);
  assert.match(script, /"python\/spatial_canvas_contract\.py"/);
  assert.match(script, /"python\/video_contract\.py"/);
  assert.match(script, /videoFixtureRoot/);
  assert.match(script, /Get-ChildItem -LiteralPath \$videoFixtureRoot -File -Recurse/);
  assert.match(script, /Sort-Object FullName/);
  assert.match(script, /\.replacement-\$token/);
  assert.match(script, /\.previous-\$token/);
  assert.match(script, /sidecar-build\.lock/);
  assert.match(script, /\[System\.IO\.FileShare\]::None/);
  assert.match(script, /Assert-NoProjectReparsePoints/);
  assert.match(script, /FileAttributes\]::ReparsePoint/);
  assert.match(script, /sidecarBuildLockOwned/);
  assert.match(script, /rev-parse --verify HEAD/);
  assert.match(spec, /'torch'/);
  assert.match(spec, /'transformers'/);
  assert.match(spec, /'tokenizers'/);
  assert.doesNotMatch(spec, /collect_data_files\('transformers'\)/);
  assert.match(spec, /semantic_query_lexicon\.json'\), '\.'/);
  assert.match(spec, /video_fixtures', 'offline-preview-v1'/);
  assert.match(script, /"python\/semantic_cutout\.py"/);
  assert.match(script, /"python\/semantic_grounding\.py"/);
  assert.match(script, /"python\/semantic_query\.py"/);
  assert.match(script, /"python\/semantic_query_lexicon\.json"/);
});

test('packaged schema upgrade gate preserves offline video evidence across restart', () => {
  const gate = read('tools/verify_packaged_schema_upgrade.py');

  assert.match(gate, /"python\/video_contract\.py": "image-to-video contract"/);
  assert.match(gate, /manifest_tracks_video_contract/);
  assert.match(gate, /\/api\/progress\/\{job_id\}/);
  assert.match(gate, /\/api\/jobs\/\{job_id\}\/traces/);
  assert.match(gate, /\/content\?download=true/);
  assert.match(gate, /def _validate_packaged_video_restart\(/);
  assert.match(gate, /"video_metrics": \{/);
  assert.match(gate, /"idempotent_replay": replayed_video\.get\("created"\) is False/);
});

test('production frontend carries pinned third-party license notices', () => {
  const notices = read('src/public/THIRD_PARTY_NOTICES.txt');
  assert.match(notices, /Excalidraw 0\.18\.1/);
  assert.match(notices, /github\.com\/excalidraw\/excalidraw\/tree\/v0\.18\.1/);
  assert.match(notices, /Copyright \(c\) 2020 Excalidraw/);
  assert.match(notices, /React 18\.3\.1 and React DOM 18\.3\.1/);
  assert.match(notices, /Copyright \(c\) Meta Platforms, Inc\. and affiliates\./);
  assert.match(notices, /Fabric\.js 7\.4\.0/);
  assert.match(notices, /Copyright \(c\) 2016-present Andrea Bogazzi/);
  assert.match(notices, /Lucide 1\.31\.0/);
  assert.match(notices, /Copyright \(c\) 2013-present Cole Bemis/);
});

test('portable smoke binds runtime identity to the candidate artifacts', () => {
  const sidecarSmoke = read('tools/Test-Portable.ps1');
  const appSmoke = read('tools/Test-Portable-App.ps1');

  assert.match(sidecarSmoke, /manifest\.git_commit/);
  assert.match(sidecarSmoke, /source_fingerprint does not match source_hashes/);
  assert.match(sidecarSmoke, /health\.service\.git_commit/);
  assert.match(appSmoke, /ParentProcessId/);
  assert.match(appSmoke, /ExecutablePath/);
  assert.match(appSmoke, /Test-ExpectedSidecarProcess/);
  assert.match(appSmoke, /health\.service\.git_commit/);
  assert.doesNotMatch(appSmoke, /beforeSidecars/);
});

test('all Windows entry points use the manifest-producing release chain', () => {
  const portable = read('build-portable.bat');
  const installer = read('build-installer.bat');
  const signedInstaller = read('build-signed-installer.bat');
  const python = read('build-python.bat');
  const buildRequirements = read('python/requirements-build.txt');
  const testRequirements = read('python/requirements-test.txt');

  assert.match(portable, /tools\\dev\.ps1/);
  assert.match(installer, /tools\\dev\.ps1/);
  assert.match(installer, /tauri build --features custom-protocol/);
  assert.doesNotMatch(installer, /(^|\s)pyinstaller\s/im);
  assert.match(signedInstaller, /tools\\Build-SignedInstaller\.ps1/);
  assert.match(signedInstaller, /No public installer was published/);
  assert.match(python, /tools\\Build-Sidecar\.ps1/);
  assert.doesNotMatch(python, /(^|\s)pyinstaller\s/im);
  assert.match(buildRequirements, /^pyinstaller==6\.22\.2$/m);
  assert.match(buildRequirements, /^pyinstaller-hooks-contrib==2026\.7$/m);
  assert.match(testRequirements, /^psutil==7\.2\.2$/m);
});

test('signed Windows release is fail-closed and candidate-first', () => {
  const signer = read('tools/Windows-CodeSigning.ps1');
  const sidecar = read('tools/Build-Sidecar.ps1');
  const portable = read('tools/dev.ps1');
  const portableGate = read('tools/Test-Portable.ps1');
  const builder = read('tools/Build-SignedInstaller.ps1');
  const installerGate = read('tools/Test-SignedInstaller.ps1');

  assert.match(signer, /PRODUCT_ATELIER_SIGN_CERT_SHA1/);
  assert.match(signer, /PRODUCT_ATELIER_SIGN_TIMESTAMP_URL/);
  assert.match(signer, /The timestamp service must use HTTPS/);
  assert.match(signer, /1\.3\.6\.1\.5\.5\.7\.3\.3/);
  assert.match(signer, /HasPrivateKey/);
  assert.match(signer, /TimeStamperCertificate/);
  assert.match(signer, /signtool\.exe is unavailable/);
  assert.match(signer, /"verify", "\/pa", "\/all", "\/v"/);
  assert.match(signer, /signCommand/);
  assert.match(signer, /"%1"/);
  assert.doesNotMatch(signer, /param\([\s\S]*Pfx/i);
  assert.doesNotMatch(signer, /CertificatePassword|PrivateKeyPath/i);

  const sidecarSign = sidecar.indexOf('-Mode Sign -ArtifactPath $stagedExecutable');
  const sidecarHash = sidecar.indexOf('executable_sha256 =');
  assert.ok(sidecarSign >= 0, 'signed sidecar path must be explicit');
  assert.ok(sidecarHash > sidecarSign, 'sidecar manifest must hash the signed executable');
  assert.match(sidecar, /manifest\["authenticode"\]/);

  const appSign = portable.indexOf('-Mode Sign -ArtifactPath $SourceExe');
  const candidateStage = portable.indexOf('$PromotionTool stage');
  assert.ok(appSign >= 0, 'portable app must be signed explicitly');
  assert.ok(candidateStage > appSign, 'candidate assembly must follow app signing');
  assert.match(portable, /Build-Sidecar\.ps1" -SignArtifacts/);
  assert.match(portable, /Windows-CodeSigning\.ps1/);
  assert.match(portableGate, /manifest\.authenticode/);
  assert.match(portableGate, /-ArtifactPath @\(\$AppExe, \$SidecarExe\)/);

  const installGate = builder.indexOf('& $InstallerGate');
  const publication = builder.indexOf('Move-Item -LiteralPath $temporary -Destination $destination');
  assert.ok(installGate >= 0, 'signed installer must have an installed-state gate');
  assert.ok(publication > installGate, 'public artifact publication must follow installed-state validation');
  assert.match(builder, /tauri build[\s\S]*--config \$TauriSigningConfig/);
  assert.match(builder, /Refusing to overwrite an existing signed installer/);

  assert.match(installerGate, /A registered Product Atelier installation already exists/);
  assert.match(installerGate, /Test-Portable\.ps1/);
  assert.match(installerGate, /Test-Portable-App\.ps1/);
  assert.match(installerGate, /verify_packaged_schema_upgrade\.py/);
  assert.match(installerGate, /NSIS uninstall did not remove the isolated install directory/);
  assert.match(installerGate, /Restore-Shortcut/);
  assert.match(installerGate, /\[ValidateSet\("Signed", "UnsignedInternal"\)\]/);
  assert.match(installerGate, /\[string\]\$TrustMode = "Signed"/);
  assert.match(installerGate, /Assert-UnsignedArtifacts/);
  assert.match(installerGate, /UnsignedInternal requires Authenticode NotSigned/);
  assert.match(installerGate, /@\("\/S", "\/NS", "\/D=\$installDirectory"\)/);
  assert.match(installerGate, /Get-ShortcutFingerprintViolation/);
  assert.match(installerGate, /if \(-not \$violation\) \{ return \$false \}/);
  assert.match(installerGate, /post-install changed protected Start Menu content/);
  assert.match(installerGate, /@\("\/S", "\/UPDATE"\)/);
  assert.match(installerGate, /Get-RegistryValueState/);
  assert.match(installerGate, /Restore-RegistryValueState/);
  assert.match(installerGate, /Restore-RegistryValueIfOwned/);
  assert.match(installerGate, /protected registry value changed by another actor/);
  assert.match(installerGate, /\$identifierParts\[1\]/);
  assert.match(installerGate, /Software\\\$NsisManufacturer/);
  assert.match(installerGate, /registry-state\.clixml/);
  assert.match(installerGate, /Export-Clixml/);
  assert.match(installerGate, /Installer Language/);
  assert.match(installerGate, /\$cleanupErrors\.Count -eq 0/);
  assert.match(installerGate, /Shortcut safety backup retained at/);
  assert.match(installerGate, /SkipAppSmoke is only allowed for an explicit UnsignedInternal headless preflight/);

  const shortcutBackup = installerGate.indexOf('$shortcutStates += Backup-Shortcut');
  const installLaunch = installerGate.indexOf('$installProcess = Start-Process');
  const postUninstallFingerprint = installerGate.indexOf('"post-uninstall"');
  const shortcutRestore = installerGate.lastIndexOf('Restore-Shortcut `');
  const registryRestore = installerGate.lastIndexOf('Restore-RegistryValueState $registryState');
  const postRecoveryFingerprint = installerGate.indexOf('"post-recovery"');
  const finalReport = installerGate.indexOf('$failures =');
  assert.ok(shortcutBackup >= 0 && shortcutBackup < installLaunch, 'shortcut backup must precede install');
  assert.ok(postUninstallFingerprint > installLaunch, 'post-uninstall fingerprints must follow install');
  assert.ok(shortcutRestore > postUninstallFingerprint, 'shortcut recovery must follow violation capture');
  assert.ok(registryRestore > postUninstallFingerprint, 'registry recovery must follow uninstall');
  assert.ok(postRecoveryFingerprint > shortcutRestore, 'post-recovery verification must follow restore');
  assert.ok(finalReport > postRecoveryFingerprint, 'final reporting must follow recovery verification');
});

test('Windows build-tool verification uses a file entry point instead of fragile python -c quoting', () => {
  const release = read('tools/dev.ps1');
  const verifier = read('tools/verify_build_requirements.py');

  assert.match(release, /verify_build_requirements\.py/);
  assert.doesNotMatch(release, /python\.exe\s+-c\s+\$versionCheck/);
  assert.match(verifier, /"pyinstaller": "6\.22\.2"/);
  assert.match(verifier, /"pyinstaller-hooks-contrib": "2026\.7"/);
  assert.match(verifier, /raise SystemExit\(main\(\)\)/);
});

test('GitHub identity checks tolerate transient TLS failures without weakening release identity', () => {
  const release = read('tools/dev.ps1');

  assert.match(release, /for \(\$attempt = 1; \$attempt -le 3; \$attempt\+\+\)/);
  assert.match(release, /fetch attempt \$attempt failed; retrying/);
  assert.match(release, /Could not fetch the GitHub origin after 3 attempts/);
  assert.match(release, /\$ErrorActionPreference = "Continue"/);
  assert.match(release, /\$ErrorActionPreference = \$previousErrorActionPreference/);
  assert.match(release, /Local HEAD does not match the GitHub upstream/);
  assert.match(release, /Formal release requires a clean worktree/);
});

test('optional grounding runtime is independently locked and never bundled into the slim sidecar', () => {
  const build = read('tools/Build-GroundingRuntime.ps1');
  const locker = read('tools/build_grounding_runtime_manifest.py');
  const runtimeSpec = read('grounding-runtime.spec');
  const sidecarSpec = read('python-server.spec');

  assert.match(build, /verify_build_requirements\.py/);
  assert.match(build, /build_grounding_runtime_manifest\.py/);
  assert.match(build, /output already exists/);
  assert.ok(
    build.indexOf('--probe') < build.indexOf('Move-Item -LiteralPath $Candidate -Destination $OutputRoot'),
    'the runtime must pass its optional model probe before the candidate path is published',
  );
  assert.match(locker, /supported_model_artifact_ids/);
  assert.match(locker, /source_fingerprint/);
  assert.match(locker, /runtime pack cannot contain links/);
  assert.match(runtimeSpec, /collect_submodules\(package\)/);
  assert.doesNotMatch(runtimeSpec, /collect_all\(/);
  assert.match(runtimeSpec, /'transformers\.models\.grounding_dino'/);
  assert.match(runtimeSpec, /'httpx'/);
  assert.doesNotMatch(runtimeSpec, /'aiohttp', 'httpx'/);
  assert.match(runtimeSpec, /'torch\.utils\.tensorboard'/);
  assert.match(sidecarSpec, /'grounding_runtime_worker'/);
  assert.match(sidecarSpec, /model-artifacts/);
});
