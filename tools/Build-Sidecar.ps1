# Product Atelier sidecar build and deployment.
# Rebuilds from current source, writes a verifiable manifest, and replaces only
# the two project-scoped sidecar directories after the staged build succeeds.
param(
    [switch]$DeployPortable
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build\sidecar-current"))
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$StagedDir = Join-Path $DistRoot "python-server"
$SourceDestination = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "src-tauri\bin\python-server"))
$PortableDestination = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "release\ProductAtelier-Portable\python-server"))

function Assert-ProjectPath([string]$PathToCheck) {
    $full = [System.IO.Path]::GetFullPath($PathToCheck)
    $prefix = $ProjectRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the project: $full"
    }
}

function Replace-Directory([string]$Source, [string]$Destination) {
    Assert-ProjectPath $Destination
    if (-not (Test-Path -LiteralPath (Join-Path $Source "python-server.exe"))) {
        throw "Staged sidecar executable is missing: $Source"
    }
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $replacement = "$Destination.replacement"
    Assert-ProjectPath $replacement
    if (Test-Path -LiteralPath $replacement) {
        Remove-Item -LiteralPath $replacement -Recurse -Force
    }
    Copy-Item -LiteralPath $Source -Destination $replacement -Recurse -Force
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    Move-Item -LiteralPath $replacement -Destination $Destination
}

Assert-ProjectPath $BuildRoot
if (Test-Path -LiteralPath $BuildRoot) {
    Remove-Item -LiteralPath $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null

Push-Location $ProjectRoot
try {
    python -m PyInstaller python-server.spec --distpath $DistRoot --workpath $WorkRoot --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller sidecar build failed" }

    $contractMatch = Select-String -LiteralPath "python\server.py" -Pattern '^SIDECAR_CONTRACT_VERSION = "([^"]+)"$'
    if (-not $contractMatch) { throw "SIDECAR_CONTRACT_VERSION is missing from python/server.py" }
    $contractVersion = $contractMatch.Matches[0].Groups[1].Value
    $schemaMatch = Select-String -LiteralPath "python\atelier_ledger.py" -Pattern '^SCHEMA_VERSION = ([0-9]+)$'
    if (-not $schemaMatch) { throw "SCHEMA_VERSION is missing from python/atelier_ledger.py" }
    $ledgerSchemaVersion = [int]$schemaMatch.Matches[0].Groups[1].Value

    $sourceFiles = @(
        "python/server.py",
        "python/atelier_ledger.py",
        "python/asset_store.py",
        "python/job_engine.py",
        "python/knowledge_engine.py",
        "python/memory_engine.py",
        "python/storage_paths.py",
        "python-server.spec"
    )
    $sourceHashes = [ordered]@{}
    foreach ($relativePath in $sourceFiles) {
        $absolutePath = Join-Path $ProjectRoot $relativePath
        $sourceHashes[$relativePath] = (Get-FileHash -Algorithm SHA256 -LiteralPath $absolutePath).Hash
    }
    $fingerprintText = ($sourceHashes.GetEnumerator() | ForEach-Object { "$($_.Key):$($_.Value)" }) -join "`n"
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $fingerprintBytes = [System.Text.Encoding]::UTF8.GetBytes($fingerprintText)
        $sourceFingerprint = [System.BitConverter]::ToString($sha256.ComputeHash($fingerprintBytes)).Replace("-", "")
    } finally {
        $sha256.Dispose()
    }

    $gitCommit = (git rev-parse HEAD).Trim()
    $manifest = [ordered]@{
        product = "Product Atelier"
        contract_version = $contractVersion
        ledger_schema_version = $ledgerSchemaVersion
        git_commit = $gitCommit
        source_fingerprint = $sourceFingerprint
        built_at = (Get-Date).ToUniversalTime().ToString("o")
        executable_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $StagedDir "python-server.exe")).Hash
        source_hashes = $sourceHashes
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $StagedDir "sidecar-manifest.json") -Encoding utf8

    Replace-Directory $StagedDir $SourceDestination
    if ($DeployPortable) {
        Replace-Directory $StagedDir $PortableDestination
    }
} finally {
    Pop-Location
}

Write-Host "Sidecar rebuilt and verified manifest created." -ForegroundColor Green
Write-Host "Source bundle: $SourceDestination"
if ($DeployPortable) { Write-Host "Portable bundle: $PortableDestination" }
