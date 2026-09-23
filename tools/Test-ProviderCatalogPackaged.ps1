# Product Atelier Provider Connection + Catalog Sync packaged delta gate.
#
# This gate deliberately copies the current legacy credential into an isolated
# config so the packaged sidecar must perform the real migration sequence:
# Credential Manager write -> read-back -> read-only handshake -> plaintext
# removal.  The source config is never modified.  No generation endpoint is
# called by this script.
param(
    [string]$PortableDir = "",
    [int]$TimeoutSeconds = 60,
    [int]$SyncTimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $PortableDir) {
    $PortableDir = Join-Path $ProjectRoot "build\portable-candidate-current"
}
$PortableDir = [System.IO.Path]::GetFullPath($PortableDir)
$AppExe = Join-Path $PortableDir "Product Atelier.exe"
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    $AppExe = Join-Path $PortableDir "product-atelier.exe"
}
$SidecarExe = Join-Path $PortableDir "python-server\python-server.exe"
if (-not (Test-Path -LiteralPath $AppExe -PathType Leaf)) {
    throw "Packaged app is missing: $AppExe"
}
if (-not (Test-Path -LiteralPath $SidecarExe -PathType Leaf)) {
    throw "Packaged sidecar is missing: $SidecarExe"
}

$runId = [guid]::NewGuid().ToString("N")
$systemTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$testRoot = Join-Path $systemTempRoot ("ProductAtelier-app-test-provider-catalog-" + $runId)
$knowledgeRoot = Join-Path $testRoot "no-knowledge-vault"
$outputRoot = Join-Path $testRoot "output"
$webViewRoot = Join-Path $testRoot "webview2-user-data"
$externalLegacySentinel = Join-Path $testRoot "no-legacy-config.json"
New-Item -ItemType Directory -Path $testRoot, $knowledgeRoot, $webViewRoot -Force | Out-Null

function Get-LegacyCredential {
    $profileRoot = [Environment]::GetFolderPath("UserProfile")
    $sources = @(
        (Join-Path $env:APPDATA "ProductAtelier\config.json"),
        (Join-Path $profileRoot ".codex\skills\lk-ai-image\config.json")
    )
    foreach ($path in $sources) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        try {
            $payload = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            $value = ([string]$payload.api_key).Trim()
            if ($value) { return $value }
        } catch {
            # A malformed/non-credential config is not a viable migration source.
        }
    }
    throw "Legacy provider credential was not found"
}

function Test-ExpectedSidecarProcess($Process, [int]$ExpectedParentId) {
    if (-not $Process -or [int]$Process.ParentProcessId -ne $ExpectedParentId) { return $false }
    if (-not $Process.ExecutablePath) { return $false }
    try { $processPath = [System.IO.Path]::GetFullPath([string]$Process.ExecutablePath) }
    catch { return $false }
    return [string]::Equals(
        $processPath,
        [System.IO.Path]::GetFullPath($SidecarExe),
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Stop-GateRuntime($Runtime) {
    if (-not $Runtime) { return }
    $app = $Runtime.App
    if ($app) {
        $app.Refresh()
        if (-not $app.HasExited) {
            [void]$app.CloseMainWindow()
            try { Wait-Process -Id $app.Id -Timeout 5 -ErrorAction Stop }
            catch { Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue }
        }
    }
    if ($Runtime.SidecarPid) {
        $sidecar = Get-CimInstance Win32_Process -Filter "ProcessId=$($Runtime.SidecarPid)" -ErrorAction SilentlyContinue
        if ($sidecar -and $app -and (Test-ExpectedSidecarProcess $sidecar ([int]$app.Id))) {
            Stop-Process -Id ([int]$Runtime.SidecarPid) -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-GateRuntime {
    $stderrPath = Join-Path $testRoot ("tauri-stderr-" + [guid]::NewGuid().ToString("N") + ".log")
    $stdoutPath = Join-Path $testRoot ("tauri-stdout-" + [guid]::NewGuid().ToString("N") + ".log")
    $app = Start-Process `
        -FilePath $AppExe `
        -WorkingDirectory $PortableDir `
        -WindowStyle Hidden `
        -RedirectStandardError $stderrPath `
        -RedirectStandardOutput $stdoutPath `
        -PassThru
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $health = $null
    $sidecarPid = $null
    $port = $null
    while ((Get-Date) -lt $deadline) {
        $app.Refresh()
        if ($app.HasExited) {
            $stderr = ""
            if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
                $stderr = (Get-Content -LiteralPath $stderrPath -Raw).Trim()
            }
            if ($stderr.Length -gt 800) { $stderr = $stderr.Substring(0, 800) }
            throw "Packaged app exited before sidecar health (code=$($app.ExitCode)): $stderr"
        }
        $sidecars = @(
            Get-CimInstance Win32_Process -Filter "Name='python-server.exe'" |
                Where-Object { Test-ExpectedSidecarProcess $_ ([int]$app.Id) }
        )
        if ($sidecars.Count -eq 1) {
            $sidecarPid = [int]$sidecars[0].ProcessId
            $match = [regex]::Match([string]$sidecars[0].CommandLine, '(\d+)\s*$')
            if ($match.Success) {
                $port = [int]$match.Groups[1].Value
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3
                    break
                } catch {
                    # The process exists briefly before FastAPI accepts requests.
                }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $health) {
        Stop-GateRuntime @{ App = $app; SidecarPid = $sidecarPid }
        throw "Packaged app sidecar did not become healthy"
    }
    return @{ App = $app; SidecarPid = $sidecarPid; Port = $port; Health = $health }
}

function Set-CatalogApiBase([string]$DatabasePath, [string]$ApiBase) {
    & python -c @"
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
try:
    connection.execute(
        "UPDATE provider_connections SET api_base=? WHERE id=?",
        (sys.argv[2], "provider_lk_primary"),
    )
    connection.commit()
finally:
    connection.close()
"@ $DatabasePath $ApiBase
    if ($LASTEXITCODE -ne 0) { throw "Could not update isolated catalog API base" }
}

function Remove-IsolatedPlaintext([string]$ConfigPath) {
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { return }
    try {
        $payload = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        if ($payload.PSObject.Properties.Name -notcontains "api_key") { return }
        $clean = [ordered]@{}
        foreach ($property in $payload.PSObject.Properties) {
            if ($property.Name -ne "api_key") { $clean[$property.Name] = $property.Value }
        }
        $clean | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ConfigPath -Encoding utf8
    } catch {
        # Never echo the config body or credential in a cleanup error.
        throw "Could not scrub the isolated legacy credential copy"
    }
}

$apiKey = Get-LegacyCredential
$configPath = Join-Path $testRoot "config.json"
[ordered]@{
    api_key = $apiKey
    knowledge_base_path = $knowledgeRoot
    output_root = $outputRoot
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding utf8

$credentialNamespace = "ProductAtelier-Packaged-Gate-$runId"
$secretRef = "$credentialNamespace/provider/lk-ai-model-center/provider_lk_primary"
$oldDataDir = $env:PRODUCT_ATELIER_DATA_DIR
$oldWebViewDataDir = $env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR
$oldWebViewUserData = $env:WEBVIEW2_USER_DATA_FOLDER
$oldKnowledgeBase = $env:PRODUCT_ATELIER_KNOWLEDGE_BASE
$oldLegacyConfig = $env:PRODUCT_ATELIER_LEGACY_CONFIG
$oldCredentialNamespace = $env:PRODUCT_ATELIER_CREDENTIAL_NAMESPACE
$oldCandidateIsolation = $env:PRODUCT_ATELIER_CANDIDATE_ISOLATION
$runtime = $null
$passed = $false

try {
    $env:PRODUCT_ATELIER_DATA_DIR = $testRoot
    $env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR = $webViewRoot
    $env:WEBVIEW2_USER_DATA_FOLDER = $webViewRoot
    $env:PRODUCT_ATELIER_KNOWLEDGE_BASE = $knowledgeRoot
    $env:PRODUCT_ATELIER_LEGACY_CONFIG = $externalLegacySentinel
    $env:PRODUCT_ATELIER_CREDENTIAL_NAMESPACE = $credentialNamespace
    $env:PRODUCT_ATELIER_CANDIDATE_ISOLATION = "1"

    $first = Start-GateRuntime
    $runtime = $first
    $baseUrl = "http://127.0.0.1:$($first.Port)"
    if (-not $first.Health.api_key_configured) {
        throw "Packaged sidecar did not import the isolated credential"
    }
    $before = Invoke-RestMethod -Uri "$baseUrl/api/provider-connections" -TimeoutSec 10
    if (-not $before.connections[0].credential_configured) {
        throw "Credential fingerprint is missing after write/read verification"
    }
    $beforeConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if (-not ([string]$beforeConfig.api_key).Trim()) {
        throw "Plaintext was removed before the read-only handshake"
    }

    $sync = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/provider-connections/provider_lk_primary/sync" `
        -TimeoutSec $SyncTimeoutSeconds
    if (
        $sync.catalog_status -ne "fresh" -or
        -not $sync.active_snapshot_id -or
        -not $sync.last_handshake_at
    ) { throw "Catalog sync did not produce a fresh handshake" }
    $afterConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($afterConfig.PSObject.Properties.Name -contains "api_key") {
        throw "Plaintext remained after the successful handshake"
    }

    $catalog = Invoke-RestMethod `
        -Uri "$baseUrl/api/provider-connections/provider_lk_primary/catalog" `
        -TimeoutSec 30
    $snapshotId = [string]$catalog.snapshot.id
    $rawHash = [string]$catalog.snapshot.raw_catalog_sha256
    $normalizedHash = [string]$catalog.snapshot.normalized_catalog_sha256
    if (
        -not $snapshotId -or
        $catalog.snapshot.endpoint_status.generation_calls -ne 0 -or
        $catalog.snapshot.endpoint_status.status -ne "complete"
    ) { throw "Catalog receipt is incomplete" }
    $publicJson = @($before, $sync, $catalog, $first.Health) | ConvertTo-Json -Depth 100 -Compress
    if ($publicJson.Contains($apiKey)) { throw "Credential leaked through an HTTP response" }

    Stop-GateRuntime $runtime
    $runtime = $null
    $second = Start-GateRuntime
    $runtime = $second
    $baseUrl = "http://127.0.0.1:$($second.Port)"
    if (-not $second.Health.api_key_configured) {
        throw "Packaged restart could not reread Windows Credential Manager"
    }
    $restartCatalog = Invoke-RestMethod `
        -Uri "$baseUrl/api/provider-connections/provider_lk_primary/catalog" `
        -TimeoutSec 30
    if (
        [string]$restartCatalog.snapshot.id -ne $snapshotId -or
        [string]$restartCatalog.snapshot.raw_catalog_sha256 -ne $rawHash
    ) { throw "Catalog snapshot changed after the Credential Manager restart" }

    $catalogDatabase = Join-Path $testRoot "provider-catalog.sqlite3"
    Set-CatalogApiBase $catalogDatabase "https://127.0.0.1:1"
    $failureBody = ""
    try {
        Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUrl/api/provider-connections/provider_lk_primary/sync" `
            -TimeoutSec 30 | Out-Null
        throw "Forced catalog failure unexpectedly succeeded"
    } catch {
        if ($_.Exception.Message -eq "Forced catalog failure unexpectedly succeeded") { throw }
        $failureBody = [string]$_.ErrorDetails.Message
    }
    if ($failureBody.Contains($apiKey)) { throw "Credential leaked through the sync failure" }
    $stale = Invoke-RestMethod `
        -Uri "$baseUrl/api/provider-connections/provider_lk_primary/catalog" `
        -TimeoutSec 30
    if ($stale.connection.catalog_status -ne "stale") {
        throw "Catalog failure did not mark the connection stale"
    }
    if (
        [string]$stale.snapshot.id -ne $snapshotId -or
        [string]$stale.snapshot.raw_catalog_sha256 -ne $rawHash
    ) { throw "Stale fallback did not preserve the active snapshot" }
    Set-CatalogApiBase $catalogDatabase "https://api.lk888.ai/api"

    Stop-GateRuntime $runtime
    $runtime = $null
    $leakFiles = @()
    Get-ChildItem -LiteralPath $testRoot -File -Recurse | ForEach-Object {
        $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
        $utf8 = [Text.Encoding]::UTF8.GetString($bytes)
        $utf16 = [Text.Encoding]::Unicode.GetString($bytes)
        if ($utf8.Contains($apiKey) -or $utf16.Contains($apiKey)) {
            $leakFiles += $_.FullName
        }
    }
    if ($leakFiles.Count -gt 0) { throw "Credential persisted in candidate data files" }

    $counts = $catalog.snapshot.normalized_catalog.counts
    $account = $catalog.snapshot.normalized_catalog.account
    $summary = [ordered]@{
        gate = "passed"
        data_root = $testRoot
        contract_version = $first.Health.service.contract_version
        git_commit = $first.Health.service.git_commit
        ledger_schema_version = $first.Health.ledger.schema_version
        catalog_store_schema_version = 1
        credential_manager_restart_read = $true
        plaintext_retained_before_handshake = $true
        plaintext_removed_after_handshake = $true
        public_response_secret_scan = "clean"
        persisted_file_secret_scan = "clean"
        generation_calls = 0
        snapshot_id = $snapshotId
        snapshot_version = $catalog.snapshot.version
        fetched_at = $catalog.snapshot.fetched_at
        raw_catalog_sha256 = $rawHash
        normalized_catalog_sha256 = $normalizedHash
        counts = $counts
        balance_fields = @($account.balance.PSObject.Properties.Name | Sort-Object)
        usage_fields = @($account.usage.PSObject.Properties.Name | Sort-Object)
        stale_fallback_preserved_snapshot = $true
    }
    $evidencePath = Join-Path $testRoot "packaged-provider-catalog-gate.json"
    $summary | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $evidencePath -Encoding utf8
    $passed = $true
    $summary | ConvertTo-Json -Depth 20
} finally {
    Stop-GateRuntime $runtime
    $env:PRODUCT_ATELIER_DATA_DIR = $oldDataDir
    $env:PRODUCT_ATELIER_WEBVIEW_DATA_DIR = $oldWebViewDataDir
    $env:WEBVIEW2_USER_DATA_FOLDER = $oldWebViewUserData
    $env:PRODUCT_ATELIER_KNOWLEDGE_BASE = $oldKnowledgeBase
    $env:PRODUCT_ATELIER_LEGACY_CONFIG = $oldLegacyConfig
    $env:PRODUCT_ATELIER_CREDENTIAL_NAMESPACE = $oldCredentialNamespace
    $env:PRODUCT_ATELIER_CANDIDATE_ISOLATION = $oldCandidateIsolation
    & python -c @"
from python.credential_store import WindowsCredentialStore
import sys
WindowsCredentialStore(namespace=sys.argv[1]).delete(sys.argv[2])
"@ $credentialNamespace $secretRef 2>$null | Out-Null
    Remove-IsolatedPlaintext $configPath
    $apiKey = $null
    if (-not $passed) {
        Write-Host "Packaged gate evidence retained at: $testRoot" -ForegroundColor Yellow
    }
}
