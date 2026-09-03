import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const scriptPath = path.join(root, 'tools', 'build_ic6_candidate.ps1');
const scriptBytes = fs.readFileSync(scriptPath);
const script = scriptBytes.toString('utf8').replace(/^\uFEFF/, '');

test('IC6 candidate entry is commit-bound and encoded for Windows PowerShell', () => {
  assert.deepEqual(
    [...scriptBytes.subarray(0, 3)],
    [0xef, 0xbb, 0xbf],
    'PowerShell entry point must use UTF-8 with BOM',
  );
  assert.match(script, /\[Parameter\(Mandatory = \$true\)\]/);
  assert.match(script, /\[ValidatePattern\("\^\[0-9a-fA-F\]\{40\}\$"\)\]/);
  assert.match(script, /\$RequiredBranch = "codex\/excalidraw-infinite-canvas"/);
  assert.match(script, /\$RequiredUpstream = "origin\/\$RequiredBranch"/);
  assert.match(script, /status", "--porcelain=v1", "--untracked-files=all"/);
  assert.match(script, /"rev-parse", "--verify", "HEAD"/);
  assert.match(script, /"rev-parse", "--verify", \$RequiredUpstream/);
  assert.match(script, /fetch --no-tags origin/);
  assert.match(
    script,
    /@\(& git\.exe -C \$Repository update-index --really-refresh 2>&1\)/,
  );
  assert.match(script, /\$exitCode -ne 0 -and \$exitCode -ne 1/);
  assert.match(script, /-Arguments @\("ls-files", "-v"\)/);
  assert.match(script, /-cmatch "\^\(\?:\[a-z\]\|S\) "/);
  assert.match(script, /\$WorktreeBase = "D:\\pa6-w"/);
  assert.match(script, /\$CargoTargetBase = "D:\\rust-target\\ic6-candidate"/);
  assert.match(script, /\$NpmCacheRoot = "D:\\ProductAtelier-Cache\\npm"/);
  assert.match(script, /\$ProcessTempBase = "D:\\ProductAtelier-Temp\\ic6-process-temp"/);
  assert.match(script, /\$BuildToken = \[guid\]::NewGuid\(\)\.ToString\("N"\)/);
  assert.match(script, /\$BuildIdentity = "\$\(\$ExpectedCommit\.Substring\(0, 12\)\)-\$BuildToken"/);
  assert.match(script, /\$MaxLegacyCopyRootLength = 58/);
  assert.match(script, /\$IsolatedProjectRoot\.Length -gt \$MaxLegacyCopyRootLength/);
  assert.match(script, /\$env:CARGO_TARGET_DIR = Join-Path \$CargoTargetBase \$BuildIdentity/);
  assert.match(script, /\$env:npm_config_cache = \$NpmCacheRoot/);
  assert.match(script, /\$env:TEMP = \$ProcessTempRoot/);
  assert.match(script, /\$env:TMP = \$ProcessTempRoot/);
  assert.doesNotMatch(script, /(?<!\r)\n/, 'PowerShell entry point must use CRLF consistently');

  const sourceAssertions = script.match(/Assert-SourceState -Expected \$ExpectedCommit/g) || [];
  const isolatedAssertions = script.match(/Assert-IsolatedSourceState -Expected \$ExpectedCommit/g) || [];
  assert.ok(sourceAssertions.length >= 2, 'active source identity must be checked before build and publication');
  assert.ok(isolatedAssertions.length >= 2, 'detached source identity must be checked before and after build');
  const isolatedGate = script.slice(
    script.indexOf('function Assert-IsolatedSourceState'),
    script.indexOf('function Remove-IsolatedBuildWorktree'),
  );
  const hiddenIndexPosition = isolatedGate.indexOf(
    'Assert-NoHiddenTrackedEntriesAt -Repository $IsolatedProjectRoot',
  );
  const refreshPosition = isolatedGate.indexOf(
    'Refresh-GitIndexAt -Repository $IsolatedProjectRoot',
  );
  const statusPosition = isolatedGate.indexOf(
    '"status", "--porcelain=v1", "--untracked-files=no"',
  );
  assert.ok(hiddenIndexPosition >= 0, 'hidden tracked-entry gate is missing');
  assert.ok(refreshPosition >= 0, 'metadata refresh call is missing');
  assert.ok(statusPosition >= 0, 'tracked-content status gate is missing');
  assert.ok(
    hiddenIndexPosition < refreshPosition && refreshPosition < statusPosition,
    'metadata-only rewrites must be refreshed before the tracked-content gate',
  );
  assert.ok(
    script.indexOf('Assert-SourceState -Expected $ExpectedCommit -FetchOrigin')
      < script.indexOf('"worktree", "add", "--detach"'),
    'source identity must be verified before creating the detached source',
  );
  assert.match(script, /"worktree", "add", "--detach", \$IsolatedProjectRoot, \$ExpectedCommit/);
  assert.match(script, /git\.exe -C \$ProjectRoot worktree remove --force \$IsolatedProjectRoot/);
  assert.match(script, /Push-Location \$IsolatedProjectRoot/);
  assert.match(script, /function Remove-OwnedDirectory/);
  assert.match(script, /\[System\.IO\.Directory\]::Delete\(\$fullPath, \$true\)/);
  assert.match(script, /-PathToRemove \$ownedDirectory\.Path/);
  assert.match(script, /-ExpectedLeaf \$BuildIdentity/);
  assert.doesNotMatch(script, /Remove-Item[^\r\n]*-Recurse/i);

  const runKey = `${'a'.repeat(12)}-${'b'.repeat(32)}`;
  assert.ok(
    path.win32.join('D:\\pa6-w', runKey).length <= 58,
    'detached worktree root must leave room for PowerShell 5 sidecar replacement paths',
  );
});

test('IC6 candidate entry runs the required gates in order and stages last', () => {
  const npmCi = script.indexOf('@("ci", "--no-audit", "--no-fund", "--ignore-scripts")');
  const pythonTests = script.indexOf('@("-m", "unittest", "discover"');
  const frontendTests = script.indexOf('@("run", "test:frontend")');
  const viteBuild = script.indexOf('@("run", "build")');
  const canvasBundle = script.indexOf('@("run", "verify:canvas-bundle")');
  const rustTests = script.indexOf('"test", "--locked"');
  const rustCheck = script.indexOf('"check", "--locked"');
  const tauriRelease = script.indexOf('"--no-install", "tauri", "build", "--no-bundle"');
  const sidecar = script.indexOf('& $IsolatedSidecarBuild');
  const stage = script.indexOf('"stage",');

  for (const [label, position] of Object.entries({
    npmCi,
    pythonTests,
    frontendTests,
    viteBuild,
    canvasBundle,
    rustTests,
    rustCheck,
    tauriRelease,
    sidecar,
    stage,
  })) {
    assert.ok(position >= 0, `${label} gate is missing`);
  }
  assert.ok(npmCi < pythonTests);
  assert.ok(pythonTests < frontendTests);
  assert.ok(frontendTests < viteBuild);
  assert.ok(viteBuild < canvasBundle);
  assert.ok(canvasBundle < sidecar);
  assert.ok(sidecar < rustTests);
  assert.ok(rustTests < rustCheck);
  assert.ok(rustCheck < tauriRelease);
  assert.ok(tauriRelease < stage);

  assert.equal(
    (script.match(/& \$IsolatedSidecarBuild/g) || []).length,
    1,
    'the isolated sidecar must be built exactly once',
  );
  assert.match(
    script.slice(sidecar, rustTests),
    /if \(\$LASTEXITCODE -ne 0\) \{[\s\S]*?throw "Python sidecar build failed/,
    'a failed sidecar build must stop before Rust or Tauri can run',
  );
  assert.equal(
    (script.match(/"stage",/g) || []).length,
    1,
    'the canonical candidate must be staged exactly once',
  );

  assert.match(script, /\$CandidateDir = Join-Path \$ProjectRoot "build\\portable-candidate-current"/);
  assert.match(script, /"--candidate-dir", \$CandidateDir/);
  assert.match(script, /"--git-commit", \$ExpectedCommit/);
  assert.doesNotMatch(
    script.slice(stage + '"stage",'.length),
    /Assert-[A-Za-z]+|Invoke-CheckedNative/,
    'no additional gate may leave a valid canonical candidate behind after stage',
  );
});

test('IC6 candidate entry cannot reach formal release or GUI helpers', () => {
  assert.doesNotMatch(script, /release\\ProductAtelier-Portable/i);
  assert.doesNotMatch(script, /tools[\\/]dev\.ps1/i);
  assert.doesNotMatch(script, /Test-Portable(?:-App)?\.ps1/i);
  assert.doesNotMatch(script, /\b(?:begin|finalize|rollback)\b/i);
  assert.doesNotMatch(script, /Start-Process|Stop-Process|taskkill|Win32_Process/i);
  assert.doesNotMatch(script, /WScript\.Shell|CreateShortcut|\.lnk/i);
});
