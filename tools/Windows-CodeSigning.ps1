# Product Atelier Windows Authenticode helper.
#
# The certificate stays in the Windows certificate store or hardware provider.
# This script never accepts a PFX path or password and never writes credentials.
param(
    [ValidateSet("Preflight", "PrepareTauriConfig", "Sign", "Verify")]
    [string]$Mode = "Preflight",
    [string[]]$ArtifactPath = @(),
    [string]$CertificateThumbprint = $env:PRODUCT_ATELIER_SIGN_CERT_SHA1,
    [string]$TimestampUrl = $env:PRODUCT_ATELIER_SIGN_TIMESTAMP_URL,
    [string]$SignToolPath = $env:PRODUCT_ATELIER_SIGNTOOL_PATH,
    [string]$TauriConfigPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$BuildRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "build"))
$CodeSigningEku = "1.3.6.1.5.5.7.3.3"

function Normalize-Thumbprint([string]$Value) {
    $normalized = ([regex]::Replace(([string]$Value), '\s', '')).ToUpperInvariant()
    if ($normalized -notmatch '^[0-9A-F]{40}$') {
        throw "PRODUCT_ATELIER_SIGN_CERT_SHA1 must be a 40-character SHA-1 certificate thumbprint."
    }
    return $normalized
}

function Assert-TimestampUrl([string]$Value) {
    $parsed = $null
    if (-not [System.Uri]::TryCreate($Value, [System.UriKind]::Absolute, [ref]$parsed)) {
        throw "PRODUCT_ATELIER_SIGN_TIMESTAMP_URL must be an absolute HTTPS URL."
    }
    if ($parsed.Scheme -ne [System.Uri]::UriSchemeHttps) {
        throw "The timestamp service must use HTTPS."
    }
    return $parsed.AbsoluteUri
}

function Resolve-SignTool([string]$ExplicitPath) {
    if ($ExplicitPath) {
        $resolved = [System.IO.Path]::GetFullPath($ExplicitPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Configured signtool.exe does not exist: $resolved"
        }
        return $resolved
    }

    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return [System.IO.Path]::GetFullPath($command.Source)
    }

    $programFilesX86 = ${env:ProgramFiles(x86)}
    if ($programFilesX86) {
        $kitsRoot = Join-Path $programFilesX86 "Windows Kits\10\bin"
        if (Test-Path -LiteralPath $kitsRoot -PathType Container) {
            $candidates = @()
            foreach ($versionDirectory in @(Get-ChildItem -LiteralPath $kitsRoot -Directory)) {
                foreach ($architecture in @("x64", "x86")) {
                    $candidate = Join-Path $versionDirectory.FullName "$architecture\signtool.exe"
                    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                        $candidates += Get-Item -LiteralPath $candidate
                    }
                }
            }
            $selected = $candidates | Sort-Object FullName -Descending | Select-Object -First 1
            if ($selected) {
                return [System.IO.Path]::GetFullPath($selected.FullName)
            }
        }
    }
    throw "signtool.exe is unavailable. Install the Windows SDK signing tools or set PRODUCT_ATELIER_SIGNTOOL_PATH."
}

function Find-SigningCertificate([string]$Thumbprint) {
    $matches = @()
    foreach ($location in @("CurrentUser", "LocalMachine")) {
        $path = "Cert:\$location\My\$Thumbprint"
        $certificate = Get-Item -LiteralPath $path -ErrorAction SilentlyContinue
        if ($certificate) {
            $matches += [pscustomobject]@{
                Certificate = $certificate
                Location = $location
            }
        }
    }
    if ($matches.Count -eq 0) {
        throw "The configured code-signing certificate was not found in CurrentUser/My or LocalMachine/My."
    }
    if ($matches.Count -gt 1) {
        throw "The configured certificate exists in more than one store; keep one unambiguous signing identity."
    }

    $match = $matches[0]
    $certificate = $match.Certificate
    $now = Get-Date
    if (-not $certificate.HasPrivateKey) {
        throw "The configured certificate has no accessible private key."
    }
    if ($now -lt $certificate.NotBefore -or $now -gt $certificate.NotAfter) {
        throw "The configured certificate is not currently valid."
    }
    $hasCodeSigningEku = $false
    foreach ($extension in $certificate.Extensions) {
        if ($extension -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]) {
            foreach ($oid in $extension.EnhancedKeyUsages) {
                if ($oid.Value -eq $CodeSigningEku) {
                    $hasCodeSigningEku = $true
                }
            }
        }
    }
    if (-not $hasCodeSigningEku) {
        throw "The configured certificate is not valid for code signing."
    }
    return $match
}

function Resolve-Artifact([string]$Value) {
    if (-not $Value) { throw "An explicit artifact path is required." }
    $resolved = [System.IO.Path]::GetFullPath($Value)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Signing artifact is missing: $resolved"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Signing artifacts may not be reparse points: $resolved"
    }
    if ($item.Extension.ToLowerInvariant() -notin @(".exe", ".dll", ".msi")) {
        throw "Unsupported Authenticode artifact type: $resolved"
    }
    return $resolved
}

function Invoke-SignTool([string]$Tool, [string[]]$Arguments, [string]$FailureMessage) {
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (signtool exit code $LASTEXITCODE)"
    }
}

function Assert-ArtifactSignature([string]$Tool, [string]$Path, [string]$Thumbprint) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ([string]$signature.Status -ne "Valid") {
        throw "Authenticode signature is not valid for $Path (status: $($signature.Status))."
    }
    if (-not $signature.SignerCertificate) {
        throw "Authenticode signer certificate is missing for $Path"
    }
    $actualThumbprint = Normalize-Thumbprint ([string]$signature.SignerCertificate.Thumbprint)
    if ($actualThumbprint -ne $Thumbprint) {
        throw "Authenticode signer identity does not match the configured certificate for $Path"
    }
    if (-not $signature.TimeStamperCertificate) {
        throw "Authenticode signature has no trusted timestamp for $Path"
    }
    Invoke-SignTool $Tool @("verify", "/pa", "/all", "/v", $Path) "Authenticode policy verification failed for $Path"
}

$thumbprint = Normalize-Thumbprint $CertificateThumbprint
$signTool = Resolve-SignTool $SignToolPath

if ($Mode -in @("Preflight", "PrepareTauriConfig", "Sign")) {
    $timestamp = Assert-TimestampUrl $TimestampUrl
    $certificateMatch = Find-SigningCertificate $thumbprint
} else {
    $timestamp = ""
    $certificateMatch = $null
}

if ($Mode -eq "Preflight") {
    Write-Host "Windows code-signing preflight passed." -ForegroundColor Green
    Write-Host "Certificate: $thumbprint"
    Write-Host "Store: $($certificateMatch.Location)/My"
    Write-Host "Timestamp: $timestamp"
    Write-Host "SignTool: $signTool"
    return
}

if ($Mode -eq "PrepareTauriConfig") {
    if (-not $TauriConfigPath) {
        $TauriConfigPath = Join-Path $BuildRoot "tauri-signing.json"
    }
    $resolvedConfig = [System.IO.Path]::GetFullPath($TauriConfigPath)
    $buildPrefix = $BuildRoot.TrimEnd('\') + '\'
    if (-not $resolvedConfig.StartsWith($buildPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The generated Tauri signing config must stay inside the project build directory."
    }
    $parent = Split-Path -Parent $resolvedConfig
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    if (Test-Path -LiteralPath $resolvedConfig) {
        $existing = Get-Item -LiteralPath $resolvedConfig -Force
        if (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "The generated Tauri signing config may not be a reparse point."
        }
    }
    $config = [ordered]@{
        bundle = [ordered]@{
            windows = [ordered]@{
                signCommand = [ordered]@{
                    cmd = "powershell.exe"
                    args = @(
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        $PSCommandPath,
                        "-Mode",
                        "Sign",
                        "-ArtifactPath",
                        "%1"
                    )
                }
            }
        }
    }
    $temporary = "$resolvedConfig.$([guid]::NewGuid().ToString('N')).tmp"
    $config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $resolvedConfig -Force
    Write-Host "Tauri signing config prepared: $resolvedConfig" -ForegroundColor Green
    return
}

if ($ArtifactPath.Count -eq 0) {
    throw "$Mode requires at least one explicit -ArtifactPath."
}
$artifacts = @($ArtifactPath | ForEach-Object { Resolve-Artifact $_ })

if ($Mode -eq "Sign") {
    foreach ($artifact in $artifacts) {
        $arguments = @("sign", "/v", "/fd", "SHA256", "/sha1", $thumbprint, "/s", "My")
        if ($certificateMatch.Location -eq "LocalMachine") {
            $arguments += "/sm"
        }
        $arguments += @("/tr", $timestamp, "/td", "SHA256", $artifact)
        Invoke-SignTool $signTool $arguments "Authenticode signing failed for $artifact"
        Assert-ArtifactSignature $signTool $artifact $thumbprint
    }
    Write-Host "Windows artifacts signed and verified." -ForegroundColor Green
    return
}

foreach ($artifact in $artifacts) {
    Assert-ArtifactSignature $signTool $artifact $thumbprint
}
Write-Host "Windows artifact signatures verified." -ForegroundColor Green
