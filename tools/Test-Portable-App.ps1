# Product Atelier full portable application smoke test.
param(
    [string]$PortableDir = "",
    [string]$ExpectedGitCommit = "",
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $PortableDir) {
    $PortableDir = Join-Path $ProjectRoot "release\ProductAtelier-Portable"
}
$PortableDir = [System.IO.Path]::GetFullPath($PortableDir)
$AppExe = [System.IO.Path]::GetFullPath((Join-Path $PortableDir "Product Atelier.exe"))
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    # `tauri build --no-bundle` keeps Cargo's binary name, while the assembled
    # portable directory uses the product name. Accept both so the same smoke
    # gate can verify a candidate before it is promoted.
    $AppExe = [System.IO.Path]::GetFullPath((Join-Path $PortableDir "product-atelier.exe"))
}
$SidecarExe = [System.IO.Path]::GetFullPath((Join-Path $PortableDir "python-server\python-server.exe"))
$ManifestPath = Join-Path $PortableDir "python-server\sidecar-manifest.json"

if (-not $ExpectedGitCommit) {
    $headOutput = @(& git.exe -C $ProjectRoot rev-parse --verify HEAD 2>&1)
    $headExitCode = $LASTEXITCODE
    if ($headExitCode -ne 0) { throw "Could not resolve Git HEAD: $($headOutput -join ' ')" }
    $ExpectedGitCommit = (($headOutput -join "`n").Trim())
}
if ($ExpectedGitCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Expected Git commit must be a full 40-character hash"
}

if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) { throw "Portable app is missing: $AppExe" }
if (-not (Test-Path -LiteralPath $SidecarExe -PathType Leaf)) { throw "Portable sidecar is missing: $SidecarExe" }
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw "Portable manifest is missing: $ManifestPath" }

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if (-not [string]::Equals(
    ([string]$manifest.git_commit).Trim(),
    $ExpectedGitCommit,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Sidecar manifest git_commit does not match the expected Git HEAD"
}
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$testData = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ("ProductAtelier-app-test-" + [guid]::NewGuid().ToString("N"))))
if (-not $testData.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Invalid temporary runtime test path"
}
New-Item -ItemType Directory -Path $testData | Out-Null

function Test-ExpectedSidecarProcess($Process, [int]$ExpectedParentId) {
    if (-not $Process) { return $false }
    if ([int]$Process.ParentProcessId -ne $ExpectedParentId) { return $false }
    if (-not $Process.ExecutablePath) { return $false }
    try {
        $processPath = [System.IO.Path]::GetFullPath([string]$Process.ExecutablePath)
    } catch {
        return $false
    }
    return [string]::Equals(
        $processPath,
        $SidecarExe,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

$previousDataDir = $env:PRODUCT_ATELIER_DATA_DIR
$app = $null
$newSidecars = @()
$trackedSidecarPids = @()

try {
    $env:PRODUCT_ATELIER_DATA_DIR = $testData
    $app = Start-Process -FilePath $AppExe -WorkingDirectory $PortableDir -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $health = $null
    while ((Get-Date) -lt $deadline) {
        $app.Refresh()
        if ($app.HasExited) {
            throw "Portable app exited early with code $($app.ExitCode)"
        }
        $newSidecars = @(
            Get-CimInstance Win32_Process -Filter "Name='python-server.exe'" |
                Where-Object { Test-ExpectedSidecarProcess $_ ([int]$app.Id) }
        )
        if ($newSidecars.Count -eq 1) {
            $sidecarPid = [int]$newSidecars[0].ProcessId
            if ($trackedSidecarPids -notcontains $sidecarPid) {
                $trackedSidecarPids += $sidecarPid
            }
            $portMatch = [regex]::Match([string]$newSidecars[0].CommandLine, '(\d+)\s*$')
            if ($portMatch.Success) {
                $sidecarPort = [int]$portMatch.Groups[1].Value
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$sidecarPort/api/health" -TimeoutSec 3
                    break
                } catch {
                    # The sidecar can exist briefly before FastAPI is accepting requests.
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }

    if ($newSidecars.Count -ne 1) { throw "Expected one new sidecar, found $($newSidecars.Count)" }
    if (-not $health) { throw "Portable app sidecar did not become healthy within $TimeoutSeconds seconds" }
    if ($health.status -ne "ok") { throw "Portable app sidecar health status is not ok" }
    if ($health.service.contract_version -ne $manifest.contract_version) {
        throw "Running portable app contract does not match its manifest"
    }
    if (-not [string]::Equals(
        ([string]$health.service.git_commit).Trim(),
        $ExpectedGitCommit,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Running portable app Git commit does not match the expected Git HEAD"
    }
    if ([int]$health.ledger.schema_version -ne [int]$manifest.ledger_schema_version) {
        throw "Running portable app ledger schema does not match its manifest"
    }

    $ledgerPath = Join-Path $testData "atelier.sqlite3"
    if (-not (Test-Path -LiteralPath $ledgerPath -PathType Leaf)) {
        throw "Portable app did not create its isolated ledger"
    }
    $appLogPath = Join-Path $testData "app.log"
    if (-not (Test-Path -LiteralPath $appLogPath -PathType Leaf)) {
        throw "Portable app did not keep its shell log inside the isolated data directory"
    }
    $appLog = Get-Content -LiteralPath $appLogPath -Raw
    if ($appLog -notmatch 'Frontend mode: embedded-custom-protocol') {
        throw "Portable app is not using embedded frontend assets; refusing a localhost-dependent release"
    }
    if ($appLog -notmatch 'Window metrics: scale=') {
        throw "Portable app did not report DPI-aware window metrics"
    }
    if ($appLog -match 'Could not apply Windows chrome') {
        throw "Portable app could not apply the Windows rounded-corner policy"
    }

    Write-Host "Portable application smoke test passed." -ForegroundColor Green
    Write-Host "Application PID: $($app.Id)"
    Write-Host "Sidecar PID: $($newSidecars[0].ProcessId)"
    Write-Host "Dynamic sidecar port: $sidecarPort"
    Write-Host "Contract: $($health.service.contract_version)"
    Write-Host "Ledger schema: v$($health.ledger.schema_version)"
    Write-Host "Git commit: $ExpectedGitCommit"
    Write-Host "Isolated ledger bytes: $((Get-Item -LiteralPath $ledgerPath).Length)"
    Write-Host "Isolated shell log: verified"
} finally {
    $env:PRODUCT_ATELIER_DATA_DIR = $previousDataDir
    if ($app -and -not $app.HasExited) {
        [void]$app.CloseMainWindow()
        try {
            Wait-Process -Id $app.Id -Timeout 5 -ErrorAction Stop
        } catch {
            Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
        }
    }

    if ($app) {
        $remainingExpectedSidecars = @(
            Get-CimInstance Win32_Process -Filter "Name='python-server.exe'" |
                Where-Object { Test-ExpectedSidecarProcess $_ ([int]$app.Id) }
        )
        foreach ($sidecar in $remainingExpectedSidecars) {
            $remainingPid = [int]$sidecar.ProcessId
            if ($trackedSidecarPids -notcontains $remainingPid) {
                $trackedSidecarPids += $remainingPid
            }
        }
    }
    foreach ($sidecarPid in $trackedSidecarPids) {
        $sidecar = Get-CimInstance Win32_Process -Filter "ProcessId=$sidecarPid" -ErrorAction SilentlyContinue
        if ($sidecar -and $app -and (Test-ExpectedSidecarProcess $sidecar ([int]$app.Id))) {
            Stop-Process -Id $sidecarPid -Force -ErrorAction SilentlyContinue
        }
    }

    if (Test-Path -LiteralPath $testData) {
        $resolvedTestData = [System.IO.Path]::GetFullPath($testData)
        if (-not $resolvedTestData.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove smoke-test data outside the temporary directory"
        }
        Remove-Item -LiteralPath $resolvedTestData -Recurse -Force
    }
}
