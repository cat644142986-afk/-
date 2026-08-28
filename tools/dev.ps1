# ============================================================
# Product Atelier - Verified Windows Portable Release Promotion
# Usage: powershell -File tools\dev.ps1 [-NoScreenshot]
#
# This script builds and smokes an isolated candidate before it touches the
# formal portable directory. Promotion is backed up, journaled, re-smoked and
# finalized; failures before finalization restore the previous formal release.
# ============================================================
param(
    [switch]$Quick,
    [switch]$SkipSidecar,
    [switch]$NoScreenshot,
    [int]$WaitSeconds = 15,
    [string]$BackupRoot = "D:\ProductAtelier-Backups"
)

$ErrorActionPreference = "Stop"
if ($Quick -or $SkipSidecar) {
    throw "-Quick and -SkipSidecar are not allowed for a formal portable promotion."
}
if ($env:OS -ne "Windows_NT") {
    throw "tools/dev.ps1 is the Windows formal-release gate and must run on Windows."
}
if ($PSVersionTable.PSVersion.Major -lt 5) {
    throw "Windows PowerShell 5 or newer is required."
}

$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$RequiredBranch = "codex/master-roadmap-phase-0-1"
$RequiredUpstream = "origin/$RequiredBranch"
$env:PATH = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:USERPROFILE\.cargo\bin;C:\mingw64\bin;C:\msys64\mingw64\bin;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"

$SourceExe = Join-Path $env:CARGO_TARGET_DIR "release\product-atelier.exe"
$SourceSidecar = Join-Path $ProjectRoot "src-tauri\bin\python-server"
$CandidateDir = Join-Path $ProjectRoot "build\portable-candidate-current"
$PortableDir = Join-Path $ProjectRoot "release\ProductAtelier-Portable"
$TargetExe = Join-Path $PortableDir "Product Atelier.exe"
$TransactionPath = Join-Path $ProjectRoot "build\portable-promotion-transaction.json"
$PromotionTool = Join-Path $PSScriptRoot "portable_release.py"
$BuildRequirements = Join-Path $ProjectRoot "python\requirements-build.txt"
$ReleaseLockPath = Join-Path $ProjectRoot "build\portable-release.lock"

function Invoke-GitCapture([string[]]$Arguments) {
    $output = @(& git.exe -C $ProjectRoot @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Git command failed (git $($Arguments -join ' ')): $($output -join ' ')"
    }
    return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Assert-ReleaseSourceState([switch]$FetchOrigin) {
    if ($FetchOrigin) {
        $fetchOutput = @(& git.exe -C $ProjectRoot fetch origin 2>&1)
        $fetchExitCode = $LASTEXITCODE
        foreach ($line in $fetchOutput) { Write-Host $line }
        if ($fetchExitCode -ne 0) { throw "Could not fetch the GitHub origin." }
    }

    $branch = Invoke-GitCapture -Arguments @("branch", "--show-current")
    if ($branch -ne $RequiredBranch) {
        throw "Formal release requires branch $RequiredBranch; current branch is $branch"
    }
    $upstream = Invoke-GitCapture -Arguments @(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", '@{upstream}'
    )
    if ($upstream -ne $RequiredUpstream) {
        throw "Formal release requires upstream $RequiredUpstream; current upstream is $upstream"
    }
    $head = Invoke-GitCapture -Arguments @("rev-parse", "--verify", "HEAD")
    $upstreamHead = Invoke-GitCapture -Arguments @("rev-parse", "--verify", $RequiredUpstream)
    if (-not [string]::Equals($head, $upstreamHead, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Local HEAD does not match the GitHub upstream. Pull or push before releasing."
    }
    $status = Invoke-GitCapture -Arguments @(
        "status", "--porcelain=v1", "--untracked-files=all"
    )
    if (-not [string]::IsNullOrWhiteSpace($status)) {
        throw "Formal release requires a clean worktree:`n$status"
    }
    return $head
}

function Test-SamePath([string]$Left, [string]$Right) {
    try {
        $leftFull = [System.IO.Path]::GetFullPath($Left)
        $rightFull = [System.IO.Path]::GetFullPath($Right)
    } catch {
        return $false
    }
    return [string]::Equals(
        $leftFull,
        $rightFull,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Stop-PortableProcesses([string]$ReleaseDirectory) {
    $expectedApp = [System.IO.Path]::GetFullPath((Join-Path $ReleaseDirectory "Product Atelier.exe"))
    $expectedSidecar = [System.IO.Path]::GetFullPath((Join-Path $ReleaseDirectory "python-server\python-server.exe"))
    $targets = @($expectedApp, $expectedSidecar)
    $processes = @()
    foreach ($processName in @("Product Atelier.exe", "product-atelier.exe", "python-server.exe")) {
        $processes += @(
            Get-CimInstance Win32_Process -Filter "Name='$processName'" -ErrorAction SilentlyContinue
        )
    }
    foreach ($process in $processes) {
        if (-not $process.ExecutablePath) { continue }
        $matches = $false
        foreach ($target in $targets) {
            if (Test-SamePath ([string]$process.ExecutablePath) $target) {
                $matches = $true
                break
            }
        }
        if (-not $matches) { continue }

        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.ProcessId)" -ErrorAction SilentlyContinue
        if (-not $current -or -not $current.ExecutablePath) { continue }
        $stillMatches = $false
        foreach ($target in $targets) {
            if (Test-SamePath ([string]$current.ExecutablePath) $target) {
                $stillMatches = $true
                break
            }
        }
        if ($stillMatches) {
            Stop-Process -Id ([int]$current.ProcessId) -Force -ErrorAction SilentlyContinue
        }
    }
}

foreach ($command in @("git.exe", "python.exe", "npm.cmd", "npx.cmd", "cargo.exe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required release command is unavailable: $command"
    }
}
if (-not (Test-Path -LiteralPath "C:\mingw64\bin\gcc.exe" -PathType Leaf)) {
    throw "The Rust GNU linker is missing: C:\mingw64\bin\gcc.exe"
}
$targetDrive = [System.IO.Path]::GetPathRoot($env:CARGO_TARGET_DIR)
if (-not (Test-Path -LiteralPath $targetDrive -PathType Container)) {
    throw "Cargo target drive is unavailable: $targetDrive"
}
New-Item -ItemType Directory -Path $env:CARGO_TARGET_DIR -Force | Out-Null
if (-not (Test-Path -LiteralPath $BuildRequirements -PathType Leaf)) {
    throw "Windows build requirements are missing: $BuildRequirements"
}
if (-not (Test-Path -LiteralPath $PromotionTool -PathType Leaf)) {
    throw "Portable promotion helper is missing: $PromotionTool"
}

New-Item -ItemType Directory -Path (Split-Path -Parent $ReleaseLockPath) -Force | Out-Null
$releaseLock = $null
try {
    try {
        $releaseLock = [System.IO.File]::Open(
            $ReleaseLockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    } catch {
        throw "Another Product Atelier release process owns $ReleaseLockPath"
    }

    Write-Host "=== Product Atelier Verified Portable Release ===" -ForegroundColor Cyan
    Write-Host "Project: $ProjectRoot"
    Write-Host "Formal directory: $PortableDir"
    Write-Host ""

    if (Test-Path -LiteralPath $TransactionPath) {
        throw "An unfinished portable promotion exists: $TransactionPath. Finalize or roll it back first."
    }

    Write-Host "[1/11] Verifying clean GitHub-aligned source..." -ForegroundColor Yellow
    $BuildHead = (Assert-ReleaseSourceState -FetchOrigin).Trim()
    $shortHead = $BuildHead.Substring(0, 12)
    Write-Host "  Build commit: $BuildHead" -ForegroundColor Green

    Write-Host "[2/11] Verifying pinned Windows build tools..." -ForegroundColor Yellow
    $versionCheck = @'
from importlib.metadata import version
expected = {"pyinstaller": "6.22.2", "pyinstaller-hooks-contrib": "2026.7"}
actual = {name: version(name) for name in expected}
missing = [f"{name}={actual[name]} (expected {wanted})" for name, wanted in expected.items() if actual[name] != wanted]
if missing:
    raise SystemExit("; ".join(missing))
print(", ".join(f"{name}={actual[name]}" for name in expected))
'@
    & python.exe -c $versionCheck
    if ($LASTEXITCODE -ne 0) {
        throw "Pinned PyInstaller tools are unavailable. Run: python -m pip install -r python\requirements-build.txt"
    }

    Write-Host "[3/11] Building current Python sidecar (candidate resource only)..." -ForegroundColor Yellow
    & "$PSScriptRoot\Build-Sidecar.ps1"

    Write-Host "[4/11] Running the complete Python test gate..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        & python.exe -m unittest discover -s tests -p "test_*.py"
        if ($LASTEXITCODE -ne 0) { throw "Python tests failed" }
    } finally {
        Pop-Location
    }

    Write-Host "[5/11] Running the complete frontend test gate..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        & npm.cmd run test:frontend
        if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }
    } finally {
        Pop-Location
    }

    Write-Host "[6/11] Building embedded frontend assets..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed" }
    } finally {
        Pop-Location
    }

    Write-Host "[7/11] Checking and building the custom-protocol Tauri release..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        & cargo.exe check --locked --manifest-path "src-tauri\Cargo.toml" --features custom-protocol
        if ($LASTEXITCODE -ne 0) { throw "Rust custom-protocol check failed" }
        & npx.cmd tauri build --no-bundle --features custom-protocol
        if ($LASTEXITCODE -ne 0) { throw "Tauri release build failed" }
    } finally {
        Pop-Location
    }

    [void](Invoke-GitCapture -Arguments @("diff", "--check"))
    $ConfirmedHead = (Assert-ReleaseSourceState -FetchOrigin).Trim()
    if (-not [string]::Equals(
        $BuildHead,
        $ConfirmedHead,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Source changed during the release build; refusing to assemble a mixed candidate."
    }
    if (-not (Test-Path -LiteralPath $SourceExe -PathType Leaf)) {
        throw "Built Tauri executable is missing: $SourceExe"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $SourceSidecar "python-server.exe") -PathType Leaf)) {
        throw "Built Python sidecar is missing: $SourceSidecar"
    }

    Write-Host "[8/11] Assembling an isolated portable candidate..." -ForegroundColor Yellow
    & python.exe $PromotionTool stage `
        --project-root $ProjectRoot `
        --app-exe $SourceExe `
        --sidecar-dir $SourceSidecar `
        --candidate-dir $CandidateDir `
        --git-commit $BuildHead
    if ($LASTEXITCODE -ne 0) { throw "Portable candidate assembly failed" }

    Write-Host "[9/11] Smoking the isolated candidate..." -ForegroundColor Yellow
    & "$PSScriptRoot\Test-Portable.ps1" -PortableDir $CandidateDir -ExpectedGitCommit $BuildHead
    & "$PSScriptRoot\Test-Portable-App.ps1" -PortableDir $CandidateDir -ExpectedGitCommit $BuildHead

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $resolvedBackupRoot = [System.IO.Path]::GetFullPath($BackupRoot)
    $BackupDir = Join-Path $resolvedBackupRoot "release-before-$timestamp-$shortHead"

    Write-Host "[10/11] Backing up and promoting the verified candidate..." -ForegroundColor Yellow
    Stop-PortableProcesses $PortableDir
    Start-Sleep -Seconds 2
    $promotionAttempted = $false
    $promotionTransactionId = ""
    try {
        $promotionAttempted = $true
        $beginOutput = @(& python.exe $PromotionTool begin `
            --project-root $ProjectRoot `
            --candidate-dir $CandidateDir `
            --portable-dir $PortableDir `
            --backup-dir $BackupDir `
            --transaction $TransactionPath `
            --git-commit $BuildHead 2>&1)
        $beginExitCode = $LASTEXITCODE
        foreach ($line in $beginOutput) { Write-Host $line }
        if ($beginExitCode -ne 0) { throw "Portable promotion begin failed" }
        try {
            $beginResult = (($beginOutput | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json
            $promotionTransactionId = ([string]$beginResult.transaction_id).Trim()
        } catch {
            throw "Portable promotion begin returned invalid transaction evidence"
        }
        if ($promotionTransactionId -notmatch '^[0-9a-f]{32}$') {
            throw "Portable promotion begin returned an invalid transaction id"
        }

        & "$PSScriptRoot\Test-Portable.ps1" -PortableDir $PortableDir -ExpectedGitCommit $BuildHead
        & "$PSScriptRoot\Test-Portable-App.ps1" -PortableDir $PortableDir -ExpectedGitCommit $BuildHead
    } catch {
        $originalError = [string]$_.Exception.Message
        if ($promotionAttempted -and (Test-Path -LiteralPath $TransactionPath)) {
            if ($promotionTransactionId -notmatch '^[0-9a-f]{32}$') {
                throw "Release failed: $originalError. The exact promotion id was not captured; preserve $TransactionPath for explicit recovery."
            }
            Stop-PortableProcesses $PortableDir
            & python.exe $PromotionTool rollback `
                --project-root $ProjectRoot `
                --transaction $TransactionPath `
                --reason $originalError `
                --git-commit $BuildHead `
                --transaction-id $promotionTransactionId
            $rollbackExitCode = $LASTEXITCODE
            if ($rollbackExitCode -ne 0) {
                throw "Release failed: $originalError. Rollback also failed; preserve $TransactionPath for recovery."
            }
        }
        throw
    }

    Write-Host "[11/11] Finalizing evidence and publishing the desktop entry..." -ForegroundColor Yellow
    & python.exe $PromotionTool finalize `
        --project-root $ProjectRoot `
        --transaction $TransactionPath `
        --git-commit $BuildHead `
        --transaction-id $promotionTransactionId
    if ($LASTEXITCODE -ne 0) {
        throw "Promotion finalization is incomplete. Keep the formal directory unchanged and rerun finalize using $TransactionPath"
    }
    if (Test-Path -LiteralPath $TransactionPath) {
        throw "Promotion transaction still exists after finalization: $TransactionPath"
    }

    $desktopShortcut = ""
    $temporaryShortcut = ""
    $postPromotionWarnings = @()
    try {
        $desktopDirectory = [Environment]::GetFolderPath("Desktop")
        if (-not $desktopDirectory) { throw "Windows desktop directory is unavailable" }
        $desktopShortcut = Join-Path $desktopDirectory "Product Atelier.lnk"
        $temporaryShortcut = Join-Path $desktopDirectory (".Product-Atelier-" + [guid]::NewGuid().ToString("N") + ".lnk")
        $WshShell = New-Object -ComObject WScript.Shell
        $shortcut = $WshShell.CreateShortcut($temporaryShortcut)
        $shortcut.TargetPath = $TargetExe
        $shortcut.WorkingDirectory = $PortableDir
        $shortcut.IconLocation = "$TargetExe,0"
        $shortcut.Save()
        if (Test-Path -LiteralPath $desktopShortcut) {
            [System.IO.File]::Replace($temporaryShortcut, $desktopShortcut, $null)
        } else {
            Move-Item -LiteralPath $temporaryShortcut -Destination $desktopShortcut
        }
    } catch {
        $shortcutError = [string]$_.Exception.Message
        $shortcutAlreadyValid = $false
        try {
            if ($desktopShortcut -and (Test-Path -LiteralPath $desktopShortcut -PathType Leaf)) {
                $shortcutReader = New-Object -ComObject WScript.Shell
                $existingShortcut = $shortcutReader.CreateShortcut($desktopShortcut)
                $shortcutAlreadyValid = (
                    (Test-SamePath ([string]$existingShortcut.TargetPath) $TargetExe) -and
                    (Test-SamePath ([string]$existingShortcut.WorkingDirectory) $PortableDir)
                )
            }
        } catch {
            $shortcutAlreadyValid = $false
        }
        if (-not $shortcutAlreadyValid) {
            throw "Promotion is finalized and must not be rolled back, but the desktop shortcut is not valid: $shortcutError"
        }
        $postPromotionWarnings += "Shortcut replacement failed, but the existing shortcut already targets the finalized formal directory: $shortcutError"
    } finally {
        if ($temporaryShortcut -and (Test-Path -LiteralPath $temporaryShortcut)) {
            Remove-Item -LiteralPath $temporaryShortcut -Force
        }
    }
    if ($postPromotionWarnings.Count -eq 0) {
        Write-Host "  Desktop shortcut updated after finalization." -ForegroundColor Green
    }

    $finalAppLaunched = $false
    try {
        Start-Process $TargetExe -WorkingDirectory $PortableDir
        $finalAppLaunched = $true
        Write-Host "  Waiting $WaitSeconds seconds for the finalized app to start..." -ForegroundColor DarkGray
        Start-Sleep -Seconds $WaitSeconds
    } catch {
        $postPromotionWarnings += "Post-finalization launch failed: $($_.Exception.Message)"
    }

    if (-not $NoScreenshot -and $finalAppLaunched) {
        $screenshotScript = Join-Path $ProjectRoot "tools\screenshot.py"
        if (Test-Path -LiteralPath $screenshotScript -PathType Leaf) {
            $screenshotPath = Join-Path $ProjectRoot "tools\last_capture.png"
            try {
                if (Test-Path -LiteralPath $screenshotPath) {
                    Remove-Item -LiteralPath $screenshotPath -Force
                }
                & python.exe $screenshotScript --window "Product Atelier" --pad 30 -o $screenshotPath
                $screenshotExitCode = $LASTEXITCODE
                if ($screenshotExitCode -ne 0) { throw "Screenshot command exited with $screenshotExitCode" }
                if (-not (Test-Path -LiteralPath $screenshotPath -PathType Leaf)) {
                    throw "Screenshot artifact was not created"
                }
                Write-Host "  Screenshot saved: $screenshotPath" -ForegroundColor Green
            } catch {
                $postPromotionWarnings += "Post-finalization screenshot failed: $($_.Exception.Message)"
            }
        }
    }

    foreach ($warning in $postPromotionWarnings) {
        Write-Warning $warning
    }

    Write-Host ""
    Write-Host "=== Verified Portable Release Complete ===" -ForegroundColor Green
    Write-Host "Git commit: $BuildHead"
    if ($finalAppLaunched) {
        Write-Host "App launched from: $TargetExe"
    } else {
        Write-Host "App launch: post-promotion warning; formal smoke had already passed"
    }
    if (Test-Path -LiteralPath $BackupDir) {
        Write-Host "Previous formal release backup: $BackupDir"
    } else {
        Write-Host "Previous formal release backup: not applicable (first promotion)"
    }
    Write-Host "Promotion evidence: $(Join-Path $ProjectRoot 'build\last-portable-promotion.json')"
} finally {
    if ($releaseLock) {
        $releaseLock.Dispose()
    }
    if (Test-Path -LiteralPath $ReleaseLockPath) {
        Remove-Item -LiteralPath $ReleaseLockPath -Force -ErrorAction SilentlyContinue
    }
}
