# Builds and validates the public Windows release path once an external
# Authenticode certificate and timestamp service have been configured.
param(
    [switch]$NoScreenshot
)

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "The signed installer can only be built and validated on Windows."
}

$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$CodeSigningTool = Join-Path $PSScriptRoot "Windows-CodeSigning.ps1"
$InstallerGate = Join-Path $PSScriptRoot "Test-SignedInstaller.ps1"
$TauriSigningConfig = Join-Path $ProjectRoot "build\tauri-signing.json"
$ReleaseDirectory = Join-Path $ProjectRoot "release\ProductAtelier-Installer"
$env:PATH = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:USERPROFILE\.cargo\bin;C:\mingw64\bin;C:\msys64\mingw64\bin;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"

foreach ($path in @($CodeSigningTool, $InstallerGate, (Join-Path $PSScriptRoot "dev.ps1"))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Signed release dependency is missing: $path"
    }
}

& $CodeSigningTool `
    -Mode PrepareTauriConfig `
    -TauriConfigPath $TauriSigningConfig

$portableArguments = @{
    SignArtifacts = $true
}
if ($NoScreenshot) {
    $portableArguments["NoScreenshot"] = $true
}
& "$PSScriptRoot\dev.ps1" @portableArguments

Push-Location $ProjectRoot
try {
    & npx.cmd tauri build `
        --features custom-protocol `
        --config $TauriSigningConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Signed Tauri NSIS build failed"
    }
} finally {
    Pop-Location
}

$gitCommit = ((& git.exe -C $ProjectRoot rev-parse --verify HEAD) -join "`n").Trim()
if ($LASTEXITCODE -ne 0 -or $gitCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Could not resolve the signed build Git identity"
}
$shortCommit = $gitCommit.Substring(0, 7).ToLowerInvariant()
$sourceApp = Join-Path $env:CARGO_TARGET_DIR "release\product-atelier.exe"
$sourceSidecar = Join-Path $ProjectRoot "src-tauri\bin\python-server\python-server.exe"
$installerCandidate = Join-Path $env:CARGO_TARGET_DIR "release\bundle\nsis\Product Atelier_1.0.0_x64-setup.exe"
foreach ($artifact in @($sourceApp, $sourceSidecar, $installerCandidate)) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Signed release artifact is missing: $artifact"
    }
}

& $CodeSigningTool `
    -Mode Verify `
    -ArtifactPath @($sourceApp, $sourceSidecar, $installerCandidate)
& $InstallerGate `
    -InstallerPath $installerCandidate `
    -ExpectedGitCommit $gitCommit

New-Item -ItemType Directory -Path $ReleaseDirectory -Force | Out-Null
$destination = Join-Path $ReleaseDirectory "Product Atelier_1.0.0_x64-setup-$shortCommit-signed.exe"
if (Test-Path -LiteralPath $destination) {
    throw "Refusing to overwrite an existing signed installer: $destination"
}
$temporary = Join-Path $ReleaseDirectory (".signed-installer-$([guid]::NewGuid().ToString('N')).tmp")
try {
    Copy-Item -LiteralPath $installerCandidate -Destination $temporary
    $sourceHash = (Get-FileHash -LiteralPath $installerCandidate -Algorithm SHA256).Hash
    $temporaryHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
    if ($sourceHash -ne $temporaryHash) {
        throw "Signed installer copy hash mismatch"
    }
    Move-Item -LiteralPath $temporary -Destination $destination
} finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Force
    }
}

& $CodeSigningTool -Mode Verify -ArtifactPath $destination
$signature = Get-AuthenticodeSignature -LiteralPath $destination
$finalHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
$finalSize = (Get-Item -LiteralPath $destination).Length

Write-Host "Signed Product Atelier installer passed build, signature, isolated install, runtime, schema, uninstall, and restoration gates." -ForegroundColor Green
Write-Host "Installer: $destination"
Write-Host "Bytes: $finalSize"
Write-Host "SHA-256: $finalHash"
Write-Host "Signer: $($signature.SignerCertificate.Subject)"
Write-Host "Timestamp: $($signature.TimeStamperCertificate.Subject)"
Write-Host "Git commit: $gitCommit"
