# Product Atelier packaged sidecar verification.
param(
    [string]$PortableDir = "",
    [string]$ExpectedGitCommit = "",
    [switch]$StaticOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $PortableDir) {
    $PortableDir = Join-Path $ProjectRoot "release\ProductAtelier-Portable"
}
$PortableDir = [System.IO.Path]::GetFullPath($PortableDir)
$AppExe = Join-Path $PortableDir "Product Atelier.exe"
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    $AppExe = Join-Path $PortableDir "product-atelier.exe"
}
$SidecarDir = Join-Path $PortableDir "python-server"
$SidecarExe = Join-Path $SidecarDir "python-server.exe"
$ManifestPath = Join-Path $SidecarDir "sidecar-manifest.json"
$CodeSigningTool = Join-Path $PSScriptRoot "Windows-CodeSigning.ps1"

if (-not $ExpectedGitCommit) {
    $headOutput = @(& git.exe -C $ProjectRoot rev-parse --verify HEAD 2>&1)
    $headExitCode = $LASTEXITCODE
    if ($headExitCode -ne 0) { throw "Could not resolve Git HEAD: $($headOutput -join ' ')" }
    $ExpectedGitCommit = (($headOutput -join "`n").Trim())
}
if ($ExpectedGitCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Expected Git commit must be a full 40-character hash"
}

if (-not (Test-Path -LiteralPath $SidecarExe)) { throw "Portable sidecar is missing: $SidecarExe" }
if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Sidecar manifest is missing: $ManifestPath" }

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if (-not [string]::Equals(
    ([string]$manifest.git_commit).Trim(),
    $ExpectedGitCommit,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Sidecar manifest git_commit does not match the expected Git HEAD"
}
if ([int]$manifest.ledger_schema_version -lt 1) {
    throw "Sidecar manifest has no valid ledger schema version"
}
$actualExeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SidecarExe).Hash
if ($actualExeHash -ne $manifest.executable_sha256) {
    throw "Portable sidecar hash does not match its build manifest"
}
if ($manifest.authenticode -and [bool]$manifest.authenticode.required) {
    if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
        throw "Signed portable app is missing: $AppExe"
    }
    if (-not (Test-Path -LiteralPath $CodeSigningTool -PathType Leaf)) {
        throw "Windows code-signing helper is missing: $CodeSigningTool"
    }
    $expectedSigner = ([string]$manifest.authenticode.certificate_thumbprint).Trim()
    if ($expectedSigner -notmatch '^[0-9A-Fa-f]{40}$') {
        throw "Signed sidecar manifest has an invalid certificate thumbprint"
    }
    & $CodeSigningTool `
        -Mode Verify `
        -ArtifactPath @($AppExe, $SidecarExe) `
        -CertificateThumbprint $expectedSigner
}

$sourceProperties = @($manifest.source_hashes.PSObject.Properties)
if ($sourceProperties.Count -lt 1) {
    throw "Sidecar manifest source_hashes must contain at least one source file"
}
foreach ($property in $sourceProperties) {
    $sourcePath = Join-Path $ProjectRoot $property.Name
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Manifest source is missing: $($property.Name)" }
    $actualSourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash
    if ($actualSourceHash -ne $property.Value) {
        throw "Portable sidecar is stale relative to source: $($property.Name)"
    }
}

$fingerprintText = ($sourceProperties | ForEach-Object {
    "$($_.Name):$($_.Value)"
}) -join "`n"
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $fingerprintBytes = [System.Text.Encoding]::UTF8.GetBytes($fingerprintText)
    $actualSourceFingerprint = [System.BitConverter]::ToString(
        $sha256.ComputeHash($fingerprintBytes)
    ).Replace("-", "")
} finally {
    $sha256.Dispose()
}
if (-not [string]::Equals(
    $actualSourceFingerprint,
    ([string]$manifest.source_fingerprint).Trim(),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Sidecar manifest source_fingerprint does not match source_hashes"
}

if (-not $StaticOnly) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()

    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    $testData = [System.IO.Path]::GetFullPath((Join-Path $tempRoot ("ProductAtelier-sidecar-test-" + [guid]::NewGuid().ToString("N"))))
    if (-not $testData.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Invalid temporary runtime test path"
    }
    New-Item -ItemType Directory -Path $testData | Out-Null

    $previousDataDir = $env:PRODUCT_ATELIER_DATA_DIR
    $process = $null
    try {
        $env:PRODUCT_ATELIER_DATA_DIR = $testData
        $process = Start-Process -FilePath $SidecarExe -ArgumentList $port -WorkingDirectory $SidecarDir -WindowStyle Hidden -PassThru
        $deadline = (Get-Date).AddSeconds(45)
        $health = $null
        while ((Get-Date) -lt $deadline) {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3
                break
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $health) { throw "Packaged sidecar did not become healthy within 45 seconds" }
        if ($health.status -ne "ok") { throw "Packaged sidecar health status is not ok" }
        if ($health.service.contract_version -ne $manifest.contract_version) {
            throw "Running sidecar contract does not match manifest"
        }
        if ($health.service.manifest_status -ne "ok") {
            throw "Running sidecar did not accept its build manifest"
        }
        if (-not [string]::Equals(
            ([string]$health.service.git_commit).Trim(),
            $ExpectedGitCommit,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Running sidecar Git commit does not match the expected Git HEAD"
        }
        if ([int]$health.ledger.schema_version -ne [int]$manifest.ledger_schema_version) {
            throw "Packaged sidecar ledger schema does not match its manifest"
        }
    } finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
        $env:PRODUCT_ATELIER_DATA_DIR = $previousDataDir
        if (Test-Path -LiteralPath $testData) {
            Remove-Item -LiteralPath $testData -Recurse -Force
        }
    }
}

Write-Host "Portable sidecar verification passed." -ForegroundColor Green
Write-Host "Contract: $($manifest.contract_version)"
Write-Host "Ledger schema: v$($manifest.ledger_schema_version)"
Write-Host "Git commit: $ExpectedGitCommit"
Write-Host "Source fingerprint: $($manifest.source_fingerprint)"
