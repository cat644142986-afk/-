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
  const candidateSmoke = script.indexOf('-PortableDir $CandidateDir');
  const begin = script.indexOf('$PromotionTool begin');
  const formalSmoke = script.indexOf('-PortableDir $PortableDir', candidateSmoke + 1);
  const rollback = script.indexOf('$PromotionTool rollback');
  const finalize = script.indexOf('$PromotionTool finalize');
  const shortcut = script.indexOf('CreateShortcut($temporaryShortcut)');

  assert.ok(stage >= 0, 'candidate staging must be present');
  assert.ok(candidateSmoke > stage, 'candidate smoke must follow candidate staging');
  assert.ok(begin > candidateSmoke, 'formal promotion must follow candidate smoke');
  assert.ok(formalSmoke > begin, 'formal smoke must follow promotion');
  assert.ok(rollback > begin, 'promotion failures must have a rollback path');
  assert.ok(finalize > formalSmoke, 'finalization must follow formal smoke');
  assert.ok(shortcut > finalize, 'desktop entry must be published after finalization');
  assert.match(script, /transaction-id \$promotionTransactionId/);
  assert.match(script, /File\]::Replace\(\$temporaryShortcut, \$desktopShortcut, \$shortcutBackup, \$true\)/);
  assert.doesNotMatch(script, /File\]::Replace\(\$temporaryShortcut, \$desktopShortcut, \$null\)/);
  assert.match(script, /previous desktop shortcut was restored/i);
  assert.match(script, /\$keepShortcutBackup = \$true/);
  assert.match(script, /published desktop shortcut does not target the finalized formal directory/i);
  assert.match(script, /-Quick and -SkipSidecar are not allowed/);
  assert.doesNotMatch(script, /Build-Sidecar\.ps1"?\s+-DeployPortable/i);
});

test('sidecar build cannot overwrite the formal portable release', () => {
  const script = read('tools/Build-Sidecar.ps1');
  const spec = read('python-server.spec');

  assert.doesNotMatch(script, /DeployPortable/);
  assert.doesNotMatch(script, /release\\ProductAtelier-Portable/);
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
  assert.match(script, /"python\/semantic_cutout\.py"/);
  assert.match(script, /"python\/semantic_grounding\.py"/);
  assert.match(script, /"python\/semantic_query\.py"/);
  assert.match(script, /"python\/semantic_query_lexicon\.json"/);
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
  const python = read('build-python.bat');
  const buildRequirements = read('python/requirements-build.txt');

  assert.match(portable, /tools\\dev\.ps1/);
  assert.match(installer, /tools\\dev\.ps1/);
  assert.match(installer, /tauri build --features custom-protocol/);
  assert.doesNotMatch(installer, /(^|\s)pyinstaller\s/im);
  assert.match(python, /tools\\Build-Sidecar\.ps1/);
  assert.doesNotMatch(python, /(^|\s)pyinstaller\s/im);
  assert.match(buildRequirements, /^pyinstaller==6\.22\.2$/m);
  assert.match(buildRequirements, /^pyinstaller-hooks-contrib==2026\.7$/m);
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
  assert.match(locker, /supported_model_artifact_ids/);
  assert.match(locker, /source_fingerprint/);
  assert.match(locker, /runtime pack cannot contain links/);
  assert.match(runtimeSpec, /collect_submodules\(package\)/);
  assert.doesNotMatch(runtimeSpec, /collect_all\(/);
  assert.match(runtimeSpec, /'transformers\.models\.grounding_dino'/);
  assert.match(runtimeSpec, /'torch\.utils\.tensorboard'/);
  assert.match(sidecarSpec, /'grounding_runtime_worker'/);
  assert.match(sidecarSpec, /model-artifacts/);
});
