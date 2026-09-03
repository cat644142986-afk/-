# Product Atelier IC6 unsigned NSIS candidate-only build entry point.
# Builds from a unique detached worktree and never executes or promotes the app.
# The NSIS bundle input is the exact portable candidate app that already passed smoke.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{40}$")]
    [string]$ExpectedCommit
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
$WarningPreference = "Continue"

if ($env:OS -ne "Windows_NT") {
    throw "The IC6 installer candidate entry point requires Windows."
}
if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "Windows PowerShell 5 or newer is required."
}

$ExpectedCommit = $ExpectedCommit.ToLowerInvariant()
$BuildToken = [guid]::NewGuid().ToString("N")
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = Join-Path $ProjectRoot "build"
$RequiredBranch = "codex/excalidraw-infinite-canvas"
$RequiredUpstream = "origin/$RequiredBranch"
$CanonicalCandidateDir = Join-Path $BuildRoot "portable-candidate-current"
$CanonicalCandidateIdentityPath = Join-Path $BuildRoot "portable-candidate-current.identity.json"
$PortablePromotionLockPath = Join-Path $BuildRoot "portable-promotion.lock"
$InstallerCandidateRoot = Join-Path $BuildRoot "installer-candidate"
$DestinationDir = Join-Path $InstallerCandidateRoot $ExpectedCommit
$WorktreeBase = "D:\ProductAtelier-IC6-Installer-Worktrees"
$RunKey = "$($ExpectedCommit.Substring(0, 12))-$BuildToken"
$WorktreeLeaf = $RunKey
$IsolatedWorktree = Join-Path $WorktreeBase $WorktreeLeaf
$IsolatedPortableReleaseTool = Join-Path $IsolatedWorktree "tools\portable_release.py"
$IsolatedCandidateValidationTool = Join-Path $IsolatedWorktree "tools\validate_ic6_installer_candidate.py"
$IsolatedBundleIdentityTool = Join-Path $IsolatedWorktree "tools\tauri_bundle_app_identity.py"
$TauriBundleIdentityAlgorithm = "tauri-bundler-v2-bundle-type-marker-v1"
$TauriConfigPath = Join-Path $IsolatedWorktree "src-tauri\tauri.conf.json"
$WindowsTauriConfigPath = Join-Path $IsolatedWorktree "src-tauri\tauri.windows.conf.json"
$PackagingSidecarDir = Join-Path $IsolatedWorktree "src-tauri\bin\python-server"
$CargoTargetBase = "D:\rust-target\ic6-installer-candidate"
$CargoTargetLeaf = $RunKey
$env:CARGO_TARGET_DIR = Join-Path $CargoTargetBase $CargoTargetLeaf
$NpmCacheRoot = "D:\ProductAtelier-IC6-Installer-Npm-Cache"
$TempBase = "D:\ProductAtelier-IC6-Installer-Temp"
$BuildTemp = Join-Path $TempBase $WorktreeLeaf
$MaxLegacyBuildRootLength = 90
foreach ($legacyBuildRoot in @($IsolatedWorktree, $env:CARGO_TARGET_DIR, $BuildTemp)) {
    if ($legacyBuildRoot.Length -gt $MaxLegacyBuildRootLength) {
        throw "IC6 installer build root exceeds the legacy Windows path budget: $legacyBuildRoot"
    }
}
$env:npm_config_cache = $NpmCacheRoot
$env:TEMP = $BuildTemp
$env:TMP = $BuildTemp
$env:PATH = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:USERPROFILE\.cargo\bin;C:\mingw64\bin;C:\msys64\mingw64\bin;$env:PATH"

function Test-SamePath([string]$Left, [string]$Right) {
    $leftFull = [System.IO.Path]::GetFullPath($Left).TrimEnd('\')
    $rightFull = [System.IO.Path]::GetFullPath($Right).TrimEnd('\')
    return [string]::Equals(
        $leftFull,
        $rightFull,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Assert-NoReparsePath([string]$PathToCheck, [string]$Label) {
    $current = [System.IO.Path]::GetFullPath($PathToCheck)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label may not contain a reparse point: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if (-not $parent -or (Test-SamePath $parent $current)) {
            break
        }
        $current = $parent
    }
}

function Assert-RegularFile([string]$PathToCheck, [string]$Label) {
    if (-not (Test-Path -LiteralPath $PathToCheck -PathType Leaf)) {
        throw "$Label is missing: $PathToCheck"
    }
    Assert-NoReparsePath -PathToCheck $PathToCheck -Label $Label
    $item = Get-Item -LiteralPath $PathToCheck -Force
    if ($item.PSIsContainer -or $item.Length -le 0) {
        throw "$Label must be a non-empty regular file: $PathToCheck"
    }
    return $item
}

function Assert-RegularDirectory([string]$PathToCheck, [string]$Label) {
    if (-not (Test-Path -LiteralPath $PathToCheck -PathType Container)) {
        throw "$Label is missing: $PathToCheck"
    }
    Assert-NoReparsePath -PathToCheck $PathToCheck -Label $Label
}

function Initialize-SafeDirectory([string]$PathToCreate, [string]$Label) {
    Assert-NoReparsePath -PathToCheck $PathToCreate -Label $Label
    if (-not (Test-Path -LiteralPath $PathToCreate)) {
        New-Item -ItemType Directory -Path $PathToCreate | Out-Null
    }
    Assert-RegularDirectory -PathToCheck $PathToCreate -Label $Label
}

function Invoke-GitCaptureAt(
    [string]$RepositoryPath,
    [string[]]$Arguments
) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git.exe -C $RepositoryPath @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Git command failed (git $($Arguments -join ' ')): $($output -join ' ')"
    }
    return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Invoke-GitCapture([string[]]$Arguments) {
    return Invoke-GitCaptureAt -RepositoryPath $ProjectRoot -Arguments $Arguments
}

function Assert-EmptyGitDiffAt(
    [string]$RepositoryPath,
    [string[]]$QuietArguments,
    [string[]]$ReportArguments,
    [string]$Label
) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git.exe -C $RepositoryPath @QuietArguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -eq 0) {
        return
    }
    if ($exitCode -ne 1) {
        throw "Git content check failed for ${Label}: $($output -join ' ')"
    }
    $changes = Invoke-GitCaptureAt `
        -RepositoryPath $RepositoryPath `
        -Arguments $ReportArguments
    if ([string]::IsNullOrWhiteSpace($changes)) {
        $changes = "(Git reported a content difference without naming a path)"
    }
    throw "$Label contains source changes:`n$changes"
}

function Assert-NoTrackedContentChangesAt([string]$RepositoryPath, [string]$Label) {
    Assert-EmptyGitDiffAt `
        -RepositoryPath $RepositoryPath `
        -QuietArguments @("diff", "--cached", "--quiet", "--no-ext-diff", "--ita-visible-in-index", "--ignore-submodules=none", "HEAD", "--") `
        -ReportArguments @("diff", "--cached", "--name-status", "--no-ext-diff", "--ita-visible-in-index", "--ignore-submodules=none", "HEAD", "--") `
        -Label "$Label index"
    Assert-EmptyGitDiffAt `
        -RepositoryPath $RepositoryPath `
        -QuietArguments @("diff", "--quiet", "--no-ext-diff", "--ignore-submodules=none", "--") `
        -ReportArguments @("diff", "--name-status", "--no-ext-diff", "--ignore-submodules=none", "--") `
        -Label "$Label worktree"
}

function Assert-NoHiddenTrackedEntriesAt([string]$RepositoryPath) {
    $entries = Invoke-GitCaptureAt `
        -RepositoryPath $RepositoryPath `
        -Arguments @("ls-files", "-v")
    $hiddenEntries = @(
        $entries -split "`n" | Where-Object { $_ -cmatch "^(?:[a-z]|S) " }
    )
    if ($hiddenEntries.Count -gt 0) {
        $summary = ($hiddenEntries | Select-Object -First 10) -join "`n"
        throw "IC6 source index contains hidden tracked entries:`n$summary"
    }
}

function Assert-NoGitlinksAt([string]$RepositoryPath) {
    $entries = Invoke-GitCaptureAt `
        -RepositoryPath $RepositoryPath `
        -Arguments @("ls-files", "--stage")
    $gitlinks = @(
        $entries -split "`n" | Where-Object { $_ -cmatch "^160000 " }
    )
    if ($gitlinks.Count -gt 0) {
        $summary = ($gitlinks | Select-Object -First 10) -join "`n"
        throw "IC6 source may not contain Git submodules:`n$summary"
    }
}

function Assert-NoUntrackedEntriesAt([string]$RepositoryPath, [string]$Label) {
    $entries = Invoke-GitCaptureAt `
        -RepositoryPath $RepositoryPath `
        -Arguments @("ls-files", "--others", "--exclude-standard")
    if (-not [string]::IsNullOrWhiteSpace($entries)) {
        throw "$Label has untracked, unignored files:`n$entries"
    }
}

function Assert-CleanSourceAt([string]$RepositoryPath, [string]$Label) {
    Assert-NoGitlinksAt -RepositoryPath $RepositoryPath
    Assert-NoHiddenTrackedEntriesAt -RepositoryPath $RepositoryPath
    Assert-NoTrackedContentChangesAt -RepositoryPath $RepositoryPath -Label $Label
    Assert-NoUntrackedEntriesAt -RepositoryPath $RepositoryPath -Label $Label
    [void](Invoke-GitCaptureAt `
        -RepositoryPath $RepositoryPath `
        -Arguments @("diff", "--check", "HEAD", "--"))
}

function Update-OriginTrackingRef {
    $fetchSucceeded = $false
    $lastFetchOutput = @()
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $lastFetchOutput = @(& git.exe -C $ProjectRoot fetch --no-tags origin 2>&1)
            $fetchExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
        foreach ($line in $lastFetchOutput) {
            Write-Host $line
        }
        if ($fetchExitCode -eq 0) {
            $fetchSucceeded = $true
            break
        }
        if ($attempt -lt 3) {
            Write-Warning "Origin fetch attempt $attempt failed; retrying without building."
            Start-Sleep -Seconds (2 * $attempt)
        }
    }
    if (-not $fetchSucceeded) {
        throw "Could not fetch origin after 3 attempts: $($lastFetchOutput -join ' ')"
    }
}

function Assert-SourceState([switch]$FetchOrigin) {
    Assert-CleanSourceAt -RepositoryPath $ProjectRoot -Label "IC6 installer source"
    $branch = Invoke-GitCapture -Arguments @("branch", "--show-current")
    if ($branch -ne $RequiredBranch) {
        throw "IC6 installer candidates require branch $RequiredBranch; current branch is $branch"
    }
    $upstream = Invoke-GitCapture -Arguments @(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", '@{upstream}'
    )
    if ($upstream -ne $RequiredUpstream) {
        throw "IC6 installer candidates require upstream $RequiredUpstream; current upstream is $upstream"
    }
    if ($FetchOrigin) {
        Update-OriginTrackingRef
    }
    $head = Invoke-GitCapture -Arguments @("rev-parse", "--verify", "HEAD")
    if (-not [string]::Equals(
        $head,
        $ExpectedCommit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Current HEAD $head does not match -ExpectedCommit $ExpectedCommit"
    }
    $upstreamHead = Invoke-GitCapture -Arguments @(
        "rev-parse", "--verify", $RequiredUpstream
    )
    if (-not [string]::Equals(
        $upstreamHead,
        $ExpectedCommit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Upstream HEAD $upstreamHead does not match -ExpectedCommit $ExpectedCommit"
    }
    Assert-CleanSourceAt -RepositoryPath $ProjectRoot -Label "IC6 installer source"
}

function New-IsolatedDetachedWorktree {
    Initialize-SafeDirectory -PathToCreate $WorktreeBase -Label "IC6 worktree base"
    if (Test-Path -LiteralPath $IsolatedWorktree) {
        throw "Unique IC6 worktree already exists: $IsolatedWorktree"
    }
    $output = Invoke-GitCapture -Arguments @(
        "worktree", "add", "--detach", $IsolatedWorktree, $ExpectedCommit
    )
    if ($output) {
        Write-Host $output
    }
    Assert-RegularDirectory -PathToCheck $IsolatedWorktree -Label "Detached IC6 worktree"
}

function Assert-IsolatedWorktreeState {
    Assert-RegularDirectory -PathToCheck $IsolatedWorktree -Label "Detached IC6 worktree"
    $head = Invoke-GitCaptureAt `
        -RepositoryPath $IsolatedWorktree `
        -Arguments @("rev-parse", "--verify", "HEAD")
    if (-not [string]::Equals(
        $head,
        $ExpectedCommit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Detached IC6 worktree HEAD $head does not match $ExpectedCommit"
    }
    $branch = Invoke-GitCaptureAt `
        -RepositoryPath $IsolatedWorktree `
        -Arguments @("branch", "--show-current")
    if (-not [string]::IsNullOrWhiteSpace($branch)) {
        throw "IC6 build worktree must remain detached; current branch is $branch"
    }
    Assert-CleanSourceAt `
        -RepositoryPath $IsolatedWorktree `
        -Label "Detached IC6 worktree"
}

function Remove-IsolatedDetachedWorktree {
    $fullPath = [System.IO.Path]::GetFullPath($IsolatedWorktree)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $fullPath))
    $leaf = Split-Path -Leaf $fullPath
    if (-not (Test-SamePath $parent $WorktreeBase) -or $leaf -cne $WorktreeLeaf) {
        throw "Refusing to clean an unowned detached worktree: $fullPath"
    }
    if (-not (Test-Path -LiteralPath $fullPath)) {
        return
    }
    Assert-NoReparsePath -PathToCheck $fullPath -Label "Detached IC6 worktree cleanup"
    [void](Invoke-GitCapture -Arguments @(
        "worktree", "remove", "--force", $fullPath
    ))
    if (Test-Path -LiteralPath $fullPath) {
        throw "Git did not remove the detached IC6 worktree: $fullPath"
    }
}

function Enter-PortablePromotionLock {
    Assert-RegularDirectory -PathToCheck $BuildRoot -Label "Build root"
    Assert-NoReparsePath `
        -PathToCheck $PortablePromotionLockPath `
        -Label "Portable promotion lock"
    $stream = [System.IO.File]::Open(
        $PortablePromotionLockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        Assert-NoReparsePath `
            -PathToCheck $PortablePromotionLockPath `
            -Label "Portable promotion lock"
        $item = Get-Item -LiteralPath $PortablePromotionLockPath -Force
        if ($item.PSIsContainer) {
            throw "Portable promotion lock must remain a regular file."
        }
        $initialIdentity = Get-StableWindowsFileIdentity -Stream $stream
        if ([uint64]$initialIdentity.NumberOfLinks -ne 1) {
            throw "Portable promotion lock may not be a hard link."
        }
        if (
            ([uint64]$initialIdentity.FileAttributes -band
                [uint64][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Portable promotion lock handle may not reference a reparse point."
        }
        if ($stream.Length -eq 0) {
            $stream.WriteByte([byte][char]'0')
            $stream.Flush()
        }
        $stream.Position = 0
        $stream.Lock(0, 1)
        $lockedIdentity = Get-StableWindowsFileIdentity -Stream $stream
        if (
            [uint64]$lockedIdentity.NumberOfLinks -ne 1 -or
            [uint64]$lockedIdentity.VolumeSerialNumber -ne
                [uint64]$initialIdentity.VolumeSerialNumber -or
            [uint64]$lockedIdentity.FileIndex -ne [uint64]$initialIdentity.FileIndex
        ) {
            throw "Portable promotion lock identity changed while it was acquired."
        }
    } catch {
        $failure = $_.Exception.Message
        $stream.Dispose()
        throw "Could not safely acquire build/portable-promotion.lock: $failure"
    }
    try {
        Assert-NoReparsePath `
            -PathToCheck $PortablePromotionLockPath `
            -Label "Portable promotion lock"
        $item = Get-Item -LiteralPath $PortablePromotionLockPath -Force
        if ($item.PSIsContainer -or $item.Length -lt 1) {
            throw "Portable promotion lock must remain a non-empty regular file."
        }
    } catch {
        try {
            $stream.Unlock(0, 1)
        } finally {
            $stream.Dispose()
        }
        throw
    }
    return ,$stream
}

function Get-StableWindowsFileIdentity([System.IO.FileStream]$Stream) {
    if (-not ("ProductAtelier.BuildFileIdentity" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace ProductAtelier
{
    public static class BuildFileIdentity
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct FILETIME
        {
            public uint Low;
            public uint High;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BY_HANDLE_FILE_INFORMATION
        {
            public uint FileAttributes;
            public FILETIME CreationTime;
            public FILETIME LastAccessTime;
            public FILETIME LastWriteTime;
            public uint VolumeSerialNumber;
            public uint FileSizeHigh;
            public uint FileSizeLow;
            public uint NumberOfLinks;
            public uint FileIndexHigh;
            public uint FileIndexLow;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out BY_HANDLE_FILE_INFORMATION information
        );

        public static ulong[] Read(SafeFileHandle file)
        {
            BY_HANDLE_FILE_INFORMATION information;
            if (file == null || file.IsInvalid ||
                !GetFileInformationByHandle(file, out information))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            ulong fileIndex = ((ulong)information.FileIndexHigh << 32) |
                information.FileIndexLow;
            return new ulong[] {
                information.VolumeSerialNumber,
                fileIndex,
                information.NumberOfLinks,
                information.FileAttributes
            };
        }
    }
}
'@
    }
    $values = [ProductAtelier.BuildFileIdentity]::Read($Stream.SafeFileHandle)
    return [pscustomobject]@{
        VolumeSerialNumber = [uint64]$values[0]
        FileIndex = [uint64]$values[1]
        NumberOfLinks = [uint64]$values[2]
        FileAttributes = [uint64]$values[3]
    }
}

function Exit-PortablePromotionLock([System.IO.FileStream]$Stream) {
    $failure = ""
    try {
        $Stream.Unlock(0, 1)
    } catch {
        $failure = $_.Exception.Message
    }
    try {
        $Stream.Dispose()
    } catch {
        if ($failure) {
            $failure = "$failure; $($_.Exception.Message)"
        } else {
            $failure = $_.Exception.Message
        }
    }
    if ($failure) {
        throw "Could not release portable promotion lock: $failure"
    }
}

function Invoke-CanonicalCandidateValidation(
    [string]$PackagedSidecarPath = "",
    [string]$ExpectedCandidateIdentitySha256 = ""
) {
    $arguments = @(
        $IsolatedCandidateValidationTool,
        "--portable-release-tool",
        $IsolatedPortableReleaseTool,
        "--project-root",
        $ProjectRoot,
        "--candidate-dir",
        $CanonicalCandidateDir,
        "--expected-git-commit",
        $ExpectedCommit
    )
    if ($ExpectedCandidateIdentitySha256) {
        $arguments += @(
            "--expected-candidate-identity-sha256",
            $ExpectedCandidateIdentitySha256
        )
    }
    if ($PackagedSidecarPath) {
        $arguments += @("--packaging-sidecar", $PackagedSidecarPath)
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& python.exe @arguments 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $rawJson = (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
    if ($exitCode -ne 0) {
        throw "Canonical candidate validation failed: $rawJson"
    }
    try {
        $data = $rawJson | ConvertFrom-Json
    } catch {
        throw "Canonical candidate validation returned invalid JSON: $rawJson"
    }
    return [pscustomobject]@{
        RawJson = $rawJson
        CanonicalFingerprint = [string]$data.canonical_fingerprint
        CandidateIdentitySha256 = [string]$data.candidate_identity.sha256
        Data = $data
    }
}

function Assert-CanonicalCandidateEvidence(
    $Evidence,
    [switch]$RequirePackagingSidecar
) {
    $artifacts = $Evidence.Data.canonical.candidate.artifacts
    $inventory = $Evidence.Data.canonical.candidate.inventory
    foreach ($hash in @(
        [string]$Evidence.CanonicalFingerprint,
        [string]$artifacts.app_sha256,
        [string]$artifacts.sidecar_sha256,
        [string]$artifacts.manifest_sha256,
        [string]$inventory.tree_sha256,
        [string]$Evidence.Data.canonical.candidate_sidecar.tree_sha256,
        [string]$Evidence.CandidateIdentitySha256
    )) {
        if ($hash -notmatch '^[0-9A-F]{64}$') {
            throw "Canonical candidate validation returned an invalid SHA-256: $hash"
        }
    }
    if ([string]$artifacts.git_commit -ne $ExpectedCommit) {
        throw "Canonical candidate evidence is not bound to $ExpectedCommit"
    }
    if (
        [string]$Evidence.Data.candidate_identity.path -cne
        [System.IO.Path]::GetFullPath($CanonicalCandidateIdentityPath)
    ) {
        throw "Canonical candidate identity receipt path is not canonical"
    }
    foreach ($field in @("file_count", "directory_count", "total_bytes")) {
        if ([long]$inventory.$field -le 0) {
            throw "Canonical candidate inventory has an invalid $field"
        }
    }
    if ($RequirePackagingSidecar) {
        foreach ($field in @("tree_sha256", "file_count", "directory_count", "total_bytes")) {
            if (
                $Evidence.Data.canonical.candidate_sidecar.$field -ne
                $Evidence.Data.packaging_sidecar.$field
            ) {
                throw "Isolated packaged sidecar $field does not match the canonical candidate"
            }
        }
    }
}

function Invoke-TauriBundleSourceIdentity([string]$AppPath) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(
            & python.exe `
                $IsolatedBundleIdentityTool `
                --mode source `
                --app $AppPath `
                2>&1
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $rawJson = (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
    if ($exitCode -ne 0) {
        throw "Tauri bundle app identity validation failed: $rawJson"
    }
    try {
        $data = $rawJson | ConvertFrom-Json
    } catch {
        throw "Tauri bundle app identity validation returned invalid JSON: $rawJson"
    }
    if ([string]$data.algorithm_version -cne $TauriBundleIdentityAlgorithm) {
        throw "Unexpected Tauri bundle app identity algorithm."
    }
    foreach ($hash in @(
        [string]$data.source_app_sha256,
        [string]$data.expected_installed_app_sha256
    )) {
        if ($hash -notmatch '^[0-9A-F]{64}$') {
            throw "Tauri bundle app identity returned an invalid SHA-256: $hash"
        }
    }
    if (
        [long]$data.source_app_size_bytes -le 0 -or
        [long]$data.source_app_size_bytes -ne [long]$data.expected_installed_app_size_bytes -or
        [long]$data.marker_offset -lt 0 -or
        [long]$data.changed_byte_count -ne 3 -or
        @($data.changed_byte_offsets).Count -ne 3 -or
        [long]$data.source_marker_counts.unknown -ne 1 -or
        [long]$data.source_marker_counts.nsis -ne 0 -or
        [long]$data.source_marker_counts.msi -ne 0 -or
        [long]$data.expected_installed_marker_counts.unknown -ne 0 -or
        [long]$data.expected_installed_marker_counts.nsis -ne 1 -or
        [long]$data.expected_installed_marker_counts.msi -ne 0
    ) {
        throw "Tauri bundle app identity returned an invalid UNK to NSS marker contract."
    }
    return $data
}

function Invoke-CheckedNative(
    [string]$Command,
    [string[]]$Arguments,
    [string]$FailureMessage
) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)"
    }
}

function Write-JsonExclusive([string]$Path, $Payload) {
    $json = $Payload | ConvertTo-Json -Depth 10
    $encoding = [System.Text.UTF8Encoding]::new($false)
    $stream = [System.IO.FileStream]::new(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        $writer = [System.IO.StreamWriter]::new($stream, $encoding)
        try {
            $writer.Write($json)
            $writer.Write("`r`n")
            $writer.Flush()
            $stream.Flush()
        } finally {
            $writer.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Remove-VerifiedOwnedTree([string]$RootPath, [string]$Label) {
    Assert-RegularDirectory -PathToCheck $RootPath -Label $Label
    $pendingDirectories = New-Object System.Collections.Stack
    $directoriesToDelete = New-Object System.Collections.Stack
    $filesToDelete = New-Object System.Collections.Stack
    $pendingDirectories.Push([System.IO.Path]::GetFullPath($RootPath))

    while ($pendingDirectories.Count -gt 0) {
        $directory = [string]$pendingDirectories.Pop()
        $directoryItem = Microsoft.PowerShell.Management\Get-Item `
            -LiteralPath $directory `
            -Force
        if (
            -not $directoryItem.PSIsContainer -or
            ($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label contains an unsafe directory entry: $directory"
        }
        $directoriesToDelete.Push($directory)
        $children = @(
            Microsoft.PowerShell.Management\Get-ChildItem `
                -LiteralPath $directory `
                -Force
        )
        foreach ($child in $children) {
            if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label contains a reparse point: $($child.FullName)"
            }
            if ($child.PSIsContainer) {
                $pendingDirectories.Push($child.FullName)
            } else {
                $filesToDelete.Push($child.FullName)
            }
        }
    }

    while ($filesToDelete.Count -gt 0) {
        $file = [string]$filesToDelete.Pop()
        $fileItem = Microsoft.PowerShell.Management\Get-Item -LiteralPath $file -Force
        if (
            $fileItem.PSIsContainer -or
            ($fileItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label file changed before cleanup: $file"
        }
        [System.IO.File]::Delete($file)
    }
    while ($directoriesToDelete.Count -gt 0) {
        $directory = [string]$directoriesToDelete.Pop()
        $directoryItem = Microsoft.PowerShell.Management\Get-Item `
            -LiteralPath $directory `
            -Force
        if (
            -not $directoryItem.PSIsContainer -or
            ($directoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "$Label directory changed before cleanup: $directory"
        }
        [System.IO.Directory]::Delete($directory, $false)
    }
}

function Remove-OwnedStaging([string]$PathToRemove) {
    if (-not (Test-Path -LiteralPath $PathToRemove)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($PathToRemove)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $fullPath))
    $leaf = Split-Path -Leaf $fullPath
    $requiredLeaf = ".$ExpectedCommit.staging-$BuildToken"
    if (-not (Test-SamePath $parent $InstallerCandidateRoot) -or $leaf -cne $requiredLeaf) {
        throw "Refusing to clean an unowned staging path: $fullPath"
    }
    Remove-VerifiedOwnedTree -RootPath $fullPath -Label "Owned installer staging"
}

function Remove-OwnedBuildTemp {
    if (-not (Test-Path -LiteralPath $BuildTemp)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($BuildTemp)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $fullPath))
    $leaf = Split-Path -Leaf $fullPath
    if (-not (Test-SamePath $parent $TempBase) -or $leaf -cne $WorktreeLeaf) {
        throw "Refusing to clean an unowned build temp directory: $fullPath"
    }
    Remove-VerifiedOwnedTree -RootPath $fullPath -Label "Owned IC6 build temp"
}

function Remove-OwnedCargoTarget {
    if (-not (Test-Path -LiteralPath $env:CARGO_TARGET_DIR)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($env:CARGO_TARGET_DIR)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $fullPath))
    $leaf = Split-Path -Leaf $fullPath
    if (-not (Test-SamePath $parent $CargoTargetBase) -or $leaf -cne $CargoTargetLeaf) {
        throw "Refusing to clean an unowned Cargo target directory: $fullPath"
    }
    Remove-VerifiedOwnedTree -RootPath $fullPath -Label "Owned IC6 Cargo target"
}

foreach ($command in @("git.exe", "python.exe", "npm.cmd", "npx.cmd")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required IC6 installer build command is unavailable: $command"
    }
}
if (-not [string]::Equals(
    [System.IO.Path]::GetPathRoot($ProjectRoot),
    "D:\",
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "The IC6 installer candidate must run from the D: project checkout."
}
foreach ($isolatedPath in @(
    $IsolatedWorktree,
    $env:CARGO_TARGET_DIR,
    $env:npm_config_cache,
    $env:TEMP,
    $env:TMP
)) {
    if (-not [string]::Equals(
        [System.IO.Path]::GetPathRoot($isolatedPath),
        "D:\",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "IC6 isolated build paths must stay on D: $isolatedPath"
    }
}

$StagingDir = Join-Path $InstallerCandidateRoot ".$ExpectedCommit.staging-$BuildToken"
$portableLockStream = $null
$worktreeCreated = $false
$buildTempCreated = $false
$cargoTargetCreated = $false
$published = $false
$result = $null

Write-Host "=== Product Atelier IC6 Unsigned NSIS Candidate ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Expected commit: $ExpectedCommit"
Write-Host "Detached build token: $BuildToken"
Write-Host "Output: $DestinationDir"

try {
    Initialize-SafeDirectory -PathToCreate $NpmCacheRoot -Label "IC6 npm cache"
    Initialize-SafeDirectory -PathToCreate $TempBase -Label "IC6 temp base"
    if (Test-Path -LiteralPath $BuildTemp) {
        throw "Unique IC6 build temp already exists: $BuildTemp"
    }
    New-Item -ItemType Directory -Path $BuildTemp | Out-Null
    $buildTempCreated = $true
    Assert-RegularDirectory -PathToCheck $BuildTemp -Label "Unique IC6 build temp"

    Assert-SourceState -FetchOrigin
    Assert-RegularDirectory -PathToCheck $BuildRoot -Label "Build root"
    Assert-NoReparsePath -PathToCheck $InstallerCandidateRoot -Label "Installer candidate root"
    if (-not (Test-Path -LiteralPath $InstallerCandidateRoot)) {
        New-Item -ItemType Directory -Path $InstallerCandidateRoot | Out-Null
    }
    Assert-RegularDirectory -PathToCheck $InstallerCandidateRoot -Label "Installer candidate root"
    if (Test-Path -LiteralPath $DestinationDir) {
        throw "Refusing to overwrite an existing installer candidate: $DestinationDir"
    }

    New-IsolatedDetachedWorktree
    $worktreeCreated = $true
    Assert-IsolatedWorktreeState
    foreach ($requiredFile in @(
        $IsolatedPortableReleaseTool,
        $IsolatedCandidateValidationTool,
        $IsolatedBundleIdentityTool,
        $TauriConfigPath,
        $WindowsTauriConfigPath,
        (Join-Path $IsolatedWorktree "package.json"),
        (Join-Path $IsolatedWorktree "package-lock.json")
    )) {
        Assert-RegularFile -PathToCheck $requiredFile -Label "Isolated IC6 input" | Out-Null
    }

    $portableLockStream = Enter-PortablePromotionLock
    Assert-RegularDirectory -PathToCheck $CanonicalCandidateDir -Label "Canonical candidate"
    $candidateEvidence = Invoke-CanonicalCandidateValidation
    Assert-CanonicalCandidateEvidence -Evidence $candidateEvidence
    $reviewedCandidateIdentitySha256 = [string]$candidateEvidence.CandidateIdentitySha256

    $tauriConfig = Get-Content -LiteralPath $TauriConfigPath -Raw | ConvertFrom-Json
    $windowsTauriConfig = Get-Content -LiteralPath $WindowsTauriConfigPath -Raw | ConvertFrom-Json
    $productName = ([string]$tauriConfig.productName).Trim()
    $productVersion = ([string]$tauriConfig.version).Trim()
    $bundleTargets = @($tauriConfig.bundle.targets | ForEach-Object { [string]$_ })
    $sidecarResourceDestination = [string]$windowsTauriConfig.bundle.resources.'bin/python-server/'
    if ($productName -ne "Product Atelier" -or $productVersion -ne "1.0.0") {
        throw "Unexpected isolated Tauri identity; default NSIS output cannot be resolved safely."
    }
    if ($bundleTargets -notcontains "nsis") {
        throw "Isolated Tauri bundle targets must contain nsis."
    }
    if ($sidecarResourceDestination -ne "python-server/") {
        throw "Isolated Tauri resources must package bin/python-server/ as python-server/."
    }

    $canonicalSidecarDir = Join-Path $CanonicalCandidateDir "python-server"
    $canonicalAppPath = Join-Path $CanonicalCandidateDir "Product Atelier.exe"
    $candidateApp = Assert-RegularFile `
        -PathToCheck $canonicalAppPath `
        -Label "Canonical candidate app"
    Assert-RegularDirectory -PathToCheck $canonicalSidecarDir -Label "Canonical candidate sidecar"
    $isolatedBinRoot = Split-Path -Parent $PackagingSidecarDir
    if (Test-Path -LiteralPath $PackagingSidecarDir) {
        throw "Detached IC6 worktree unexpectedly contains a packaged sidecar."
    }
    New-Item -ItemType Directory -Path $isolatedBinRoot | Out-Null
    Copy-Item -LiteralPath $canonicalSidecarDir -Destination $isolatedBinRoot -Recurse
    Assert-RegularDirectory -PathToCheck $PackagingSidecarDir -Label "Isolated packaged sidecar"
    $copiedSidecarEvidence = Invoke-CanonicalCandidateValidation `
        -PackagedSidecarPath $PackagingSidecarDir `
        -ExpectedCandidateIdentitySha256 $reviewedCandidateIdentitySha256
    Assert-CanonicalCandidateEvidence `
        -Evidence $copiedSidecarEvidence `
        -RequirePackagingSidecar
    if ($copiedSidecarEvidence.CanonicalFingerprint -cne $candidateEvidence.CanonicalFingerprint) {
        throw "Canonical candidate changed while its sidecar was copied."
    }
    Assert-IsolatedWorktreeState

    $BuiltApp = Join-Path $env:CARGO_TARGET_DIR "release\product-atelier.exe"
    $rawRebuildAppHash = ""
    $rawRebuildAppSize = 0
    $bundleInputHash = ""
    $bundleInputSize = 0
    $bundleInputIdentity = $null

    Push-Location $IsolatedWorktree
    try {
        Invoke-CheckedNative `
            -Command "npm.cmd" `
            -Arguments @("ci", "--no-audit", "--no-fund", "--ignore-scripts") `
            -FailureMessage "Isolated npm dependency installation failed"

        Initialize-SafeDirectory -PathToCreate $CargoTargetBase -Label "IC6 Cargo target base"
        if (Test-Path -LiteralPath $env:CARGO_TARGET_DIR) {
            throw "Unique IC6 Cargo target already exists: $env:CARGO_TARGET_DIR"
        }
        New-Item -ItemType Directory -Path $env:CARGO_TARGET_DIR | Out-Null
        $cargoTargetCreated = $true
        Assert-RegularDirectory -PathToCheck $env:CARGO_TARGET_DIR -Label "Unique IC6 Cargo target"
        Assert-RegularDirectory -PathToCheck $env:npm_config_cache -Label "IC6 npm cache"
        Assert-RegularDirectory -PathToCheck $env:TEMP -Label "Unique IC6 TEMP"
        Assert-RegularDirectory -PathToCheck $env:TMP -Label "Unique IC6 TMP"

        & npx.cmd --no-install tauri build --no-bundle --features custom-protocol --no-sign
        if ($LASTEXITCODE -ne 0) {
            throw "Unsigned isolated Tauri app build failed (exit code $LASTEXITCODE)"
        }

        $rawRebuildApp = Assert-RegularFile `
            -PathToCheck $BuiltApp `
            -Label "Raw isolated rebuilt Tauri app"
        $rawRebuildAppHash = (Get-FileHash -LiteralPath $BuiltApp -Algorithm SHA256).Hash
        $rawRebuildAppSize = [long]$rawRebuildApp.Length
        Assert-IsolatedWorktreeState
        $preBundleEvidence = Invoke-CanonicalCandidateValidation `
            -PackagedSidecarPath $PackagingSidecarDir `
            -ExpectedCandidateIdentitySha256 $reviewedCandidateIdentitySha256
        Assert-CanonicalCandidateEvidence `
            -Evidence $preBundleEvidence `
            -RequirePackagingSidecar
        if ($preBundleEvidence.CanonicalFingerprint -cne $candidateEvidence.CanonicalFingerprint) {
            throw "Canonical candidate changed before the isolated NSIS bundle."
        }

        $canonicalAppHashBeforeCopy = (Get-FileHash `
            -LiteralPath $canonicalAppPath `
            -Algorithm SHA256).Hash
        if (
            $canonicalAppHashBeforeCopy -cne
            [string]$candidateEvidence.Data.canonical.candidate.artifacts.app_sha256
        ) {
            throw "Canonical candidate app changed before it became the NSIS bundle input."
        }
        Copy-Item -LiteralPath $canonicalAppPath -Destination $BuiltApp -Force
        $bundleInputApp = Assert-RegularFile `
            -PathToCheck $BuiltApp `
            -Label "Canonical NSIS bundle input app"
        $canonicalAppHashAfterCopy = (Get-FileHash `
            -LiteralPath $canonicalAppPath `
            -Algorithm SHA256).Hash
        $bundleInputHash = (Get-FileHash -LiteralPath $BuiltApp -Algorithm SHA256).Hash
        $bundleInputSize = [long]$bundleInputApp.Length
        if (
            $canonicalAppHashBeforeCopy -cne $canonicalAppHashAfterCopy -or
            $canonicalAppHashBeforeCopy -cne $bundleInputHash -or
            [long]$candidateApp.Length -ne $bundleInputSize
        ) {
            throw "Canonical candidate app changed or its NSIS bundle input copy is not exact."
        }
        $bundleInputIdentity = Invoke-TauriBundleSourceIdentity -AppPath $BuiltApp
        if (
            [string]$bundleInputIdentity.source_app_sha256 -cne $bundleInputHash -or
            [long]$bundleInputIdentity.source_app_size_bytes -ne $bundleInputSize
        ) {
            throw "Tauri bundle identity is not bound to the exact canonical bundle input."
        }
        if ((Get-FileHash -LiteralPath $BuiltApp -Algorithm SHA256).Hash -cne $bundleInputHash) {
            throw "Canonical NSIS bundle input changed after identity planning."
        }

        & npx.cmd --no-install tauri bundle --bundles nsis --features custom-protocol --no-sign
        if ($LASTEXITCODE -ne 0) {
            throw "Unsigned isolated Tauri NSIS bundle failed (exit code $LASTEXITCODE)"
        }
    } finally {
        Pop-Location
    }

    Assert-SourceState
    Assert-IsolatedWorktreeState
    $prePublishEvidence = Invoke-CanonicalCandidateValidation `
        -PackagedSidecarPath $PackagingSidecarDir `
        -ExpectedCandidateIdentitySha256 $reviewedCandidateIdentitySha256
    Assert-CanonicalCandidateEvidence `
        -Evidence $prePublishEvidence `
        -RequirePackagingSidecar
    if ($prePublishEvidence.CanonicalFingerprint -cne $candidateEvidence.CanonicalFingerprint) {
        throw "Canonical candidate changed during the isolated NSIS build."
    }

    $defaultNsisName = "${productName}_${productVersion}_x64-setup.exe"
    $DefaultNsisOutput = Join-Path `
        $env:CARGO_TARGET_DIR `
        "release\bundle\nsis\$defaultNsisName"
    $builtAppItem = Assert-RegularFile -PathToCheck $BuiltApp -Label "Isolated built Tauri app"
    $installerItem = Assert-RegularFile -PathToCheck $DefaultNsisOutput -Label "Isolated NSIS output"
    $postBundleRestoredAppHash = (Get-FileHash -LiteralPath $BuiltApp -Algorithm SHA256).Hash
    if (
        $postBundleRestoredAppHash -cne $bundleInputHash -or
        $postBundleRestoredAppHash -cne
            [string]$candidateEvidence.Data.canonical.candidate.artifacts.app_sha256 -or
        [long]$builtAppItem.Length -ne $bundleInputSize
    ) {
        throw "Tauri bundler did not restore the canonical bundle input app byte-for-byte."
    }
    $postBundleIdentity = Invoke-TauriBundleSourceIdentity -AppPath $BuiltApp
    foreach ($field in @(
        "algorithm_version",
        "source_app_sha256",
        "source_app_size_bytes",
        "expected_installed_app_sha256",
        "expected_installed_app_size_bytes",
        "marker_offset",
        "changed_byte_count"
    )) {
        if ($postBundleIdentity.$field -cne $bundleInputIdentity.$field) {
            throw "Tauri bundle input identity changed after restoration: $field"
        }
    }
    $sourceSignature = Get-AuthenticodeSignature -LiteralPath $DefaultNsisOutput
    if ([string]$sourceSignature.Status -ne "NotSigned") {
        throw "IC6 requires an unsigned NSIS candidate; status was $($sourceSignature.Status)."
    }

    if (Test-Path -LiteralPath $StagingDir) {
        throw "Exclusive staging path already exists: $StagingDir"
    }
    if (Test-Path -LiteralPath $DestinationDir) {
        throw "Refusing to overwrite an installer candidate created concurrently: $DestinationDir"
    }
    New-Item -ItemType Directory -Path $StagingDir | Out-Null
    Assert-RegularDirectory -PathToCheck $StagingDir -Label "Exclusive installer staging"
    $StagedInstaller = Join-Path $StagingDir $defaultNsisName
    $sourceHashBeforeCopy = (Get-FileHash -LiteralPath $DefaultNsisOutput -Algorithm SHA256).Hash
    Copy-Item -LiteralPath $DefaultNsisOutput -Destination $StagedInstaller
    $stagedItem = Assert-RegularFile -PathToCheck $StagedInstaller -Label "Staged NSIS candidate"
    $sourceHashAfterCopy = (Get-FileHash -LiteralPath $DefaultNsisOutput -Algorithm SHA256).Hash
    $stagedHash = (Get-FileHash -LiteralPath $StagedInstaller -Algorithm SHA256).Hash
    if (
        $sourceHashBeforeCopy -cne $sourceHashAfterCopy -or
        $sourceHashBeforeCopy -cne $stagedHash -or
        [long]$installerItem.Length -ne [long]$stagedItem.Length
    ) {
        throw "NSIS candidate changed or its staged copy failed hash/size binding."
    }
    $stagedSignature = Get-AuthenticodeSignature -LiteralPath $StagedInstaller
    if ([string]$stagedSignature.Status -ne "NotSigned") {
        throw "Staged IC6 NSIS candidate is not unsigned."
    }

    $candidateSidecar = Assert-RegularFile `
        -PathToCheck (Join-Path $CanonicalCandidateDir "python-server\python-server.exe") `
        -Label "Canonical candidate sidecar"
    $manifest = [ordered]@{
        schema_version = 4
        kind = "product-atelier-unsigned-nsis-candidate"
        created_at_utc = [DateTime]::UtcNow.ToString("o")
        git_commit = $ExpectedCommit
        source = [ordered]@{
            branch = $RequiredBranch
            upstream = $RequiredUpstream
            build_mode = "detached-worktree-canonical-app-bundle"
            build_token = $BuildToken
            detached_head = $ExpectedCommit
            cargo_target_key = $CargoTargetLeaf
        }
        canonical_candidate = [ordered]@{
            relative_path = "build/portable-candidate-current"
            identity_receipt_relative_path = "build/portable-candidate-current.identity.json"
            identity_receipt_sha256 = $reviewedCandidateIdentitySha256
            identity_receipt_format_version = [long]$candidateEvidence.Data.candidate_identity.receipt.format_version
            identity_receipt_kind = [string]$candidateEvidence.Data.candidate_identity.receipt.kind
            app_sha256 = [string]$candidateEvidence.Data.canonical.candidate.artifacts.app_sha256
            app_size_bytes = [long]$candidateApp.Length
            sidecar_sha256 = [string]$candidateEvidence.Data.canonical.candidate.artifacts.sidecar_sha256
            sidecar_size_bytes = [long]$candidateSidecar.Length
            sidecar_manifest_sha256 = [string]$candidateEvidence.Data.canonical.candidate.artifacts.manifest_sha256
            tree_sha256 = [string]$candidateEvidence.Data.canonical.candidate.inventory.tree_sha256
            tree_file_count = [long]$candidateEvidence.Data.canonical.candidate.inventory.file_count
            tree_directory_count = [long]$candidateEvidence.Data.canonical.candidate.inventory.directory_count
            tree_total_bytes = [long]$candidateEvidence.Data.canonical.candidate.inventory.total_bytes
            sidecar_tree_sha256 = [string]$candidateEvidence.Data.canonical.candidate_sidecar.tree_sha256
            sidecar_tree_file_count = [long]$candidateEvidence.Data.canonical.candidate_sidecar.file_count
            sidecar_tree_directory_count = [long]$candidateEvidence.Data.canonical.candidate_sidecar.directory_count
            sidecar_tree_total_bytes = [long]$candidateEvidence.Data.canonical.candidate_sidecar.total_bytes
        }
        installer = [ordered]@{
            filename = $defaultNsisName
            sha256 = $stagedHash
            size_bytes = [long]$stagedItem.Length
            authenticode_status = "NotSigned"
            bundle_identity_algorithm = [string]$bundleInputIdentity.algorithm_version
            raw_rebuild_app_sha256 = $rawRebuildAppHash
            raw_rebuild_app_size_bytes = [long]$rawRebuildAppSize
            portable_app_sha256 = [string]$candidateEvidence.Data.canonical.candidate.artifacts.app_sha256
            portable_app_size_bytes = [long]$candidateApp.Length
            bundle_input_app_sha256 = $bundleInputHash
            bundle_input_app_size_bytes = [long]$bundleInputSize
            expected_installed_app_sha256 = [string]$bundleInputIdentity.expected_installed_app_sha256
            expected_installed_app_size_bytes = [long]$bundleInputIdentity.expected_installed_app_size_bytes
            post_bundle_restored_app_sha256 = $postBundleRestoredAppHash
            post_bundle_restored_app_size_bytes = [long]$builtAppItem.Length
            bundle_type_marker_offset = [long]$bundleInputIdentity.marker_offset
            bundle_type_marker_source = [string]$bundleInputIdentity.source_marker
            bundle_type_marker_installed = [string]$bundleInputIdentity.installed_marker
            bundle_type_changed_byte_count = [long]$bundleInputIdentity.changed_byte_count
            bundle_type_changed_byte_offsets = @($bundleInputIdentity.changed_byte_offsets)
            bundles = @("nsis")
            features = @("custom-protocol")
        }
    }
    $ManifestName = "installer-candidate-manifest.json"
    $StagedManifest = Join-Path $StagingDir $ManifestName
    Write-JsonExclusive -Path $StagedManifest -Payload $manifest
    Assert-RegularFile -PathToCheck $StagedManifest -Label "Installer candidate manifest" | Out-Null
    $stagedManifestHash = (Get-FileHash -LiteralPath $StagedManifest -Algorithm SHA256).Hash
    if ((Get-FileHash -LiteralPath $StagedInstaller -Algorithm SHA256).Hash -cne $stagedHash) {
        throw "Staged NSIS candidate changed before publication."
    }
    if ((Get-FileHash -LiteralPath $StagedManifest -Algorithm SHA256).Hash -cne $stagedManifestHash) {
        throw "Staged manifest changed before publication."
    }
    if ((Get-FileHash -LiteralPath $DefaultNsisOutput -Algorithm SHA256).Hash -cne $stagedHash) {
        throw "Isolated NSIS output changed before publication."
    }
    if (Test-Path -LiteralPath $DestinationDir) {
        throw "Refusing to overwrite an installer candidate created concurrently: $DestinationDir"
    }

    $FinalInstaller = Join-Path $DestinationDir $defaultNsisName
    $FinalManifest = Join-Path $DestinationDir $ManifestName
    $result = [pscustomobject]@{
        Directory = $DestinationDir
        Installer = $FinalInstaller
        Manifest = $FinalManifest
        Bytes = [long]$stagedItem.Length
        Sha256 = $stagedHash
        ManifestSha256 = $stagedManifestHash
        ExpectedInstalledAppSha256 = [string]$bundleInputIdentity.expected_installed_app_sha256
        GitCommit = $ExpectedCommit
        CandidateIdentitySha256 = $reviewedCandidateIdentitySha256
    }
    [System.IO.Directory]::Move($StagingDir, $DestinationDir)
    $StagingDir = ""
    $published = $true
} finally {
    if ($StagingDir -and (Test-Path -LiteralPath $StagingDir)) {
        try {
            Remove-OwnedStaging -PathToRemove $StagingDir
        } catch {
            Write-Warning "Could not clean owned installer staging: $($_.Exception.Message)"
        }
    }
    if ($null -ne $portableLockStream) {
        try {
            Exit-PortablePromotionLock -Stream $portableLockStream
        } catch {
            Write-Warning $_.Exception.Message
        }
    }
    if ($worktreeCreated -or (Test-Path -LiteralPath $IsolatedWorktree)) {
        try {
            Remove-IsolatedDetachedWorktree
        } catch {
            Write-Warning "Could not clean the unique detached worktree: $($_.Exception.Message)"
        }
    }
    if ($cargoTargetCreated) {
        try {
            Remove-OwnedCargoTarget
        } catch {
            Write-Warning "Could not clean the unique D: Cargo target: $($_.Exception.Message)"
        }
    }
    if ($buildTempCreated -or (Test-Path -LiteralPath $BuildTemp)) {
        try {
            Remove-OwnedBuildTemp
        } catch {
            Write-Warning "Could not clean the unique D: build temp: $($_.Exception.Message)"
        }
    }
}

if ($published) {
    Write-Host "Unsigned IC6 NSIS candidate published without execution." -ForegroundColor Green
    Write-Host "Directory: $($result.Directory)"
    Write-Host "Installer: $($result.Installer)"
    Write-Host "Manifest: $($result.Manifest)"
    Write-Host "Bytes: $($result.Bytes)"
    Write-Host "SHA-256: $($result.Sha256)"
    Write-Host "Manifest SHA-256: $($result.ManifestSha256)"
    Write-Host "Expected installed App SHA-256: $($result.ExpectedInstalledAppSha256)"
    Write-Host "Git commit: $($result.GitCommit)"
    Write-Host "Candidate identity SHA-256: $($result.CandidateIdentitySha256)"
}
