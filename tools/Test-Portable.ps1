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
$CanonicalSourceHashFormat = "sha256-text-lf-v1"
$RawSourceHashFormat = "sha256-raw-v1"

function Get-SourceFileSha256([string]$PathToHash, [string]$HashFormat) {
    if ($HashFormat -eq $RawSourceHashFormat) {
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $PathToHash).Hash
    }
    if ($HashFormat -ne $CanonicalSourceHashFormat) {
        throw "Unsupported sidecar source hash format: $HashFormat"
    }
    $extension = [System.IO.Path]::GetExtension($PathToHash).ToLowerInvariant()
    if ($extension -notin @(".py", ".json", ".spec")) {
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $PathToHash).Hash
    }

    $sourceBytes = [System.IO.File]::ReadAllBytes($PathToHash)
    $normalized = New-Object System.IO.MemoryStream
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        for ($index = 0; $index -lt $sourceBytes.Length; $index++) {
            if (
                $sourceBytes[$index] -eq 13 -and
                $index + 1 -lt $sourceBytes.Length -and
                $sourceBytes[$index + 1] -eq 10
            ) {
                $normalized.WriteByte([byte]10)
                $index++
            } else {
                $normalized.WriteByte($sourceBytes[$index])
            }
        }
        $normalized.Position = 0
        return ([System.BitConverter]::ToString(
            $sha256.ComputeHash($normalized)
        )).Replace("-", "")
    } finally {
        $sha256.Dispose()
        $normalized.Dispose()
    }
}

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
$sourceHashFormatProperty = $manifest.PSObject.Properties["source_hash_format"]
if ($null -eq $sourceHashFormatProperty) {
    $sourceHashFormat = $RawSourceHashFormat
} elseif ($sourceHashFormatProperty.Value -isnot [string]) {
    throw "Unsupported sidecar source hash format: source_hash_format must be a string"
} else {
    $sourceHashFormat = [string]$sourceHashFormatProperty.Value
}
if ($sourceHashFormat -notin @($RawSourceHashFormat, $CanonicalSourceHashFormat)) {
    throw "Unsupported sidecar source hash format: $sourceHashFormat"
}
foreach ($property in $sourceProperties) {
    $sourcePath = Join-Path $ProjectRoot $property.Name
    if (-not (Test-Path -LiteralPath $sourcePath)) { throw "Manifest source is missing: $($property.Name)" }
    $actualSourceHash = Get-SourceFileSha256 `
        -PathToHash $sourcePath `
        -HashFormat $sourceHashFormat
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
    $legacyConfigSentinel = Join-Path $testData "no-legacy-config.json"
    $testKnowledgeBase = Join-Path $testData "no-knowledge-vault"
    New-Item -ItemType Directory -Path $testKnowledgeBase | Out-Null

    $previousDataDir = $env:PRODUCT_ATELIER_DATA_DIR
    $previousLegacyConfig = $env:PRODUCT_ATELIER_LEGACY_CONFIG
    $previousKnowledgeBase = $env:PRODUCT_ATELIER_KNOWLEDGE_BASE
    $process = $null
    try {
        $env:PRODUCT_ATELIER_DATA_DIR = $testData
        $env:PRODUCT_ATELIER_LEGACY_CONFIG = $legacyConfigSentinel
        $env:PRODUCT_ATELIER_KNOWLEDGE_BASE = $testKnowledgeBase
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
        $env:PRODUCT_ATELIER_LEGACY_CONFIG = $previousLegacyConfig
        $env:PRODUCT_ATELIER_KNOWLEDGE_BASE = $previousKnowledgeBase
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
