import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const scriptPath = path.join(root, 'tools', 'build_ic6_candidate.ps1');
const scriptBytes = fs.readFileSync(scriptPath);
const script = scriptBytes.toString('utf8').replace(/^\uFEFF/, '');

const detachHarness = String.raw`
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  $env:PA_IC6_SCRIPT,
  [ref]$tokens,
  [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) { throw ($parseErrors | Out-String) }
$functionNames = @(
  'Assert-NoReparsePath',
  'Initialize-CandidateBuildFileIdentity',
  'Get-StableWindowsFileIdentity',
  'Test-SameFileIdentity',
  'Assert-RegularArtifactHandle',
  'Get-StreamSha256',
  'New-DetachedCargoArtifact'
)
foreach ($functionName in $functionNames) {
  $definition = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -ceq $functionName
  }, $true)
  if (-not $definition) { throw "Missing function: $functionName" }
  Invoke-Expression $definition.Extent.Text
}
$cases = $env:PA_IC6_CASES | ConvertFrom-Json
$results = foreach ($case in $cases) {
  $snapshot = $null
  try {
    $destination = Join-Path $case.destinationRoot 'product-atelier-stage-source.exe'
    $detachArguments = @{
      SourcePath = [string]$case.source
      ExpectedPeerPath = [string]$case.peer
      CargoRoot = [string]$case.cargoRoot
      DestinationPath = $destination
      DestinationRoot = [string]$case.destinationRoot
    }
    $snapshot = New-DetachedCargoArtifact @detachArguments
    $writeBlocked = $false
    try {
      $probe = [System.IO.File]::Open(
        $snapshot.Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
      )
      $probe.Dispose()
    } catch {
      $writeBlocked = $true
    }
    $readAllowed = $false
    try {
      $readProbe = [System.IO.File]::Open(
        $snapshot.Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
      )
      $readAllowed = $true
      $readProbe.Dispose()
    } catch {
      $readAllowed = $false
    }
    [pscustomobject]@{
      name = [string]$case.name
      ok = $true
      path = [string]$snapshot.Path
      sha256 = [string]$snapshot.Sha256
      length = [uint64]$snapshot.Length
      read_allowed_while_staged = $readAllowed
      write_blocked_while_staged = $writeBlocked
      error = ''
    }
  } catch {
    [pscustomobject]@{
      name = [string]$case.name
      ok = $false
      path = ''
      sha256 = ''
      length = 0
      read_allowed_while_staged = $false
      write_blocked_while_staged = $false
      error = [string]$_.Exception.Message
    }
  } finally {
    if ($snapshot -and $snapshot.Lock) { $snapshot.Lock.Dispose() }
  }
}
ConvertTo-Json -InputObject @($results) -Compress
`;

function runDetachCases(cases) {
  const result = childProcess.spawnSync(
    'powershell.exe',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', detachHarness],
    {
      cwd: root,
      encoding: 'utf8',
      env: {
        ...process.env,
        PA_IC6_SCRIPT: scriptPath,
        PA_IC6_CASES: JSON.stringify(cases),
      },
    },
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  return JSON.parse(result.stdout.trim());
}

function cargoCase(base, name, {
  linkPeer = false,
  wrongPeer = false,
  externalLinks = 0,
  targetExists = false,
  missingDestinationRoot = false,
} = {}) {
  const cargoRoot = path.join(base, `${name}-cargo`);
  const release = path.join(cargoRoot, 'release');
  const deps = path.join(release, 'deps');
  const destinationRoot = path.join(base, `${name}-temp`);
  const source = path.join(release, 'product-atelier.exe');
  const peer = path.join(deps, 'product_atelier.exe');
  fs.mkdirSync(deps, { recursive: true });
  if (!missingDestinationRoot) fs.mkdirSync(destinationRoot, { recursive: true });
  fs.writeFileSync(source, `candidate-${name}`);
  if (linkPeer) fs.linkSync(source, peer);
  else if (wrongPeer) fs.writeFileSync(peer, 'unrelated-cargo-peer');
  for (let index = 0; index < externalLinks; index += 1) {
    fs.linkSync(source, path.join(base, `${name}-external-${index}.exe`));
  }
  if (targetExists) {
    fs.writeFileSync(path.join(destinationRoot, 'product-atelier-stage-source.exe'), 'occupied');
  }
  return { name, cargoRoot, source, peer, destinationRoot };
}

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
  assert.match(script, /"rev-parse", "--verify", "HEAD"/);
  assert.match(script, /"rev-parse", "--verify", \$RequiredUpstream/);
  assert.match(script, /fetch --no-tags origin/);
  assert.match(script, /@\(& git\.exe -C \$Repository @QuietArguments 2>&1\)/);
  assert.match(script, /"diff", "--cached", "--quiet", "--no-ext-diff", "--ita-visible-in-index", "--ignore-submodules=none", "HEAD", "--"/);
  assert.match(script, /"diff", "--quiet", "--no-ext-diff", "--ignore-submodules=none", "--"/);
  assert.match(script, /"diff", "--cached", "--name-status", "--no-ext-diff", "--ita-visible-in-index", "--ignore-submodules=none", "HEAD", "--"/);
  assert.match(script, /"diff", "--name-status", "--no-ext-diff", "--ignore-submodules=none", "--"/);
  assert.match(script, /\$exitCode -ne 1/);
  assert.match(script, /"ls-files", "--others", "--exclude-standard"/);
  assert.match(script, /-Arguments @\("ls-files", "-v"\)/);
  assert.match(script, /-cmatch "\^\(\?:\[a-z\]\|S\) "/);
  assert.match(script, /-Arguments @\("ls-files", "--stage"\)/);
  assert.match(script, /-cmatch "\^160000 "/);
  assert.match(script, /"diff", "--check", "HEAD", "--"/);
  assert.doesNotMatch(script, /update-index --really-refresh/);
  assert.doesNotMatch(script, /"status", "--porcelain/);
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
  assert.match(script, /\$SourceExePeer = Join-Path \$env:CARGO_TARGET_DIR "release\\deps\\product_atelier\.exe"/);
  assert.match(script, /\$DetachedApp = Join-Path \$ProcessTempRoot "product-atelier-stage-source\.exe"/);
  assert.match(script, /function Initialize-CandidateBuildFileIdentity/);
  assert.match(script, /function New-DetachedCargoArtifact/);
  assert.match(script, /FILE_SHARE_READ = 0x00000001/);
  assert.match(script, /FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000/);
  assert.match(script, /Initialize-CandidateBuildFileIdentity[\s\S]{0,500}\$fullCargoRoot/);
  assert.match(script, /\[uint64\]\$sourceBefore\.NumberOfLinks -notin @\(1, 2\)/);
  assert.match(script, /Test-SameFileIdentity -Left \$sourceBefore -Right \$peerBefore/);
  assert.match(script, /\[uint64\]\$peerBefore\.NumberOfLinks -ne 2/);
  assert.match(script, /CREATE_NEW = 1/);
  assert.match(script, /CreateReadWriteNoFollow/);
  assert.match(script, /\$sourceHashBefore -cne \$sourceHashAfter/);
  assert.match(script, /\$destinationHash -cne \$sourceHashBefore/);
  assert.match(script, /\[uint64\]\$destinationAfter\.NumberOfLinks -ne 1/);
  assert.match(script, /\$DetachedAppLock = \$DetachedAppSnapshot\.Lock/);
  assert.match(script, /\$DetachedAppLock\.Dispose\(\)/);
  assert.doesNotMatch(script, /(?<!\r)\n/, 'PowerShell entry point must use CRLF consistently');

  const sourceAssertions = script.match(/Assert-SourceState -Expected \$ExpectedCommit/g) || [];
  const isolatedAssertions = script.match(/Assert-IsolatedSourceState -Expected \$ExpectedCommit/g) || [];
  assert.ok(sourceAssertions.length >= 2, 'active source identity must be checked before build and publication');
  assert.ok(isolatedAssertions.length >= 2, 'detached source identity must be checked before and after build');
  const contentGate = script.slice(
    script.indexOf('function Assert-CleanSourceAt'),
    script.indexOf('function Assert-NoReparsePath'),
  );
  const gitlinkPosition = contentGate.indexOf('Assert-NoGitlinksAt');
  const hiddenIndexPosition = contentGate.indexOf('Assert-NoHiddenTrackedEntriesAt');
  const trackedPosition = contentGate.indexOf('Assert-NoTrackedContentChangesAt');
  const untrackedPosition = contentGate.indexOf('Assert-NoUntrackedEntriesAt');
  const whitespacePosition = contentGate.indexOf('"diff", "--check", "HEAD", "--"');
  assert.ok(gitlinkPosition >= 0, 'submodule rejection gate is missing');
  assert.ok(hiddenIndexPosition >= 0, 'hidden tracked-entry gate is missing');
  assert.ok(trackedPosition >= 0, 'tracked-content gate is missing');
  assert.ok(untrackedPosition >= 0, 'untracked-content gate is missing');
  assert.ok(whitespacePosition >= 0, 'whitespace gate is missing');
  assert.ok(
    gitlinkPosition < hiddenIndexPosition
      && hiddenIndexPosition < trackedPosition
      && trackedPosition < untrackedPosition
      && untrackedPosition < whitespacePosition,
    'gitlink, hidden, tracked, untracked, and whitespace gates must run in fail-closed order',
  );
  assert.match(
    script.slice(
      script.indexOf('function Assert-IsolatedSourceState'),
      script.indexOf('function Remove-IsolatedBuildWorktree'),
    ),
    /Assert-CleanSourceAt[\s\S]*?-Repository \$IsolatedProjectRoot/,
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
  const detach = script.indexOf('$DetachedAppSnapshot = New-DetachedCargoArtifact');
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
    detach,
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
  assert.ok(tauriRelease < detach);
  assert.ok(detach < stage);
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
  assert.match(script, /"--app-exe", \$DetachedAppSnapshot\.Path/);
  assert.match(script, /"--candidate-dir", \$CandidateDir/);
  assert.match(script, /"--git-commit", \$ExpectedCommit/);
  assert.doesNotMatch(
    script.slice(stage + '"stage",'.length),
    /Assert-[A-Za-z]+|Invoke-CheckedNative/,
    'no additional gate may leave a valid canonical candidate behind after stage',
  );
});

test('IC6 candidate entry detaches real single-link and Cargo two-link artifacts', { skip: process.platform !== 'win32' }, () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'pa-ic6-detach-ok-'));
  try {
    const cases = [
      cargoCase(temp, 'single'),
      cargoCase(temp, 'cargo-pair', { linkPeer: true }),
    ];
    const results = runDetachCases(cases);
    assert.equal(results.length, cases.length);
    for (const [index, result] of results.entries()) {
      assert.equal(result.ok, true, result.error);
      assert.equal(result.read_allowed_while_staged, true);
      assert.equal(result.write_blocked_while_staged, true);
      assert.equal(fs.statSync(result.path).nlink, 1);
      assert.equal(fs.readFileSync(result.path, 'utf8'), `candidate-${cases[index].name}`);
    }
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test('IC6 candidate entry rejects non-Cargo links and unsafe detach targets', { skip: process.platform !== 'win32' }, () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'pa-ic6-detach-fail-'));
  try {
    const cases = [
      cargoCase(temp, 'external-peer', { externalLinks: 1 }),
      cargoCase(temp, 'wrong-peer', { wrongPeer: true, externalLinks: 1 }),
      cargoCase(temp, 'third-link', { linkPeer: true, externalLinks: 1 }),
      cargoCase(temp, 'target-exists', { targetExists: true }),
      cargoCase(temp, 'copy-open-failure', { missingDestinationRoot: true }),
    ];
    const results = runDetachCases(cases);
    assert.deepEqual(results.map((entry) => entry.ok), [false, false, false, false, false]);
    assert.match(results[0].error, /exact peer|cannot find|could not find|找不到/i);
    assert.match(results[1].error, /exact peer/i);
    assert.match(results[2].error, /exact two links/i);
    assert.match(results[3].error, /already exists/i);
    assert.match(results[4].error, /Could not find a part|path/i);
    for (const entry of cases) {
      if (entry.name !== 'target-exists') {
        assert.equal(
          fs.existsSync(path.join(entry.destinationRoot, 'product-atelier-stage-source.exe')),
          false,
        );
      }
    }
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});

test('IC6 candidate entry cannot reach formal release or GUI helpers', () => {
  assert.doesNotMatch(script, /release\\ProductAtelier-Portable/i);
  assert.doesNotMatch(script, /tools[\\/]dev\.ps1/i);
  assert.doesNotMatch(script, /Test-Portable(?:-App)?\.ps1/i);
  assert.doesNotMatch(script, /\b(?:begin|finalize|rollback)\b/i);
  assert.doesNotMatch(script, /Start-Process|Stop-Process|taskkill|Win32_Process/i);
  assert.doesNotMatch(script, /WScript\.Shell|CreateShortcut|\.lnk/i);
});
