import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const scriptPath = path.join(root, 'tools/build_ic6_installer_candidate.ps1');
const validatorPath = path.join(root, 'tools/validate_ic6_installer_candidate.py');
const scriptBytes = fs.readFileSync(scriptPath);
const script = scriptBytes.toString('utf8').replace(/^\uFEFF/, '');
const validator = fs.readFileSync(validatorPath, 'utf8');

test('IC6 installer entry is commit-bound, CRLF, and isolates every large path on D', () => {
  assert.deepEqual(
    [...scriptBytes.subarray(0, 3)],
    [0xef, 0xbb, 0xbf],
    'PowerShell entry point must use UTF-8 with BOM',
  );
  assert.match(script, /\r\n/);
  assert.doesNotMatch(script, /(?<!\r)\n/, 'PowerShell entry point must use CRLF only');
  assert.match(script, /\[Parameter\(Mandatory = \$true\)\]/);
  assert.match(script, /\[ValidatePattern\("\^\[0-9a-fA-F\]\{40\}\$"\)\]/);
  assert.match(script, /\$WarningPreference = "Continue"/);
  assert.match(script, /\$RequiredBranch = "codex\/excalidraw-infinite-canvas"/);
  assert.match(script, /\$RequiredUpstream = "origin\/\$RequiredBranch"/);
  assert.match(script, /fetch --no-tags origin/);
  assert.match(script, /@\(& git\.exe -C \$RepositoryPath @QuietArguments 2>&1\)/);
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
  assert.match(script, /"rev-parse", "--verify", "HEAD"/);
  assert.match(script, /"rev-parse", "--verify", \$RequiredUpstream/);

  assert.match(script, /\$BuildToken = \[guid\]::NewGuid\(\)\.ToString\("N"\)/);
  assert.match(script, /\$RunKey = "\$\(\$ExpectedCommit\.Substring\(0, 12\)\)-\$BuildToken"/);
  assert.match(script, /\$WorktreeLeaf = \$RunKey/);
  assert.match(script, /\$CargoTargetLeaf = \$RunKey/);
  assert.match(script, /\$env:CARGO_TARGET_DIR = Join-Path \$CargoTargetBase \$CargoTargetLeaf/);
  assert.match(script, /\$env:npm_config_cache = \$NpmCacheRoot/);
  assert.match(script, /\$env:TEMP = \$BuildTemp/);
  assert.match(script, /\$env:TMP = \$BuildTemp/);
  assert.match(script, /\$BuildTemp = Join-Path \$TempBase \$WorktreeLeaf/);
  assert.match(script, /foreach \(\$isolatedPath in @\([\s\S]*?\$env:npm_config_cache,[\s\S]*?\$env:TEMP,[\s\S]*?\$env:TMP/);
  assert.match(script, /GetPathRoot\(\$isolatedPath\)[\s\S]*?"D:\\"/);
  assert.match(script, /Assert-RegularDirectory -PathToCheck \$env:npm_config_cache/);
  assert.match(script, /Assert-RegularDirectory -PathToCheck \$env:TEMP/);
  assert.match(script, /Assert-RegularDirectory -PathToCheck \$env:TMP/);
  assert.match(script, /\$MaxLegacyBuildRootLength = 90/);
  assert.match(script, /foreach \(\$legacyBuildRoot in @\(\$IsolatedWorktree, \$env:CARGO_TARGET_DIR, \$BuildTemp\)\)/);
  assert.match(script, /\$legacyBuildRoot\.Length -gt \$MaxLegacyBuildRootLength/);

  const runKey = `${'a'.repeat(12)}-${'b'.repeat(32)}`;
  for (const base of [
    'D:\\ProductAtelier-IC6-Installer-Worktrees',
    'D:\\rust-target\\ic6-installer-candidate',
    'D:\\ProductAtelier-IC6-Installer-Temp',
  ]) {
    assert.ok(
      path.win32.join(base, runKey).length <= 90,
      `${base} must leave enough room for legacy Windows build descendants`,
    );
  }
});

test('NSIS build uses only a unique detached worktree and its pinned local Tauri CLI', () => {
  assert.match(script, /"worktree", "add", "--detach", \$IsolatedWorktree, \$ExpectedCommit/);
  assert.match(script, /\$TauriConfigPath = Join-Path \$IsolatedWorktree "src-tauri\\tauri\.conf\.json"/);
  assert.match(script, /\$WindowsTauriConfigPath = Join-Path \$IsolatedWorktree "src-tauri\\tauri\.windows\.conf\.json"/);
  assert.match(script, /\$IsolatedPortableReleaseTool = Join-Path \$IsolatedWorktree/);
  assert.match(script, /Push-Location \$IsolatedWorktree/);
  assert.doesNotMatch(script, /Push-Location \$ProjectRoot/);
  assert.match(script, /-Arguments @\("ci", "--no-audit", "--no-fund", "--ignore-scripts"\)/);
  assert.match(script, /& npx\.cmd --no-install tauri build --bundles nsis --features custom-protocol --no-sign/);
  assert.doesNotMatch(script, /& npx\.cmd tauri build/);

  const contentGate = script.slice(
    script.indexOf('function Assert-CleanSourceAt'),
    script.indexOf('function Update-OriginTrackingRef'),
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
      script.indexOf('function Assert-IsolatedWorktreeState'),
      script.indexOf('function Remove-IsolatedDetachedWorktree'),
    ),
    /Assert-CleanSourceAt[\s\S]*?-RepositoryPath \$IsolatedWorktree/,
  );

  const sourceGate = script.indexOf('Assert-SourceState -FetchOrigin');
  const worktreeAdd = script.indexOf('    New-IsolatedDetachedWorktree\r\n');
  const promotionLock = script.indexOf('$portableLockStream = Enter-PortablePromotionLock');
  const candidateGate = script.indexOf('$candidateEvidence = Invoke-CanonicalCandidateValidation');
  const configRead = script.indexOf('$tauriConfig = Get-Content -LiteralPath $TauriConfigPath');
  const sidecarCopy = script.indexOf('Copy-Item -LiteralPath $canonicalSidecarDir -Destination $isolatedBinRoot -Recurse');
  const npmCi = script.indexOf('-Arguments @("ci", "--no-audit", "--no-fund", "--ignore-scripts")');
  const tauriBuild = script.indexOf('& npx.cmd --no-install tauri build');

  for (const [label, position] of Object.entries({
    sourceGate,
    worktreeAdd,
    promotionLock,
    candidateGate,
    configRead,
    sidecarCopy,
    npmCi,
    tauriBuild,
  })) {
    assert.ok(position >= 0, `${label} is missing`);
  }
  assert.ok(sourceGate < worktreeAdd);
  assert.ok(worktreeAdd < promotionLock);
  assert.ok(promotionLock < candidateGate);
  assert.ok(candidateGate < configRead);
  assert.ok(configRead < sidecarCopy);
  assert.ok(sidecarCopy < npmCi);
  assert.ok(npmCi < tauriBuild);
});

test('portable candidate is locked, copied, and revalidated throughout the build', () => {
  assert.match(script, /\$PortablePromotionLockPath = Join-Path \$BuildRoot "portable-promotion\.lock"/);
  assert.match(script, /\[System\.IO\.FileMode\]::OpenOrCreate/);
  assert.match(script, /\[System\.IO\.FileShare\]::ReadWrite/);
  assert.match(script, /\$stream\.Lock\(0, 1\)/);
  assert.match(script, /\$Stream\.Unlock\(0, 1\)/);
  assert.doesNotMatch(script, /Delete\(\$PortablePromotionLockPath\)|Remove-Item[^\r\n]*PortablePromotionLockPath/);

  assert.match(
    script,
    /\$IsolatedCandidateValidationTool = Join-Path \$IsolatedWorktree "tools\\validate_ic6_installer_candidate\.py"/,
  );
  assert.match(script, /\$IsolatedCandidateValidationTool,[\s\S]*?--portable-release-tool/);
  assert.match(script, /--expected-candidate-identity-sha256/);
  assert.match(script, /--packaging-sidecar/);
  const validationFunction = script.slice(
    script.indexOf('function Invoke-CanonicalCandidateValidation'),
    script.indexOf('function Assert-CanonicalCandidateEvidence'),
  );
  assert.doesNotMatch(validationFunction, /"-c"|\$validationCode|@'/);
  assert.match(validator, /release_module\.verify_candidate_identity\(/);
  assert.match(
    validator,
    /expected_candidate_identity_sha256=expected_candidate_identity_sha256/,
  );
  assert.match(validator, /"candidate_identity": verified\["identity_receipt"\]/);
  assert.match(validator, /candidate_path \/ "python-server"/);
  assert.match(
    validator,
    /payload\["packaging_sidecar"\] = release_module\.directory_inventory\(/,
  );
  assert.match(script, /Isolated packaged sidecar \$field does not match the canonical candidate/);
  assert.match(script, /\$copiedSidecarEvidence\.CanonicalFingerprint -cne \$candidateEvidence\.CanonicalFingerprint/);
  assert.match(script, /\$prePublishEvidence\.CanonicalFingerprint -cne \$candidateEvidence\.CanonicalFingerprint/);
  assert.match(script, /\$reviewedCandidateIdentitySha256 = \[string\]\$candidateEvidence\.CandidateIdentitySha256/);
  const exactIdentityChecks =
    script.match(/-ExpectedCandidateIdentitySha256 \$reviewedCandidateIdentitySha256/g) || [];
  assert.ok(exactIdentityChecks.length >= 2, 'candidate identity must be locked after capture');

  const lock = script.indexOf('$portableLockStream = Enter-PortablePromotionLock');
  const firstValidation = script.indexOf('$candidateEvidence = Invoke-CanonicalCandidateValidation');
  const finalValidation = script.indexOf('$prePublishEvidence = Invoke-CanonicalCandidateValidation');
  const publication = script.indexOf('[System.IO.Directory]::Move($StagingDir, $DestinationDir)');
  const unlockCall = script.lastIndexOf('Exit-PortablePromotionLock -Stream $portableLockStream');
  assert.ok(lock < firstValidation);
  assert.ok(firstValidation < finalValidation);
  assert.ok(finalValidation < publication);
  assert.ok(publication < unlockCall, 'promotion lock must be held through atomic publication');
});

test(
  'portable promotion lock rejects a hardlink before touching its external target',
  { skip: process.platform !== 'win32' },
  (t) => {
    const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ic6-installer-lock-'));
    try {
      const buildRoot = path.join(tempRoot, 'build');
      const externalTarget = path.join(tempRoot, 'external-lock-target');
      const lockPath = path.join(buildRoot, 'portable-promotion.lock');
      const probePath = path.join(tempRoot, 'lock-probe.ps1');
      fs.mkdirSync(buildRoot);
      fs.writeFileSync(externalTarget, Buffer.alloc(0));
      try {
        fs.linkSync(externalTarget, lockPath);
      } catch (error) {
        t.skip(`test filesystem does not permit hardlinks: ${error.message}`);
        return;
      }

      const probe = String.raw`[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [Parameter(Mandatory = $true)]
    [string]$BuildRootPath,
    [Parameter(Mandatory = $true)]
    [string]$LockPath
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $ScriptPath,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw ($parseErrors.Message -join '; ')
}

foreach ($functionName in @(
    'Test-SamePath',
    'Assert-NoReparsePath',
    'Assert-RegularDirectory',
    'Get-StableWindowsFileIdentity',
    'Enter-PortablePromotionLock'
)) {
    $definitions = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $functionName
    }, $true))
    if ($definitions.Count -ne 1) {
        throw "Expected exactly one function definition for $functionName"
    }
    Invoke-Expression $definitions[0].Extent.Text
}

$BuildRoot = [System.IO.Path]::GetFullPath($BuildRootPath)
$PortablePromotionLockPath = [System.IO.Path]::GetFullPath($LockPath)
$stream = $null
try {
    $stream = Enter-PortablePromotionLock
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 17
}
if ($null -ne $stream) {
    $stream.Dispose()
}
[Console]::Error.WriteLine('Hardlinked promotion lock was unexpectedly acquired.')
exit 3
`;
      fs.writeFileSync(probePath, probe, 'utf8');

      const powershell = path.join(
        process.env.SystemRoot ?? 'C:\\Windows',
        'System32',
        'WindowsPowerShell',
        'v1.0',
        'powershell.exe',
      );
      const result = spawnSync(
        powershell,
        [
          '-NoLogo',
          '-NoProfile',
          '-NonInteractive',
          '-ExecutionPolicy',
          'Bypass',
          '-File',
          probePath,
          '-ScriptPath',
          scriptPath,
          '-BuildRootPath',
          buildRoot,
          '-LockPath',
          lockPath,
        ],
        { encoding: 'utf8', windowsHide: true },
      );

      assert.equal(result.status, 17, result.stderr || result.stdout);
      assert.match(result.stderr, /hard link/i);
      assert.equal(fs.statSync(externalTarget).size, 0);
    } finally {
      fs.rmSync(tempRoot, { force: true, recursive: true });
    }
  },
);

test('owned cleanup uses an explicit non-recursive stack and rejects child reparse points', () => {
  const cleanupStart = script.indexOf('function Remove-VerifiedOwnedTree');
  const cleanupEnd = script.indexOf('function Remove-OwnedStaging', cleanupStart);
  assert.ok(cleanupStart >= 0 && cleanupEnd > cleanupStart);
  const cleanup = script.slice(cleanupStart, cleanupEnd);

  assert.match(cleanup, /New-Object System\.Collections\.Stack/);
  assert.match(cleanup, /Microsoft\.PowerShell\.Management\\Get-ChildItem/);
  assert.doesNotMatch(
    script,
    /Get-ChildItem[^\r\n]*(?:`\r\n[^\r\n]*)*\s-Recurse\b/i,
    'Get-ChildItem cleanup enumeration must never use -Recurse',
  );

  const childReparseCheck = cleanup.indexOf(
    '($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint)',
  );
  const childDirectoryPush = cleanup.indexOf('$pendingDirectories.Push($child.FullName)');
  assert.ok(childReparseCheck >= 0, 'child reparse-point check is missing');
  assert.ok(childDirectoryPush >= 0, 'child directory stack push is missing');
  assert.ok(
    childReparseCheck < childDirectoryPush,
    'child reparse points must be rejected before a directory is pushed',
  );

  assert.match(cleanup, /\[System\.IO\.File\]::Delete\(\$file\)/);
  assert.match(cleanup, /\[System\.IO\.Directory\]::Delete\(\$directory, \$false\)/);
  assert.doesNotMatch(cleanup, /Directory\]::Delete\([^\r\n]*, \$true\)/);
  for (const cleanupFunction of [
    'Remove-OwnedStaging',
    'Remove-OwnedBuildTemp',
    'Remove-OwnedCargoTarget',
  ]) {
    assert.match(
      script,
      new RegExp(`function ${cleanupFunction}[\\s\\S]*?Remove-VerifiedOwnedTree`),
    );
  }
});

test('publication is exclusive, hash-bound, and has no fallible post-publication gate', () => {
  assert.match(script, /\$InstallerCandidateRoot = Join-Path \$BuildRoot "installer-candidate"/);
  assert.match(script, /\$DestinationDir = Join-Path \$InstallerCandidateRoot \$ExpectedCommit/);
  assert.match(script, /\[System\.IO\.FileMode\]::CreateNew/);
  assert.match(script, /\[System\.IO\.FileShare\]::None/);
  assert.match(script, /sourceHashBeforeCopy/);
  assert.match(script, /sourceHashAfterCopy/);
  assert.match(script, /\$sourceHashBeforeCopy -cne \$stagedHash/);
  assert.match(script, /Get-AuthenticodeSignature -LiteralPath \$DefaultNsisOutput/);
  assert.match(script, /Status -ne "NotSigned"/);
  assert.match(script, /\[System\.IO\.Directory\]::Move\(\$StagingDir, \$DestinationDir\)/);
  assert.match(script, /Assert-NoReparsePath/);
  assert.match(script, /FileAttributes\]::ReparsePoint/);

  const publication = script.indexOf('[System.IO.Directory]::Move($StagingDir, $DestinationDir)');
  const cleanup = script.indexOf('} finally {', publication);
  assert.ok(publication >= 0 && cleanup > publication);
  const afterPublication = script.slice(publication, cleanup);
  assert.doesNotMatch(afterPublication, /Assert-|Get-FileHash|Get-AuthenticodeSignature|throw\s/);
  assert.match(afterPublication, /\$StagingDir = ""[\s\S]*\$published = \$true/);

  assert.match(script, /"worktree", "remove", "--force", \$fullPath/);
  assert.match(script, /Test-SamePath \$parent \$WorktreeBase/);
  assert.match(script, /\$leaf -cne \$WorktreeLeaf/);
  assert.match(script, /function Remove-OwnedCargoTarget/);
  assert.match(script, /Test-SamePath \$parent \$CargoTargetBase/);
  assert.match(script, /\$leaf -cne \$CargoTargetLeaf/);
  assert.match(script, /\$cargoTargetCreated = \$true/);
  assert.match(script, /if \(\$cargoTargetCreated\) \{[\s\S]*?Remove-OwnedCargoTarget/);
  assert.match(script, /Could not clean the unique D: Cargo target/);
  assert.doesNotMatch(script, /Remove-Item[^\r\n]*-Recurse/i);
  assert.match(script, /catch \{\r\n\s+Write-Warning "Could not clean the unique detached worktree/);
  assert.match(script, /catch \{\r\n\s+Write-Warning "Could not clean the unique D: build temp/);

  for (const field of [
    'schema_version = 3',
    'build_mode = "detached-worktree"',
    'build_token = $BuildToken',
    'detached_head = $ExpectedCommit',
    'cargo_target_key = $CargoTargetLeaf',
    'app_sha256',
    'sidecar_sha256',
    'sidecar_manifest_sha256',
    'identity_receipt_relative_path',
    'identity_receipt_sha256 = $reviewedCandidateIdentitySha256',
    'identity_receipt_format_version',
    'identity_receipt_kind',
    'tree_sha256',
    'sidecar_tree_sha256',
    'sha256 = $stagedHash',
    'size_bytes = [long]$stagedItem.Length',
    'authenticode_status = "NotSigned"',
  ]) {
    assert.ok(script.includes(field), `manifest evidence is missing ${field}`);
  }
});

test('IC6 installer candidate entry cannot promote, execute, install, or touch formal paths', () => {
  assert.doesNotMatch(script, /build-installer\.bat/i);
  assert.doesNotMatch(script, /build_ic6_candidate\.ps1/i);
  assert.doesNotMatch(script, /tools[\\/]dev\.ps1/i);
  assert.doesNotMatch(script, /Build-SignedInstaller|Test-SignedInstaller/i);
  assert.doesNotMatch(script, /Test-Portable(?:-App)?\.ps1/i);
  assert.doesNotMatch(script, /release\\ProductAtelier-(?:Portable|Installer)/i);
  assert.doesNotMatch(script, /(?:"|')(?:begin|finalize|rollback)(?:"|')/i);
  assert.doesNotMatch(script, /Start-Process|Stop-Process|taskkill|Win32_Process/i);
  assert.doesNotMatch(script, /WScript\.Shell|CreateShortcut|\.lnk/i);
  assert.doesNotMatch(script, /launch_and_shoot|verify_formal_webview/i);
  assert.doesNotMatch(script, /uninstall|\/D=|\/@UPDATE/i);
});
