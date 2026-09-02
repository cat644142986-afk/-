# Product Atelier sidecar build.
# Rebuilds from current source, writes a verifiable manifest, and replaces only
# the Tauri resource directory after the staged build succeeds. Formal portable
# releases are promoted only by tools/dev.ps1 after candidate smoke tests.

param(
    [switch]$SignArtifacts
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build\sidecar-current"))
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$StagedDir = Join-Path $DistRoot "python-server"
$SourceDestination = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "src-tauri\bin\python-server"))
$SidecarBuildLockPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build\sidecar-build.lock"))
$CodeSigningTool = Join-Path $PSScriptRoot "Windows-CodeSigning.ps1"

function Assert-ProjectPath([string]$PathToCheck) {
    $full = [System.IO.Path]::GetFullPath($PathToCheck)
    $prefix = $ProjectRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside the project: $full"
    }
}

function Assert-NoProjectReparsePoints(
    [string]$PathToCheck,
    [string]$Label,
    [switch]$InspectTree
) {
    Assert-ProjectPath $PathToCheck
    $full = [System.IO.Path]::GetFullPath($PathToCheck)
    $rootItem = Get-Item -LiteralPath $ProjectRoot -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Project root may not be a reparse point: $ProjectRoot"
    }

    $rootWithoutSlash = $ProjectRoot.TrimEnd('\')
    $relative = $full.Substring($rootWithoutSlash.Length).TrimStart('\')
    $current = $ProjectRoot
    foreach ($segment in @($relative -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($segment)) { continue }
        $current = Join-Path $current $segment
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label contains a reparse point: $current"
            }
        }
    }

    if (-not $InspectTree -or -not (Test-Path -LiteralPath $full)) { return }
    $treeRoot = Get-Item -LiteralPath $full -Force
    if (-not $treeRoot.PSIsContainer) {
        throw "$Label must be a directory: $full"
    }
    $pending = New-Object System.Collections.Stack
    $pending.Push($full)
    while ($pending.Count -gt 0) {
        $directory = [string]$pending.Pop()
        $children = @(Get-ChildItem -LiteralPath $directory -Force)
        foreach ($child in $children) {
            if (($child.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label contains a reparse point: $($child.FullName)"
            }
            if ($child.PSIsContainer) {
                $pending.Push($child.FullName)
            }
        }
    }
}

function Replace-Directory([string]$Source, [string]$Destination) {
    Assert-ProjectPath $Destination
    Assert-NoProjectReparsePoints $Source "Staged sidecar tree" -InspectTree
    Assert-NoProjectReparsePoints $Destination "Sidecar destination" -InspectTree
    if (-not (Test-Path -LiteralPath (Join-Path $Source "python-server.exe"))) {
        throw "Staged sidecar executable is missing: $Source"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Source "sidecar-manifest.json"))) {
        throw "Staged sidecar manifest is missing: $Source"
    }
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $token = [guid]::NewGuid().ToString("N")
    $replacement = "$Destination.replacement-$token"
    $previous = "$Destination.previous-$token"
    Assert-ProjectPath $replacement
    Assert-ProjectPath $previous
    Assert-NoProjectReparsePoints $replacement "Sidecar replacement"
    Assert-NoProjectReparsePoints $previous "Previous sidecar directory"
    $destinationMoved = $false
    $replacementMoved = $false
    try {
        Copy-Item -LiteralPath $Source -Destination $replacement -Recurse -Force
        Assert-NoProjectReparsePoints $replacement "Sidecar replacement" -InspectTree
        if (Test-Path -LiteralPath $Destination) {
            Assert-NoProjectReparsePoints $Destination "Sidecar destination" -InspectTree
            Move-Item -LiteralPath $Destination -Destination $previous
            $destinationMoved = $true
        }
        Move-Item -LiteralPath $replacement -Destination $Destination
        $replacementMoved = $true
        if ($destinationMoved -and (Test-Path -LiteralPath $previous)) {
            Assert-NoProjectReparsePoints $previous "Previous sidecar directory" -InspectTree
            Remove-Item -LiteralPath $previous -Recurse -Force
        }
    } catch {
        if (-not $replacementMoved -and $destinationMoved -and
            (Test-Path -LiteralPath $previous) -and
            -not (Test-Path -LiteralPath $Destination)) {
            Assert-NoProjectReparsePoints $previous "Previous sidecar directory" -InspectTree
            Move-Item -LiteralPath $previous -Destination $Destination
        }
        throw
    } finally {
        if (Test-Path -LiteralPath $replacement) {
            Assert-NoProjectReparsePoints $replacement "Sidecar replacement" -InspectTree
            Remove-Item -LiteralPath $replacement -Recurse -Force
        }
    }
}

$sidecarBuildLock = $null
$sidecarBuildLockOwned = $false
try {
    Assert-NoProjectReparsePoints $BuildRoot "Sidecar build root" -InspectTree
    Assert-NoProjectReparsePoints $SourceDestination "Sidecar destination" -InspectTree
    New-Item -ItemType Directory -Path (Split-Path -Parent $SidecarBuildLockPath) -Force | Out-Null
    Assert-NoProjectReparsePoints $BuildRoot "Sidecar build root" -InspectTree
    Assert-NoProjectReparsePoints $SourceDestination "Sidecar destination" -InspectTree
    if (Test-Path -LiteralPath $SidecarBuildLockPath) {
        $lockItem = Get-Item -LiteralPath $SidecarBuildLockPath -Force
        if (($lockItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Sidecar build lock may not be a reparse point: $SidecarBuildLockPath"
        }
    }
    try {
        $sidecarBuildLock = [System.IO.File]::Open(
            $SidecarBuildLockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        $sidecarBuildLockOwned = $true
    } catch {
        throw "Another sidecar build owns $SidecarBuildLockPath"
    }

    Assert-NoProjectReparsePoints $BuildRoot "Sidecar build root" -InspectTree
    Assert-NoProjectReparsePoints $SourceDestination "Sidecar destination" -InspectTree
    if (Test-Path -LiteralPath $BuildRoot) {
        Remove-Item -LiteralPath $BuildRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null

    Push-Location $ProjectRoot
    try {
        python -m PyInstaller python-server.spec --distpath $DistRoot --workpath $WorkRoot --noconfirm
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller sidecar build failed" }

        $authenticode = $null
        if ($SignArtifacts) {
            if (-not (Test-Path -LiteralPath $CodeSigningTool -PathType Leaf)) {
                throw "Windows code-signing helper is missing: $CodeSigningTool"
            }
            $stagedExecutable = Join-Path $StagedDir "python-server.exe"
            & $CodeSigningTool -Mode Sign -ArtifactPath $stagedExecutable
            $signature = Get-AuthenticodeSignature -LiteralPath $stagedExecutable
            $authenticode = [ordered]@{
                required = $true
                status = [string]$signature.Status
                certificate_thumbprint = ([string]$signature.SignerCertificate.Thumbprint).ToUpperInvariant()
                timestamped = [bool]$signature.TimeStamperCertificate
                digest_algorithm = "sha256"
            }
        }

        $contractMatch = Select-String -LiteralPath "python\server.py" -Pattern '^SIDECAR_CONTRACT_VERSION = "([^"]+)"$'
        if (-not $contractMatch) { throw "SIDECAR_CONTRACT_VERSION is missing from python/server.py" }
        $contractVersion = $contractMatch.Matches[0].Groups[1].Value
        $schemaMatch = Select-String -LiteralPath "python\atelier_ledger.py" -Pattern '^SCHEMA_VERSION = ([0-9]+)$'
        if (-not $schemaMatch) { throw "SCHEMA_VERSION is missing from python/atelier_ledger.py" }
        $ledgerSchemaVersion = [int]$schemaMatch.Matches[0].Groups[1].Value

        $sourceFiles = @(
            "python/server.py",
            "python/atelier_ledger.py",
            "python/command_registry.py",
            "python/canvas_export.py",
            "python/local_edit_contract.py",
            "python/asset_store.py",
            "python/job_engine.py",
            "python/knowledge_engine.py",
            "python/memory_engine.py",
            "python/model_artifacts.py",
            "python/semantic_cutout.py",
            "python/semantic_grounding.py",
            "python/grounding_runtime.py",
            "python/semantic_query.py",
            "python/semantic_query_lexicon.json",
            "docs/model-artifacts/grounding-dino-tiny.json",
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

        $gitOutput = @(& git.exe -C $ProjectRoot rev-parse --verify HEAD 2>&1)
        $gitExitCode = $LASTEXITCODE
        if ($gitExitCode -ne 0) { throw "Could not resolve Git HEAD: $($gitOutput -join ' ')" }
        $gitCommit = (($gitOutput -join "`n").Trim())
        if ($gitCommit -notmatch '^[0-9a-fA-F]{40}$') {
            throw "Git HEAD is not a full 40-character commit hash"
        }
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
        if ($authenticode) {
            $manifest["authenticode"] = $authenticode
        }
        $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $StagedDir "sidecar-manifest.json") -Encoding utf8

        Replace-Directory $StagedDir $SourceDestination
    } finally {
        Pop-Location
    }
} finally {
    if ($sidecarBuildLockOwned -and $sidecarBuildLock) {
        $sidecarBuildLock.Dispose()
        if (Test-Path -LiteralPath $SidecarBuildLockPath) {
            Remove-Item -LiteralPath $SidecarBuildLockPath -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "Sidecar rebuilt and verified manifest created." -ForegroundColor Green
Write-Host "Source bundle: $SourceDestination"
