# Product Atelier IC6 candidate-only build entry point.
# This script builds the canonical isolated candidate and never promotes it.

param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-fA-F]{40}$")]
    [string]$ExpectedCommit
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "The IC6 candidate build entry point requires Windows."
}
if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "Windows PowerShell 5 or newer is required."
}

$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ExpectedCommit = $ExpectedCommit.ToLowerInvariant()
$RequiredBranch = "codex/excalidraw-infinite-canvas"
$RequiredUpstream = "origin/$RequiredBranch"
$CandidateDir = Join-Path $ProjectRoot "build\portable-candidate-current"
$TransactionPath = Join-Path $ProjectRoot "build\portable-promotion-transaction.json"
$WorktreeBase = "D:\pa6-w"
$CargoTargetBase = "D:\rust-target\ic6-candidate"
$NpmCacheRoot = "D:\ProductAtelier-Cache\npm"
$ProcessTempBase = "D:\ProductAtelier-Temp\ic6-process-temp"
$BuildToken = [guid]::NewGuid().ToString("N")
$BuildIdentity = "$($ExpectedCommit.Substring(0, 12))-$BuildToken"
$IsolatedProjectRoot = Join-Path $WorktreeBase $BuildIdentity
$ProcessTempRoot = Join-Path $ProcessTempBase $BuildIdentity
$MaxLegacyCopyRootLength = 58
if ($IsolatedProjectRoot.Length -gt $MaxLegacyCopyRootLength) {
    throw "IC6 worktree root exceeds the Windows PowerShell copy budget: $IsolatedProjectRoot"
}
$env:PATH = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:USERPROFILE\.cargo\bin;C:\mingw64\bin;C:\msys64\mingw64\bin;$env:PATH"
$env:CARGO_TARGET_DIR = Join-Path $CargoTargetBase $BuildIdentity
$env:npm_config_cache = $NpmCacheRoot
$env:TEMP = $ProcessTempRoot
$env:TMP = $ProcessTempRoot
$SourceExe = Join-Path $env:CARGO_TARGET_DIR "release\product-atelier.exe"
$SourceExePeer = Join-Path $env:CARGO_TARGET_DIR "release\deps\product_atelier.exe"
$DetachedApp = Join-Path $ProcessTempRoot "product-atelier-stage-source.exe"
$SourceSidecar = Join-Path $IsolatedProjectRoot "src-tauri\bin\python-server"
$IsolatedPromotionTool = Join-Path $IsolatedProjectRoot "tools\portable_release.py"
$IsolatedSidecarBuild = Join-Path $IsolatedProjectRoot "tools\Build-Sidecar.ps1"
$WorktreeAdded = $false
$DetachedAppLock = $null

function Invoke-GitCaptureAt([string]$Repository, [string[]]$Arguments) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git.exe -C $Repository @Arguments 2>&1)
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
    return Invoke-GitCaptureAt -Repository $ProjectRoot -Arguments $Arguments
}

function Assert-EmptyGitDiffAt(
    [string]$Repository,
    [string[]]$QuietArguments,
    [string[]]$ReportArguments,
    [string]$Label
) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& git.exe -C $Repository @QuietArguments 2>&1)
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
        -Repository $Repository `
        -Arguments $ReportArguments
    if ([string]::IsNullOrWhiteSpace($changes)) {
        $changes = "(Git reported a content difference without naming a path)"
    }
    throw "$Label contains source changes:`n$changes"
}

function Assert-NoTrackedContentChangesAt([string]$Repository, [string]$Label) {
    Assert-EmptyGitDiffAt `
        -Repository $Repository `
        -QuietArguments @("diff", "--cached", "--quiet", "--no-ext-diff", "--ita-visible-in-index", "--ignore-submodules=none", "HEAD", "--") `
        -ReportArguments @("diff", "--cached", "--name-status", "--no-ext-diff", "--ita-visible-in-index", "--ignore-submodules=none", "HEAD", "--") `
        -Label "$Label index"
    Assert-EmptyGitDiffAt `
        -Repository $Repository `
        -QuietArguments @("diff", "--quiet", "--no-ext-diff", "--ignore-submodules=none", "--") `
        -ReportArguments @("diff", "--name-status", "--no-ext-diff", "--ignore-submodules=none", "--") `
        -Label "$Label worktree"
}

function Assert-NoHiddenTrackedEntriesAt([string]$Repository) {
    $entries = Invoke-GitCaptureAt `
        -Repository $Repository `
        -Arguments @("ls-files", "-v")
    $hiddenEntries = @(
        $entries -split "`n" | Where-Object { $_ -cmatch "^(?:[a-z]|S) " }
    )
    if ($hiddenEntries.Count -gt 0) {
        $summary = ($hiddenEntries | Select-Object -First 10) -join "`n"
        throw "IC6 source index contains hidden tracked entries:`n$summary"
    }
}

function Assert-NoGitlinksAt([string]$Repository) {
    $entries = Invoke-GitCaptureAt `
        -Repository $Repository `
        -Arguments @("ls-files", "--stage")
    $gitlinks = @(
        $entries -split "`n" | Where-Object { $_ -cmatch "^160000 " }
    )
    if ($gitlinks.Count -gt 0) {
        $summary = ($gitlinks | Select-Object -First 10) -join "`n"
        throw "IC6 source may not contain Git submodules:`n$summary"
    }
}

function Assert-NoUntrackedEntriesAt([string]$Repository, [string]$Label) {
    $entries = Invoke-GitCaptureAt `
        -Repository $Repository `
        -Arguments @("ls-files", "--others", "--exclude-standard")
    if (-not [string]::IsNullOrWhiteSpace($entries)) {
        throw "$Label has untracked, unignored files:`n$entries"
    }
}

function Assert-CleanSourceAt([string]$Repository, [string]$Label) {
    Assert-NoGitlinksAt -Repository $Repository
    Assert-NoHiddenTrackedEntriesAt -Repository $Repository
    Assert-NoTrackedContentChangesAt -Repository $Repository -Label $Label
    Assert-NoUntrackedEntriesAt -Repository $Repository -Label $Label
    [void](Invoke-GitCaptureAt `
        -Repository $Repository `
        -Arguments @("diff", "--check", "HEAD", "--"))
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
        if (-not $parent -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
}

function Initialize-CandidateBuildFileIdentity {
    if (-not ("ProductAtelier.CandidateBuildFileIdentity" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace ProductAtelier
{
    public static class CandidateBuildFileIdentity
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

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        private const uint GENERIC_READ = 0x80000000;
        private const uint GENERIC_WRITE = 0x40000000;
        private const uint FILE_SHARE_READ = 0x00000001;
        private const uint CREATE_NEW = 1;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_ATTRIBUTE_NORMAL = 0x00000080;
        private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;

        private static FileStream Open(
            string path,
            uint desiredAccess,
            uint creationDisposition,
            FileAccess access
        )
        {
            SafeFileHandle handle = CreateFileW(
                path,
                desiredAccess,
                FILE_SHARE_READ,
                IntPtr.Zero,
                creationDisposition,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                IntPtr.Zero
            );
            if (handle == null || handle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                if (handle != null) handle.Dispose();
                throw new Win32Exception(error);
            }
            try
            {
                return new FileStream(handle, access);
            }
            catch
            {
                handle.Dispose();
                throw;
            }
        }

        public static FileStream OpenReadNoFollow(string path)
        {
            return Open(path, GENERIC_READ, OPEN_EXISTING, FileAccess.Read);
        }

        public static FileStream CreateReadWriteNoFollow(string path)
        {
            return Open(
                path,
                GENERIC_READ | GENERIC_WRITE,
                CREATE_NEW,
                FileAccess.ReadWrite
            );
        }

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
            ulong fileSize = ((ulong)information.FileSizeHigh << 32) |
                information.FileSizeLow;
            return new ulong[] {
                information.VolumeSerialNumber,
                fileIndex,
                information.NumberOfLinks,
                information.FileAttributes,
                fileSize
            };
        }
    }
}
'@
    }
}

function Get-StableWindowsFileIdentity([System.IO.FileStream]$Stream) {
    Initialize-CandidateBuildFileIdentity
    $values = [ProductAtelier.CandidateBuildFileIdentity]::Read($Stream.SafeFileHandle)
    return [pscustomobject]@{
        VolumeSerialNumber = [uint64]$values[0]
        FileIndex = [uint64]$values[1]
        NumberOfLinks = [uint64]$values[2]
        FileAttributes = [uint64]$values[3]
        FileSize = [uint64]$values[4]
    }
}

function Test-SameFileIdentity($Left, $Right) {
    return (
        [uint64]$Left.VolumeSerialNumber -eq [uint64]$Right.VolumeSerialNumber -and
        [uint64]$Left.FileIndex -eq [uint64]$Right.FileIndex
    )
}

function Assert-RegularArtifactHandle($Identity, [string]$Label) {
    $attributes = [uint64]$Identity.FileAttributes
    if (
        ($attributes -band [uint64][System.IO.FileAttributes]::Directory) -ne 0 -or
        ($attributes -band [uint64][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Label must remain a regular non-reparse file."
    }
}

function Get-StreamSha256([System.IO.FileStream]$Stream) {
    $originalPosition = $Stream.Position
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        $digest = $sha256.ComputeHash($Stream)
        return ([System.BitConverter]::ToString($digest)).Replace("-", "")
    } finally {
        $sha256.Dispose()
        $Stream.Position = $originalPosition
    }
}

function New-DetachedCargoArtifact(
    [string]$SourcePath,
    [string]$ExpectedPeerPath,
    [string]$CargoRoot,
    [string]$DestinationPath,
    [string]$DestinationRoot
) {
    Initialize-CandidateBuildFileIdentity
    $fullCargoRoot = [System.IO.Path]::GetFullPath($CargoRoot)
    $fullSource = [System.IO.Path]::GetFullPath($SourcePath)
    $fullPeer = [System.IO.Path]::GetFullPath($ExpectedPeerPath)
    $fullDestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot)
    $fullDestination = [System.IO.Path]::GetFullPath($DestinationPath)
    $requiredSource = [System.IO.Path]::GetFullPath(
        (Join-Path $fullCargoRoot "release\product-atelier.exe")
    )
    $requiredPeer = [System.IO.Path]::GetFullPath(
        (Join-Path $fullCargoRoot "release\deps\product_atelier.exe")
    )
    $requiredDestination = [System.IO.Path]::GetFullPath(
        (Join-Path $fullDestinationRoot "product-atelier-stage-source.exe")
    )
    foreach ($pathPair in @(
        @($fullSource, $requiredSource, "Cargo application source"),
        @($fullPeer, $requiredPeer, "Cargo application peer"),
        @($fullDestination, $requiredDestination, "detached application target")
    )) {
        if (-not [string]::Equals(
            [string]$pathPair[0],
            [string]$pathPair[1],
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "$($pathPair[2]) is outside its exact build-owned path: $($pathPair[0])"
        }
    }
    Assert-NoReparsePath -PathToCheck $fullCargoRoot -Label "IC6 Cargo artifact root"
    Assert-NoReparsePath -PathToCheck $fullSource -Label "Built Tauri executable"
    Assert-NoReparsePath -PathToCheck $fullDestinationRoot -Label "IC6 process temp"
    Assert-NoReparsePath -PathToCheck $fullDestination -Label "Detached Tauri executable"
    if (Test-Path -LiteralPath $fullDestination) {
        throw "Detached Tauri executable already exists: $fullDestination"
    }

    $sourceStream = $null
    $peerStream = $null
    $destinationStream = $null
    $destinationReadLock = $null
    try {
        $sourceStream = [ProductAtelier.CandidateBuildFileIdentity]::OpenReadNoFollow(
            $fullSource
        )
        Assert-NoReparsePath -PathToCheck $fullSource -Label "Built Tauri executable"
        $sourceBefore = Get-StableWindowsFileIdentity -Stream $sourceStream
        Assert-RegularArtifactHandle -Identity $sourceBefore -Label "Built Tauri executable"
        if ([uint64]$sourceBefore.NumberOfLinks -notin @(1, 2)) {
            throw "Built Tauri executable must have one link or Cargo's exact two links."
        }

        if ([uint64]$sourceBefore.NumberOfLinks -eq 2) {
            Assert-NoReparsePath -PathToCheck $fullPeer -Label "Built Tauri executable peer"
            $peerStream = [ProductAtelier.CandidateBuildFileIdentity]::OpenReadNoFollow(
                $fullPeer
            )
            Assert-NoReparsePath -PathToCheck $fullPeer -Label "Built Tauri executable peer"
            $peerBefore = Get-StableWindowsFileIdentity -Stream $peerStream
            Assert-RegularArtifactHandle -Identity $peerBefore -Label "Built Tauri executable peer"
            if (
                [uint64]$peerBefore.NumberOfLinks -ne 2 -or
                -not (Test-SameFileIdentity -Left $sourceBefore -Right $peerBefore)
            ) {
                throw "Built Tauri executable hard links do not match Cargo's exact peer."
            }
        }

        $sourceHashBefore = Get-StreamSha256 -Stream $sourceStream
        $sourceStream.Position = 0
        $destinationStream = [ProductAtelier.CandidateBuildFileIdentity]::CreateReadWriteNoFollow(
            $fullDestination
        )
        $destinationBefore = Get-StableWindowsFileIdentity -Stream $destinationStream
        Assert-RegularArtifactHandle -Identity $destinationBefore -Label "Detached Tauri executable"
        if ([uint64]$destinationBefore.NumberOfLinks -ne 1) {
            throw "Detached Tauri executable must start with exactly one hard link."
        }

        $sourceStream.CopyTo($destinationStream)
        $destinationStream.Flush($true)
        if ([uint64]$destinationStream.Length -ne [uint64]$sourceBefore.FileSize) {
            throw "Detached Tauri executable size does not match the locked Cargo source."
        }
        $destinationHash = Get-StreamSha256 -Stream $destinationStream
        $sourceAfter = Get-StableWindowsFileIdentity -Stream $sourceStream
        $sourceHashAfter = Get-StreamSha256 -Stream $sourceStream
        if (
            -not (Test-SameFileIdentity -Left $sourceBefore -Right $sourceAfter) -or
            [uint64]$sourceBefore.NumberOfLinks -ne [uint64]$sourceAfter.NumberOfLinks -or
            [uint64]$sourceBefore.FileSize -ne [uint64]$sourceAfter.FileSize -or
            $sourceHashBefore -cne $sourceHashAfter
        ) {
            throw "Built Tauri executable changed while its detached copy was created."
        }
        if ($peerStream) {
            $peerAfter = Get-StableWindowsFileIdentity -Stream $peerStream
            if (
                -not (Test-SameFileIdentity -Left $sourceAfter -Right $peerAfter) -or
                [uint64]$peerAfter.NumberOfLinks -ne 2
            ) {
                throw "Built Tauri executable peer changed while its detached copy was created."
            }
        }
        $destinationAfter = Get-StableWindowsFileIdentity -Stream $destinationStream
        if (
            -not (Test-SameFileIdentity -Left $destinationBefore -Right $destinationAfter) -or
            [uint64]$destinationAfter.NumberOfLinks -ne 1 -or
            [uint64]$destinationAfter.FileSize -ne [uint64]$sourceBefore.FileSize -or
            $destinationHash -cne $sourceHashBefore
        ) {
            throw "Detached Tauri executable identity or content is invalid."
        }
        Assert-NoReparsePath -PathToCheck $fullDestination -Label "Detached Tauri executable"

        $destinationStream.Dispose()
        $destinationStream = $null
        $destinationReadLock = [ProductAtelier.CandidateBuildFileIdentity]::OpenReadNoFollow(
            $fullDestination
        )
        $lockedDestination = Get-StableWindowsFileIdentity -Stream $destinationReadLock
        $lockedDestinationHash = Get-StreamSha256 -Stream $destinationReadLock
        if (
            -not (Test-SameFileIdentity -Left $destinationAfter -Right $lockedDestination) -or
            [uint64]$lockedDestination.NumberOfLinks -ne 1 -or
            [uint64]$lockedDestination.FileSize -ne [uint64]$sourceBefore.FileSize -or
            $lockedDestinationHash -cne $sourceHashBefore
        ) {
            throw "Detached Tauri executable changed while its read lock was acquired."
        }

        $result = [pscustomobject]@{
            Path = $fullDestination
            Sha256 = $lockedDestinationHash
            Length = [uint64]$lockedDestination.FileSize
            Lock = $destinationReadLock
        }
        $destinationReadLock = $null
        return $result
    } finally {
        if ($destinationReadLock) { $destinationReadLock.Dispose() }
        if ($destinationStream) { $destinationStream.Dispose() }
        if ($peerStream) { $peerStream.Dispose() }
        if ($sourceStream) { $sourceStream.Dispose() }
    }
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
        foreach ($line in $lastFetchOutput) { Write-Host $line }
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

function Assert-SourceState([string]$Expected, [switch]$FetchOrigin) {
    Assert-CleanSourceAt -Repository $ProjectRoot -Label "IC6 candidate source"

    $branch = Invoke-GitCapture -Arguments @("branch", "--show-current")
    if ($branch -ne $RequiredBranch) {
        throw "IC6 candidate builds require branch $RequiredBranch; current branch is $branch"
    }
    $upstream = Invoke-GitCapture -Arguments @(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", '@{upstream}'
    )
    if ($upstream -ne $RequiredUpstream) {
        throw "IC6 candidate builds require upstream $RequiredUpstream; current upstream is $upstream"
    }

    if ($FetchOrigin) {
        Update-OriginTrackingRef
    }

    $head = Invoke-GitCapture -Arguments @("rev-parse", "--verify", "HEAD")
    if (-not [string]::Equals($head, $Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Current HEAD $head does not match -ExpectedCommit $Expected"
    }
    $upstreamHead = Invoke-GitCapture -Arguments @(
        "rev-parse", "--verify", $RequiredUpstream
    )
    if (-not [string]::Equals(
        $upstreamHead,
        $Expected,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Upstream HEAD $upstreamHead does not match -ExpectedCommit $Expected"
    }

    Assert-CleanSourceAt -Repository $ProjectRoot -Label "IC6 candidate source"
}

function Assert-IsolatedSourceState([string]$Expected) {
    $head = Invoke-GitCaptureAt `
        -Repository $IsolatedProjectRoot `
        -Arguments @("rev-parse", "--verify", "HEAD")
    if (-not [string]::Equals(
        $head,
        $Expected,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Isolated build HEAD $head does not match $Expected"
    }
    $branch = Invoke-GitCaptureAt `
        -Repository $IsolatedProjectRoot `
        -Arguments @("branch", "--show-current")
    if (-not [string]::IsNullOrWhiteSpace($branch)) {
        throw "IC6 isolated build source must remain detached; branch was $branch"
    }
    Assert-CleanSourceAt `
        -Repository $IsolatedProjectRoot `
        -Label "IC6 isolated source"
}

function Remove-IsolatedBuildWorktree {
    if (-not $WorktreeAdded) {
        return
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(
            & git.exe -C $ProjectRoot worktree remove --force $IsolatedProjectRoot 2>&1
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        Write-Warning (
            "Could not remove this run's isolated build worktree; " +
            "it was not reused and remains at $IsolatedProjectRoot. " +
            ($output -join " ")
        )
        return
    }
    $script:WorktreeAdded = $false
}

function Remove-OwnedDirectory(
    [string]$PathToRemove,
    [string]$AllowedParent,
    [string]$ExpectedLeaf,
    [string]$Label
) {
    if (-not (Test-Path -LiteralPath $PathToRemove)) {
        return
    }
    $fullPath = [System.IO.Path]::GetFullPath($PathToRemove)
    $fullParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $fullPath))
    $allowed = [System.IO.Path]::GetFullPath($AllowedParent)
    $leaf = Split-Path -Leaf $fullPath
    if ($fullParent -ne $allowed -or $leaf -cne $ExpectedLeaf) {
        throw "Refusing to clean an unowned $Label directory: $fullPath"
    }
    Assert-NoReparsePath -PathToCheck $fullPath -Label $Label
    $pending = [System.Collections.Generic.Stack[System.IO.DirectoryInfo]]::new()
    $pending.Push((Get-Item -LiteralPath $fullPath -Force))
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($entry in $directory.GetFileSystemInfos()) {
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to clean $Label containing a reparse point: $($entry.FullName)"
            }
            if (($entry.Attributes -band [System.IO.FileAttributes]::Directory) -ne 0) {
                $pending.Push([System.IO.DirectoryInfo]$entry)
            }
        }
    }
    [System.IO.Directory]::Delete($fullPath, $true)
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

function Write-Step([string]$Step, [string]$Message) {
    Write-Host "[$Step] $Message" -ForegroundColor Yellow
}

foreach ($command in @("git.exe", "python.exe", "npm.cmd", "npx.cmd", "cargo.exe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required IC6 build command is unavailable: $command"
    }
}
foreach ($requiredFile in @(
    (Join-Path $PSScriptRoot "portable_release.py"),
    (Join-Path $PSScriptRoot "Build-Sidecar.ps1")
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required IC6 build helper is missing: $requiredFile"
    }
}
if (Test-Path -LiteralPath $TransactionPath) {
    throw "An unfinished portable promotion exists: $TransactionPath"
}
$targetDrive = [System.IO.Path]::GetPathRoot($env:CARGO_TARGET_DIR)
if ($targetDrive -ne "D:\" -or -not (Test-Path -LiteralPath $targetDrive -PathType Container)) {
    throw "IC6 requires an available D: drive CARGO_TARGET_DIR; configured path is $env:CARGO_TARGET_DIR"
}

Write-Host "=== Product Atelier IC6 Candidate-Only Build ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Expected commit: $ExpectedCommit"
Write-Host "Candidate: $CandidateDir"

Assert-SourceState -Expected $ExpectedCommit -FetchOrigin
Assert-NoReparsePath -PathToCheck $WorktreeBase -Label "IC6 worktree base"
Assert-NoReparsePath -PathToCheck $CargoTargetBase -Label "IC6 Cargo target base"
Assert-NoReparsePath -PathToCheck $NpmCacheRoot -Label "IC6 npm cache"
Assert-NoReparsePath -PathToCheck $ProcessTempBase -Label "IC6 process temp base"
New-Item -ItemType Directory -Path $WorktreeBase -Force | Out-Null
New-Item -ItemType Directory -Path $CargoTargetBase -Force | Out-Null
New-Item -ItemType Directory -Path $NpmCacheRoot -Force | Out-Null
New-Item -ItemType Directory -Path $ProcessTempRoot -Force | Out-Null
Assert-NoReparsePath -PathToCheck $WorktreeBase -Label "IC6 worktree base"
Assert-NoReparsePath -PathToCheck $CargoTargetBase -Label "IC6 Cargo target base"
Assert-NoReparsePath -PathToCheck $NpmCacheRoot -Label "IC6 npm cache"
Assert-NoReparsePath -PathToCheck $ProcessTempRoot -Label "IC6 process temp"

try {
    [void](Invoke-GitCapture -Arguments @(
        "worktree", "add", "--detach", $IsolatedProjectRoot, $ExpectedCommit
    ))
    $WorktreeAdded = $true
    Assert-NoReparsePath -PathToCheck $IsolatedProjectRoot -Label "IC6 isolated worktree"
    Assert-IsolatedSourceState -Expected $ExpectedCommit
    foreach ($requiredFile in @($IsolatedPromotionTool, $IsolatedSidecarBuild)) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Required isolated IC6 build helper is missing: $requiredFile"
        }
    }
    New-Item -ItemType Directory -Path $env:CARGO_TARGET_DIR -Force | Out-Null
    Invoke-CheckedNative `
        -Command "python.exe" `
        -Arguments @((Join-Path $IsolatedProjectRoot "tools\verify_build_requirements.py")) `
        -FailureMessage "Pinned Windows build requirements are unavailable"

    Push-Location $IsolatedProjectRoot
    try {
        Write-Step "0/9" "Installing the exact locked frontend toolchain in the isolated source..."
        Invoke-CheckedNative `
            -Command "npm.cmd" `
            -Arguments @("ci", "--no-audit", "--no-fund", "--ignore-scripts") `
            -FailureMessage "Locked frontend dependency installation failed"

        Write-Step "1/9" "Running the complete Python test gate..."
        Invoke-CheckedNative `
            -Command "python.exe" `
            -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py") `
            -FailureMessage "Python tests failed"

        Write-Step "2/9" "Running the complete frontend test gate..."
        Invoke-CheckedNative `
            -Command "npm.cmd" `
            -Arguments @("run", "test:frontend") `
            -FailureMessage "Frontend tests failed"

        Write-Step "3/9" "Building Vite production assets..."
        Invoke-CheckedNative `
            -Command "npm.cmd" `
            -Arguments @("run", "build") `
            -FailureMessage "Vite production build failed"

        Write-Step "4/9" "Verifying the infinite-canvas production bundle..."
        Invoke-CheckedNative `
            -Command "npm.cmd" `
            -Arguments @("run", "verify:canvas-bundle") `
            -FailureMessage "Infinite-canvas bundle verification failed"

        Write-Step "5/9" "Building the isolated Python sidecar resource..."
        & $IsolatedSidecarBuild
        if ($LASTEXITCODE -ne 0) {
            throw "Python sidecar build failed (exit code $LASTEXITCODE)"
        }

        Write-Step "6/9" "Running the locked custom-protocol Rust tests..."
        Invoke-CheckedNative `
            -Command "cargo.exe" `
            -Arguments @(
                "test", "--locked", "--manifest-path", "src-tauri\Cargo.toml",
                "--features", "custom-protocol"
            ) `
            -FailureMessage "Rust custom-protocol tests failed"

        Write-Step "7/9" "Checking the custom-protocol Rust target..."
        Invoke-CheckedNative `
            -Command "cargo.exe" `
            -Arguments @(
                "check", "--locked", "--manifest-path", "src-tauri\Cargo.toml",
                "--features", "custom-protocol"
            ) `
            -FailureMessage "Rust custom-protocol check failed"

        Write-Step "8/9" "Building the custom-protocol Tauri release without a bundle..."
        Invoke-CheckedNative `
            -Command "npx.cmd" `
            -Arguments @(
                "--no-install", "tauri", "build", "--no-bundle",
                "--features", "custom-protocol"
            ) `
            -FailureMessage "Tauri release build failed"

    } finally {
        Pop-Location
    }

    Assert-IsolatedSourceState -Expected $ExpectedCommit
    Assert-SourceState -Expected $ExpectedCommit
    if (Test-Path -LiteralPath $TransactionPath) {
        throw "A portable promotion transaction appeared during the candidate build: $TransactionPath"
    }
    if (-not (Test-Path -LiteralPath $SourceExe -PathType Leaf)) {
        throw "Built Tauri executable is missing: $SourceExe"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $SourceSidecar "python-server.exe") -PathType Leaf)) {
        throw "Built Python sidecar is missing: $SourceSidecar"
    }

    $DetachedAppSnapshot = New-DetachedCargoArtifact `
        -SourcePath $SourceExe `
        -ExpectedPeerPath $SourceExePeer `
        -CargoRoot $env:CARGO_TARGET_DIR `
        -DestinationPath $DetachedApp `
        -DestinationRoot $ProcessTempRoot
    $DetachedAppLock = $DetachedAppSnapshot.Lock

    Write-Step "9/9" "Assembling only the canonical portable candidate..."
    Invoke-CheckedNative `
        -Command "python.exe" `
        -Arguments @(
            $IsolatedPromotionTool,
            "stage",
            "--project-root", $ProjectRoot,
            "--app-exe", $DetachedAppSnapshot.Path,
            "--sidecar-dir", $SourceSidecar,
            "--candidate-dir", $CandidateDir,
            "--git-commit", $ExpectedCommit
        ) `
        -FailureMessage "Portable candidate assembly failed"
} finally {
    if ($DetachedAppLock) {
        $DetachedAppLock.Dispose()
        $DetachedAppLock = $null
    }
    Remove-IsolatedBuildWorktree
    foreach ($ownedDirectory in @(
        [pscustomobject]@{
            Path = $env:CARGO_TARGET_DIR
            Parent = $CargoTargetBase
            Label = "IC6 Cargo target"
        },
        [pscustomobject]@{
            Path = $ProcessTempRoot
            Parent = $ProcessTempBase
            Label = "IC6 process temp"
        }
    )) {
        try {
            Remove-OwnedDirectory `
                -PathToRemove $ownedDirectory.Path `
                -AllowedParent $ownedDirectory.Parent `
                -ExpectedLeaf $BuildIdentity `
                -Label $ownedDirectory.Label
        } catch {
            Write-Warning "Could not clean this run's $($ownedDirectory.Label): $($_.Exception.Message)"
        }
    }
}

Write-Host "IC6 candidate assembled without promotion." -ForegroundColor Green
Write-Host "Candidate: $CandidateDir"
Write-Host "Commit: $ExpectedCommit"
