# Product Atelier packaged sidecar verification.
param(
    [string]$PortableDir = "",
    [switch]$StaticOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $PortableDir) {
    $PortableDir = Join-Path $ProjectRoot "release\ProductAtelier-Portable"
}
$PortableDir = [System.IO.Path]::GetFullPath($PortableDir)
$SidecarDir = Join-Path $PortableDir "python-server"
$SidecarExe = Join-Path $SidecarDir "python-server.exe"
$ManifestPath = Join-Path $SidecarDir "sidecar-manifest.json"

if (-not (Test-Path -LiteralPath $SidecarExe)) { throw "Portable sidecar is missing: $SidecarExe" }
if (-not (Test-Path -LiteralPath $ManifestPath)) { throw "Sidecar manifest is missing: $ManifestPath" }

$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([int]$manifest.ledger_schema_version -lt 1) {
    throw "Sidecar manifest has no valid ledger schema version"
}
$actualExeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SidecarExe).Hash
if ($actualExeHash -ne $manifest.executable_sha256) {
    throw "Portable sidecar hash does not match its build manifest"
}

foreach ($property in $manifest.source_hashes.PSObject.Properties) {
    $sourcePath = Join-Path $ProjectRoot $property.Name
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Manifest source is missing: $($property.Name)" }
    $actualSourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePath).Hash
    if ($actualSourceHash -ne $property.Value) {
        throw "Portable sidecar is stale relative to source: $($property.Name)"
    }
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
Write-Host "Source fingerprint: $($manifest.source_fingerprint)"
