# Installs a signed NSIS candidate into a unique temporary directory, runs the
# packaged gates, uninstalls it, and restores any pre-existing shortcuts.
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [string]$ExpectedGitCommit = "",
    [string]$CertificateThumbprint = $env:PRODUCT_ATELIER_SIGN_CERT_SHA1,
    [int]$CleanupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$InstallerPath = [System.IO.Path]::GetFullPath($InstallerPath)
$CodeSigningTool = Join-Path $PSScriptRoot "Windows-CodeSigning.ps1"
$SchemaGate = Join-Path $PSScriptRoot "verify_packaged_schema_upgrade.py"

if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
    throw "Signed installer candidate is missing: $InstallerPath"
}
if (-not $ExpectedGitCommit) {
    $ExpectedGitCommit = ((& git.exe -C $ProjectRoot rev-parse --verify HEAD) -join "`n").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve Git HEAD" }
}
if ($ExpectedGitCommit -notmatch '^[0-9a-fA-F]{40}$') {
    throw "Expected Git commit must be a full 40-character hash"
}
if (-not (Test-Path -LiteralPath $CodeSigningTool -PathType Leaf)) {
    throw "Windows code-signing helper is missing: $CodeSigningTool"
}
if (-not (Test-Path -LiteralPath $SchemaGate -PathType Leaf)) {
    throw "Packaged schema gate is missing: $SchemaGate"
}

function Get-ProductUninstallEntries {
    $roots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    return @(
        Get-ItemProperty -Path $roots -ErrorAction SilentlyContinue |
            Where-Object { ([string]$_.DisplayName).Trim() -like "Product Atelier*" }
    )
}

function Test-SamePath([string]$Left, [string]$Right) {
    try {
        $leftFull = [System.IO.Path]::GetFullPath($Left)
        $rightFull = [System.IO.Path]::GetFullPath($Right)
    } catch {
        return $false
    }
    return [string]::Equals($leftFull, $rightFull, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-PathInside([string]$PathToCheck, [string]$Directory) {
    try {
        $fullPath = [System.IO.Path]::GetFullPath($PathToCheck)
        $prefix = [System.IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\'
    } catch {
        return $false
    }
    return $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Stop-TestProcesses([string]$Directory) {
    $expectedApp = Join-Path $Directory "Product Atelier.exe"
    $expectedSidecar = Join-Path $Directory "python-server\python-server.exe"
    foreach ($name in @("Product Atelier.exe", "product-atelier.exe", "python-server.exe")) {
        foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue)) {
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

function Backup-Shortcut([string]$Path, [string]$BackupRoot, [int]$Index) {
    $existed = Test-Path -LiteralPath $Path -PathType Leaf
    $backup = Join-Path $BackupRoot ("shortcut-$Index.lnk")
    if ($existed) {
        Copy-Item -LiteralPath $Path -Destination $backup
    }
    return [pscustomobject]@{
        Path = $Path
        Existed = $existed
        Backup = $backup
    }
}

function Restore-Shortcut($State, [string]$InstallDirectory) {
    if ($State.Existed) {
        if (-not (Test-Path -LiteralPath $State.Backup -PathType Leaf)) {
            throw "Shortcut backup is missing: $($State.Backup)"
        }
        $parent = Split-Path -Parent $State.Path
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -LiteralPath $State.Backup -Destination $State.Path -Force
        return
    }
    if (-not (Test-Path -LiteralPath $State.Path -PathType Leaf)) { return }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($State.Path)
    if (-not (Test-PathInside ([string]$shortcut.TargetPath) $InstallDirectory)) {
        throw "Refusing to remove a shortcut not created for the isolated install: $($State.Path)"
    }
    Remove-Item -LiteralPath $State.Path -Force
}

$existingInstallations = Get-ProductUninstallEntries
if ($existingInstallations.Count -gt 0) {
    throw "A registered Product Atelier installation already exists; refusing an isolated installer test that could overwrite it."
}

& $CodeSigningTool `
    -Mode Verify `
    -ArtifactPath $InstallerPath `
    -CertificateThumbprint $CertificateThumbprint

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$testToken = [guid]::NewGuid().ToString("N")
$installDirectory = [System.IO.Path]::GetFullPath((Join-Path $tempRoot "ProductAtelier-installer-test-$testToken"))
$safetyRoot = [System.IO.Path]::GetFullPath((Join-Path $tempRoot "ProductAtelier-installer-safety-$testToken"))
if (-not (Test-PathInside $installDirectory $tempRoot) -or -not (Test-PathInside $safetyRoot $tempRoot)) {
    throw "Installer test paths must stay inside the system temporary directory."
}
if (Test-Path -LiteralPath $installDirectory) {
    throw "Isolated installer target already exists: $installDirectory"
}
New-Item -ItemType Directory -Path $safetyRoot | Out-Null

$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Product Atelier.lnk"
$programsDirectory = [Environment]::GetFolderPath("Programs")
$programFolder = Join-Path $programsDirectory "Product Atelier"
$programFolderExisted = Test-Path -LiteralPath $programFolder -PathType Container
$shortcutPaths = @(
    $desktopShortcut,
    (Join-Path $programsDirectory "Product Atelier.lnk"),
    (Join-Path $programFolder "Product Atelier.lnk")
)
$shortcutStates = @()
for ($index = 0; $index -lt $shortcutPaths.Count; $index++) {
    $shortcutStates += Backup-Shortcut $shortcutPaths[$index] $safetyRoot $index
}

$installSucceeded = $false
$installAttempted = $false
$uninstallAttempted = $false
$uninstaller = ""
try {
    $installAttempted = $true
    $installProcess = Start-Process `
        -FilePath $InstallerPath `
        -ArgumentList @("/S", "/D=$installDirectory") `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($installProcess.ExitCode -ne 0) {
        throw "NSIS installer returned exit code $($installProcess.ExitCode)"
    }
    $installSucceeded = $true

    $appExe = Join-Path $installDirectory "Product Atelier.exe"
    $sidecarExe = Join-Path $installDirectory "python-server\python-server.exe"
    if (-not (Test-Path -LiteralPath $appExe -PathType Leaf)) {
        throw "Installed application is missing: $appExe"
    }
    if (-not (Test-Path -LiteralPath $sidecarExe -PathType Leaf)) {
        throw "Installed sidecar is missing: $sidecarExe"
    }
    $uninstallers = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter "uninstall*.exe")
    if ($uninstallers.Count -ne 1) {
        throw "Expected exactly one installed NSIS uninstaller, found $($uninstallers.Count)"
    }
    $uninstaller = $uninstallers[0].FullName

    & $CodeSigningTool `
        -Mode Verify `
        -ArtifactPath @($appExe, $sidecarExe, $uninstaller) `
        -CertificateThumbprint $CertificateThumbprint
    & "$PSScriptRoot\Test-Portable.ps1" `
        -PortableDir $installDirectory `
        -ExpectedGitCommit $ExpectedGitCommit
    & "$PSScriptRoot\Test-Portable-App.ps1" `
        -PortableDir $installDirectory `
        -ExpectedGitCommit $ExpectedGitCommit
    & python.exe $SchemaGate --sidecar-dir (Join-Path $installDirectory "python-server")
    if ($LASTEXITCODE -ne 0) {
        throw "Installed schema upgrade and local-edit gate failed"
    }
} finally {
    Stop-TestProcesses $installDirectory
    if (-not $uninstaller -and (Test-Path -LiteralPath $installDirectory -PathType Container)) {
        $cleanupUninstallers = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter "uninstall*.exe")
        if ($cleanupUninstallers.Count -eq 1) {
            $uninstaller = $cleanupUninstallers[0].FullName
        }
    }
    if ($installAttempted -and $uninstaller -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        $uninstallAttempted = $true
        $uninstallProcess = Start-Process `
            -FilePath $uninstaller `
            -ArgumentList "/S" `
            -Wait `
            -PassThru `
            -WindowStyle Hidden
        if ($uninstallProcess.ExitCode -ne 0) {
            throw "NSIS uninstaller returned exit code $($uninstallProcess.ExitCode)"
        }
    }

    foreach ($shortcutState in $shortcutStates) {
        Restore-Shortcut $shortcutState $installDirectory
    }
    if (-not $programFolderExisted -and (Test-Path -LiteralPath $programFolder -PathType Container)) {
        $remainingProgramEntries = @(Get-ChildItem -LiteralPath $programFolder -Force)
        if ($remainingProgramEntries.Count -gt 0) {
            throw "The isolated installer left unexpected Start Menu content: $programFolder"
        }
        Remove-Item -LiteralPath $programFolder -Force
    }

    if ($installAttempted) {
        $deadline = (Get-Date).AddSeconds($CleanupTimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            if (-not (Test-Path -LiteralPath $installDirectory)) { break }
            Start-Sleep -Milliseconds 500
        }
        if (Test-Path -LiteralPath $installDirectory) {
            throw "NSIS uninstall did not remove the isolated install directory: $installDirectory"
        }
        if ((Get-ProductUninstallEntries).Count -gt 0) {
            throw "NSIS uninstall left a Product Atelier registry entry"
        }
        if ($installSucceeded -and -not $uninstallAttempted) {
            throw "The isolated installation succeeded but no uninstaller could be executed"
        }
    }

    if (Test-Path -LiteralPath $safetyRoot) {
        Remove-Item -LiteralPath $safetyRoot -Recurse -Force
    }
}

Write-Host "Signed NSIS installation, packaged gates, uninstall, and shortcut restoration passed." -ForegroundColor Green
Write-Host "Installer SHA-256: $((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash)"
Write-Host "Git commit: $ExpectedGitCommit"
