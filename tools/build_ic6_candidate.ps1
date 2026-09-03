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
$SourceSidecar = Join-Path $IsolatedProjectRoot "src-tauri\bin\python-server"
$IsolatedPromotionTool = Join-Path $IsolatedProjectRoot "tools\portable_release.py"
$IsolatedSidecarBuild = Join-Path $IsolatedProjectRoot "tools\Build-Sidecar.ps1"
$WorktreeAdded = $false

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

function Assert-CleanWorktree {
    $status = Invoke-GitCapture -Arguments @(
        "status", "--porcelain=v1", "--untracked-files=all"
    )
    if (-not [string]::IsNullOrWhiteSpace($status)) {
        throw "IC6 candidate builds require a clean worktree:`n$status"
    }
}

function Assert-SourceState([string]$Expected, [switch]$FetchOrigin) {
    Assert-CleanWorktree

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

    [void](Invoke-GitCapture -Arguments @("diff", "--check"))
    Assert-CleanWorktree
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
    $status = Invoke-GitCaptureAt `
        -Repository $IsolatedProjectRoot `
        -Arguments @("status", "--porcelain=v1", "--untracked-files=no")
    if (-not [string]::IsNullOrWhiteSpace($status)) {
        throw "IC6 isolated tracked source changed during the build:`n$status"
    }
    [void](Invoke-GitCaptureAt `
        -Repository $IsolatedProjectRoot `
        -Arguments @("diff", "--check"))
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

    Write-Step "9/9" "Assembling only the canonical portable candidate..."
    Invoke-CheckedNative `
        -Command "python.exe" `
        -Arguments @(
            $IsolatedPromotionTool,
            "stage",
            "--project-root", $ProjectRoot,
            "--app-exe", $SourceExe,
            "--sidecar-dir", $SourceSidecar,
            "--candidate-dir", $CandidateDir,
            "--git-commit", $ExpectedCommit
        ) `
        -FailureMessage "Portable candidate assembly failed"
} finally {
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
