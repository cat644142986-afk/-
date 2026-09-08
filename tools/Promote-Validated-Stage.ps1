# Product Atelier validated-stage promotion.
#
# This entry point never builds a candidate. It accepts only the canonical
# candidate plus an exact, reviewed Validated receipt hash. The old formal
# release remains recoverable until formal smoke succeeds and the transaction
# is finalized. The desktop shortcut is published only after finalization.
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$ExpectedGitCommit,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{64}$')]
    [string]$ValidatedReceiptSha256,
    [string]$ReceiptPath = "",
    [string]$BackupRoot = "D:\ProductAtelier-Backups"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$CandidateDir = [System.IO.Path]::GetFullPath((Join-Path $BuildRoot "portable-candidate-current"))
$PortableDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "release\ProductAtelier-Portable"))
$TargetExe = Join-Path $PortableDir "Product Atelier.exe"
$TransactionPath = Join-Path $BuildRoot "portable-promotion-transaction.json"
$ReleaseLockPath = Join-Path $BuildRoot "portable-release.lock"
$PromotionTool = Join-Path $PSScriptRoot "portable_release.py"
if (-not $ReceiptPath) { $ReceiptPath = Join-Path $BuildRoot "portable-validated-stage.json" }
$ReceiptPath = [System.IO.Path]::GetFullPath($ReceiptPath)
$ValidatedReceiptSha256 = $ValidatedReceiptSha256.ToUpperInvariant()

function Test-SamePath([string]$Left, [string]$Right) {
    try {
        $leftFull = [System.IO.Path]::GetFullPath($Left)
        $rightFull = [System.IO.Path]::GetFullPath($Right)
    } catch {
        return $false
    }
    return [string]::Equals($leftFull, $rightFull, [System.StringComparison]::OrdinalIgnoreCase)
}

function Invoke-JsonCommand([string]$FilePath, [string[]]$ArgumentList) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $FilePath @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Command failed: $FilePath $($ArgumentList -join ' '): $($output -join ' ')"
    }
    try {
        return (($output | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json
    } catch {
        throw "Command did not return valid JSON: $FilePath $($ArgumentList -join ' ')"
    }
}

function Stop-PortableProcesses([string]$ReleaseDirectory) {
    $expectedApp = [System.IO.Path]::GetFullPath((Join-Path $ReleaseDirectory "Product Atelier.exe"))
    $expectedSidecar = [System.IO.Path]::GetFullPath((Join-Path $ReleaseDirectory "python-server\python-server.exe"))
    foreach ($processName in @("Product Atelier.exe", "product-atelier.exe", "python-server.exe")) {
        foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='$processName'" -ErrorAction SilentlyContinue)) {
            if (-not $process.ExecutablePath) { continue }
            if (
                (Test-SamePath ([string]$process.ExecutablePath) $expectedApp) -or
                (Test-SamePath ([string]$process.ExecutablePath) $expectedSidecar)
            ) {
                Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Assert-ValidatedReceipt {
    if (-not (Test-SamePath (Split-Path -Parent $ReceiptPath) $BuildRoot)) {
        throw "Validated receipts must be direct children of: $BuildRoot"
    }
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        throw "Validated stage receipt is missing: $ReceiptPath"
    }
    $actualReceiptSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReceiptPath).Hash
    if (-not [string]::Equals(
        $actualReceiptSha256,
        $ValidatedReceiptSha256,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Validated stage receipt SHA-256 changed after review"
    }
    try {
        $receipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
    } catch {
        throw "Validated stage receipt is invalid JSON: $ReceiptPath"
    }
    if ([int]$receipt.format_version -ne 1) { throw "Unsupported validated stage receipt format" }
    if ([string]$receipt.kind -cne "product-atelier-validated-portable-stage") {
        throw "Validated stage receipt has an invalid kind"
    }
    if ([string]$receipt.status -cne "validated") {
        throw "Only a Validated stage may be promoted"
    }
    if (-not [string]::Equals(
        ([string]$receipt.git_commit).Trim(),
        $ExpectedGitCommit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Validated stage receipt names another Git commit"
    }
    if (-not (Test-SamePath ([string]$receipt.project_root) $ProjectRoot)) {
        throw "Validated stage receipt belongs to another project"
    }
    if (-not (Test-SamePath ([string]$receipt.candidate_dir) $CandidateDir)) {
        throw "Validated stage receipt names another candidate directory"
    }
    $requiredGates = @(
        "portable_identity",
        "packaged_sidecar_smoke",
        "packaged_app_smoke",
        "packaged_schema_upgrade",
        "post_smoke_identity"
    )
    $passedGates = @($receipt.gates | Where-Object { [string]$_.status -ceq "passed" } | ForEach-Object { [string]$_.name })
    foreach ($gate in $requiredGates) {
        if ($passedGates -notcontains $gate) { throw "Validated stage receipt is missing passed gate: $gate" }
    }
    $identitySha256 = ([string]$receipt.candidate_identity_sha256).Trim().ToUpperInvariant()
    if ($identitySha256 -notmatch '^[0-9A-F]{64}$') {
        throw "Validated stage receipt has an invalid candidate identity SHA-256"
    }
    return [ordered]@{
        receipt = $receipt
        receipt_sha256 = $actualReceiptSha256
        candidate_identity_sha256 = $identitySha256
    }
}

if ($env:OS -ne "Windows_NT") { throw "Validated-stage promotion requires Windows." }
foreach ($command in @("git.exe", "python.exe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required promotion command is unavailable: $command"
    }
}
if (-not (Test-Path -LiteralPath $PromotionTool -PathType Leaf)) {
    throw "Portable promotion helper is missing: $PromotionTool"
}
if (Test-Path -LiteralPath $TransactionPath) {
    throw "An unfinished promotion exists: $TransactionPath"
}
& git.exe -C $ProjectRoot cat-file -e "$ExpectedGitCommit`^{commit}"
if ($LASTEXITCODE -ne 0) { throw "Expected Git commit is unavailable: $ExpectedGitCommit" }
$remoteRefs = @(& git.exe -C $ProjectRoot for-each-ref --contains $ExpectedGitCommit --format='%(refname)' refs/remotes/origin 2>$null)
if ($LASTEXITCODE -ne 0 -or $remoteRefs.Count -lt 1) {
    throw "Expected Git commit is not reachable from a local origin tracking ref"
}

$validated = Assert-ValidatedReceipt
$candidateIdentitySha256 = [string]$validated.candidate_identity_sha256
$identity = Invoke-JsonCommand -FilePath "python.exe" -ArgumentList @(
    $PromotionTool, "verify-identity",
    "--project-root", $ProjectRoot,
    "--candidate-dir", $CandidateDir,
    "--git-commit", $ExpectedGitCommit,
    "--candidate-identity-sha256", $candidateIdentitySha256
)
if (($identity.candidate | ConvertTo-Json -Depth 10 -Compress) -cne ($validated.receipt.candidate | ConvertTo-Json -Depth 10 -Compress)) {
    throw "Candidate identity no longer matches the Validated stage receipt"
}

$resolvedBackupRoot = [System.IO.Path]::GetFullPath($BackupRoot)
$backupDrive = [System.IO.Path]::GetPathRoot($resolvedBackupRoot)
if (-not (Test-Path -LiteralPath $backupDrive -PathType Container)) {
    throw "Backup drive is unavailable: $backupDrive"
}
if ((Test-SamePath $resolvedBackupRoot $ProjectRoot) -or $resolvedBackupRoot.StartsWith($ProjectRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Backup root may not overlap the project tree"
}
if (-not (Test-Path -LiteralPath $resolvedBackupRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $resolvedBackupRoot | Out-Null
}
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$shortCommit = $ExpectedGitCommit.Substring(0, 12).ToLowerInvariant()
$BackupDir = Join-Path $resolvedBackupRoot "release-before-$timestamp-$shortCommit"

New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
$releaseLock = $null
$promotionTransactionId = ""
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

    Write-Host "=== Product Atelier Validated Stage Promotion ===" -ForegroundColor Cyan
    Write-Host "[1/5] Re-verifying exact Validated candidate identity..." -ForegroundColor Yellow
    Write-Host "  Commit: $ExpectedGitCommit"
    Write-Host "  Validated receipt SHA-256: $($validated.receipt_sha256)"

    Write-Host "[2/5] Backing up and promoting candidate transactionally..." -ForegroundColor Yellow
    Stop-PortableProcesses $PortableDir
    Start-Sleep -Seconds 2
    try {
        $begin = Invoke-JsonCommand -FilePath "python.exe" -ArgumentList @(
            $PromotionTool, "begin",
            "--project-root", $ProjectRoot,
            "--candidate-dir", $CandidateDir,
            "--portable-dir", $PortableDir,
            "--backup-dir", $BackupDir,
            "--transaction", $TransactionPath,
            "--git-commit", $ExpectedGitCommit,
            "--candidate-identity-sha256", $candidateIdentitySha256
        )
        $promotionTransactionId = ([string]$begin.transaction_id).Trim()
        if ($promotionTransactionId -notmatch '^[0-9a-f]{32}$') {
            throw "Promotion begin returned an invalid transaction id"
        }

        Write-Host "[3/5] Smoking the promoted formal directory..." -ForegroundColor Yellow
        & (Join-Path $PSScriptRoot "Test-Portable.ps1") `
            -PortableDir $PortableDir `
            -ExpectedGitCommit $ExpectedGitCommit
        & (Join-Path $PSScriptRoot "Test-Portable-App.ps1") `
            -PortableDir $PortableDir `
            -ExpectedGitCommit $ExpectedGitCommit
    } catch {
        $originalError = [string]$_.Exception.Message
        if ((Test-Path -LiteralPath $TransactionPath) -and $promotionTransactionId -match '^[0-9a-f]{32}$') {
            Stop-PortableProcesses $PortableDir
            & python.exe $PromotionTool rollback `
                --project-root $ProjectRoot `
                --transaction $TransactionPath `
                --reason $originalError `
                --git-commit $ExpectedGitCommit `
                --transaction-id $promotionTransactionId
            if ($LASTEXITCODE -ne 0) {
                throw "Promotion failed: $originalError. Rollback also failed; preserve $TransactionPath for recovery."
            }
        }
        throw
    }

    Write-Host "[4/5] Finalizing promotion evidence..." -ForegroundColor Yellow
    $finalized = Invoke-JsonCommand -FilePath "python.exe" -ArgumentList @(
        $PromotionTool, "finalize",
        "--project-root", $ProjectRoot,
        "--transaction", $TransactionPath,
        "--git-commit", $ExpectedGitCommit,
        "--transaction-id", $promotionTransactionId
    )
    if (Test-Path -LiteralPath $TransactionPath) {
        throw "Promotion transaction still exists after finalization: $TransactionPath"
    }

    Write-Host "[5/5] Publishing the finalized desktop entry..." -ForegroundColor Yellow
    $desktopDirectory = [Environment]::GetFolderPath("Desktop")
    if (-not $desktopDirectory) { throw "Windows desktop directory is unavailable" }
    $desktopShortcut = Join-Path $desktopDirectory "Product Atelier.lnk"
    $temporaryShortcut = Join-Path $desktopDirectory (".Product-Atelier-" + [guid]::NewGuid().ToString("N") + ".lnk")
    $shortcutBackup = ""
    $keepShortcutBackup = $false
    try {
        $WshShell = New-Object -ComObject WScript.Shell
        $shortcut = $WshShell.CreateShortcut($temporaryShortcut)
        $shortcut.TargetPath = $TargetExe
        $shortcut.Arguments = ""
        $shortcut.WorkingDirectory = $PortableDir
        $shortcut.IconLocation = "$TargetExe,0"
        $shortcut.Description = "Product Atelier stable $([string]$identity.candidate.artifacts.contract_version) ($shortCommit)"
        $shortcut.Save()
        if (Test-Path -LiteralPath $desktopShortcut -PathType Leaf) {
            $shortcutBackup = Join-Path $desktopDirectory (".Product-Atelier-backup-" + [guid]::NewGuid().ToString("N") + ".lnk")
            [System.IO.File]::Replace($temporaryShortcut, $desktopShortcut, $shortcutBackup, $true)
        } else {
            Move-Item -LiteralPath $temporaryShortcut -Destination $desktopShortcut
        }

        $publishedShortcut = $WshShell.CreateShortcut($desktopShortcut)
        if (
            -not (Test-SamePath ([string]$publishedShortcut.TargetPath) $TargetExe) -or
            -not [string]::IsNullOrEmpty([string]$publishedShortcut.Arguments) -or
            -not (Test-SamePath ([string]$publishedShortcut.WorkingDirectory) $PortableDir) -or
            -not [string]::Equals(
                ([string]$publishedShortcut.IconLocation).Trim(),
                "$TargetExe,0",
                [System.StringComparison]::OrdinalIgnoreCase
            ) -or
            -not (Test-Path -LiteralPath $publishedShortcut.TargetPath -PathType Leaf)
        ) {
            throw "Published desktop shortcut failed its Target/Arguments/WorkingDirectory/Icon gate"
        }
    } catch {
        if ($shortcutBackup -and (Test-Path -LiteralPath $shortcutBackup -PathType Leaf)) {
            try {
                Copy-Item -LiteralPath $shortcutBackup -Destination $desktopShortcut -Force
            } catch {
                $keepShortcutBackup = $true
            }
        }
        throw
    } finally {
        if (Test-Path -LiteralPath $temporaryShortcut) {
            Remove-Item -LiteralPath $temporaryShortcut -Force -ErrorAction SilentlyContinue
        }
        if (-not $keepShortcutBackup -and $shortcutBackup -and (Test-Path -LiteralPath $shortcutBackup)) {
            Remove-Item -LiteralPath $shortcutBackup -Force -ErrorAction SilentlyContinue
        }
    }

    [ordered]@{
        status = "promoted"
        git_commit = $ExpectedGitCommit.ToLowerInvariant()
        contract_version = [string]$identity.candidate.artifacts.contract_version
        ledger_schema_version = [int]$identity.candidate.artifacts.ledger_schema_version
        candidate_identity_sha256 = $candidateIdentitySha256
        validated_receipt_sha256 = [string]$validated.receipt_sha256
        transaction_id = $promotionTransactionId
        portable_dir = $PortableDir
        target_exe = $TargetExe
        backup_dir = $BackupDir
        promotion_evidence = [string]$finalized.evidence_path
        desktop_shortcut = $desktopShortcut
    } | ConvertTo-Json -Depth 8 -Compress
} finally {
    if ($releaseLock) { $releaseLock.Dispose() }
    if (Test-Path -LiteralPath $ReleaseLockPath) {
        Remove-Item -LiteralPath $ReleaseLockPath -Force -ErrorAction SilentlyContinue
    }
}
