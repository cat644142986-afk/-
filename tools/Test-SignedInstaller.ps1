# Installs an NSIS candidate into a unique temporary directory, runs the
# packaged gates, uninstalls it, and protects pre-existing shortcuts.
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [string]$ExpectedGitCommit = "",
    [string]$InstallerManifestPath = "",
    [string]$ExpectedInstallerManifestSha256 = "",
    [string]$ExpectedInstalledAppSha256 = "",
    [string]$CertificateThumbprint = $env:PRODUCT_ATELIER_SIGN_CERT_SHA1,
    [ValidateSet("Signed", "UnsignedInternal")]
    [string]$TrustMode = "Signed",
    [switch]$SkipAppSmoke,
    [int]$CleanupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$InstallerPath = [System.IO.Path]::GetFullPath($InstallerPath)
$CodeSigningTool = Join-Path $PSScriptRoot "Windows-CodeSigning.ps1"
$SchemaGate = Join-Path $PSScriptRoot "verify_packaged_schema_upgrade.py"
$BundleIdentityTool = Join-Path $PSScriptRoot "tauri_bundle_app_identity.py"
$TauriConfigPath = Join-Path $ProjectRoot "src-tauri\tauri.conf.json"
$TauriBundleIdentityAlgorithm = "tauri-bundler-v2-bundle-type-marker-v1"

if (-not (Test-Path -LiteralPath $TauriConfigPath -PathType Leaf)) {
    throw "Tauri bundle config is missing: $TauriConfigPath"
}
$tauriConfig = Get-Content -LiteralPath $TauriConfigPath -Raw | ConvertFrom-Json
$ProductName = ([string]$tauriConfig.productName).Trim()
$bundleIdentifier = ([string]$tauriConfig.identifier).Trim()
$identifierParts = @($bundleIdentifier.Split('.'))
if (-not $ProductName -or $identifierParts.Count -lt 2 -or -not $identifierParts[1]) {
    throw "Tauri productName and bundle identifier must define the NSIS registry contract"
}
# Tauri CLI 2.11.4 derives the default NSIS manufacturer from identifier segment 2.
$NsisManufacturer = $identifierParts[1]

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
$ExpectedGitCommit = $ExpectedGitCommit.ToLowerInvariant()
if ($TrustMode -eq "UnsignedInternal") {
    if ($ExpectedInstallerManifestSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "UnsignedInternal requires an explicit installer manifest SHA-256 anchor."
    }
    if ($ExpectedInstalledAppSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        throw "UnsignedInternal requires an explicit installed App SHA-256 anchor."
    }
    $ExpectedInstallerManifestSha256 = $ExpectedInstallerManifestSha256.ToUpperInvariant()
    $ExpectedInstalledAppSha256 = $ExpectedInstalledAppSha256.ToUpperInvariant()
}
if ($TrustMode -eq "Signed" -and -not (Test-Path -LiteralPath $CodeSigningTool -PathType Leaf)) {
    throw "Windows code-signing helper is missing: $CodeSigningTool"
}
if (-not (Test-Path -LiteralPath $SchemaGate -PathType Leaf)) {
    throw "Packaged schema gate is missing: $SchemaGate"
}
if (-not (Test-Path -LiteralPath $BundleIdentityTool -PathType Leaf)) {
    throw "Tauri bundle app identity helper is missing: $BundleIdentityTool"
}
if ($SkipAppSmoke -and $TrustMode -ne "UnsignedInternal") {
    throw "SkipAppSmoke is only allowed for an explicit UnsignedInternal headless preflight"
}

function Get-ProductUninstallEntries {
    $roots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    return @(
        Get-ItemProperty -Path $roots -ErrorAction SilentlyContinue |
            Where-Object { ([string]$_.DisplayName).Trim() -like "$ProductName*" }
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

function Assert-NoReparsePath([string]$PathToCheck, [string]$Label) {
    $current = [System.IO.Path]::GetFullPath($PathToCheck)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label may not contain a reparse point: $current"
            }
        }
        $parent = Split-Path -Parent $current
        if (-not $parent -or (Test-SamePath $parent $current)) {
            break
        }
        $current = $parent
    }
}

function Assert-RegularFile([string]$PathToCheck, [string]$Label) {
    if (-not (Test-Path -LiteralPath $PathToCheck -PathType Leaf)) {
        throw "$Label is missing: $PathToCheck"
    }
    Assert-NoReparsePath -PathToCheck $PathToCheck -Label $Label
    $item = Get-Item -LiteralPath $PathToCheck -Force
    if ($item.PSIsContainer -or $item.Length -le 0) {
        throw "$Label must be a non-empty regular file: $PathToCheck"
    }
    return $item
}

function Assert-UnsignedArtifacts([string[]]$ArtifactPaths) {
    foreach ($artifact in $ArtifactPaths) {
        if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
            throw "Unsigned internal artifact is missing: $artifact"
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $artifact
        if ([string]$signature.Status -ne "NotSigned") {
            throw "UnsignedInternal requires Authenticode NotSigned, got $($signature.Status): $artifact"
        }
    }
}

function Invoke-InstalledNsisIdentityValidation(
    [string]$AppPath,
    [string]$ExpectedSha256
) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(
            & python.exe `
                $BundleIdentityTool `
                --mode installed `
                --app $AppPath `
                --expected-sha256 $ExpectedSha256 `
                2>&1
        )
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $rawJson = (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
    if ($exitCode -ne 0) {
        throw "Installed NSIS app identity validation failed: $rawJson"
    }
    try {
        $data = $rawJson | ConvertFrom-Json
    } catch {
        throw "Installed NSIS app identity validation returned invalid JSON: $rawJson"
    }
    if (
        [string]$data.algorithm_version -cne $TauriBundleIdentityAlgorithm -or
        [string]$data.installed_app_sha256 -cne $ExpectedSha256 -or
        [long]$data.installed_app_size_bytes -le 0 -or
        [long]$data.marker_offset -lt 0 -or
        [long]$data.marker_counts.unknown -ne 0 -or
        [long]$data.marker_counts.nsis -ne 1 -or
        [long]$data.marker_counts.msi -ne 0
    ) {
        throw "Installed NSIS app identity helper returned an invalid result."
    }
    return $data
}

function Get-ShortcutFingerprint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{
            Path = $Path
            Exists = $false
            Sha256 = ""
            TargetPath = ""
            WorkingDirectory = ""
        }
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    return [pscustomobject]@{
        Path = $Path
        Exists = $true
        Sha256 = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
        TargetPath = [string]$shortcut.TargetPath
        WorkingDirectory = [string]$shortcut.WorkingDirectory
    }
}

function Get-ShortcutFingerprintViolation($Before, [string]$Phase) {
    $after = Get-ShortcutFingerprint $Before.Path
    foreach ($property in @("Exists", "Sha256", "TargetPath", "WorkingDirectory")) {
        if ($after.$property -ne $Before.$property) {
            return "$Phase changed protected shortcut $($Before.Path) ($property)"
        }
    }
    return ""
}

function ConvertTo-RegistryValueFingerprint($Value) {
    if ($null -eq $Value) { return "<null>" }
    if ($Value -is [byte[]]) {
        return "binary:" + [Convert]::ToBase64String($Value)
    }
    if ($Value -is [string[]]) {
        return "multi-string:" + (ConvertTo-Json -InputObject @($Value) -Compress)
    }
    return "$($Value.GetType().FullName):$Value"
}

function Get-RegistryValueState([string]$SubKeyPath, [string]$ValueName) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKeyPath, $false)
    if ($null -eq $key) {
        return [pscustomobject]@{
            SubKeyPath = $SubKeyPath
            ValueName = $ValueName
            KeyExists = $false
            ValueExists = $false
            Kind = ""
            Value = $null
            Fingerprint = ""
        }
    }
    try {
        $valueExists = @($key.GetValueNames()) -contains $ValueName
        if (-not $valueExists) {
            return [pscustomobject]@{
                SubKeyPath = $SubKeyPath
                ValueName = $ValueName
                KeyExists = $true
                ValueExists = $false
                Kind = ""
                Value = $null
                Fingerprint = ""
            }
        }
        $value = $key.GetValue(
            $ValueName,
            $null,
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        )
        return [pscustomobject]@{
            SubKeyPath = $SubKeyPath
            ValueName = $ValueName
            KeyExists = $true
            ValueExists = $true
            Kind = [string]$key.GetValueKind($ValueName)
            Value = $value
            Fingerprint = ConvertTo-RegistryValueFingerprint $value
        }
    } finally {
        $key.Dispose()
    }
}

function Get-RegistryValueViolation($Before, [string]$Phase) {
    $after = Get-RegistryValueState $Before.SubKeyPath $Before.ValueName
    foreach ($property in @("KeyExists", "ValueExists", "Kind", "Fingerprint")) {
        if ($after.$property -ne $Before.$property) {
            $displayName = if ($Before.ValueName) { $Before.ValueName } else { "(Default)" }
            return "$Phase changed protected registry value HKCU\$($Before.SubKeyPath)\$displayName ($property)"
        }
    }
    return ""
}

function Restore-RegistryValueState($State) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($State.SubKeyPath, $true)
    try {
        if ($State.ValueExists) {
            if ($null -eq $key) {
                $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($State.SubKeyPath, $true)
            }
            $kind = [System.Enum]::Parse(
                [Microsoft.Win32.RegistryValueKind],
                [string]$State.Kind
            )
            $key.SetValue($State.ValueName, $State.Value, $kind)
        } elseif ($null -ne $key) {
            $key.DeleteValue($State.ValueName, $false)
        }
    } finally {
        if ($null -ne $key) { $key.Dispose() }
    }
}

function Restore-RegistryValueIfOwned($Before, [string]$InstallDirectory) {
    $violation = Get-RegistryValueViolation $Before "pre-recovery"
    if (-not $violation) { return $false }

    $after = Get-RegistryValueState $Before.SubKeyPath $Before.ValueName
    $referencesIsolatedInstall = (
        $after.ValueExists -and
        $after.Value -is [string] -and
        ([string]$after.Value).IndexOf(
            $InstallDirectory,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    )
    if (($Before.ValueExists -and -not $after.ValueExists) -or $referencesIsolatedInstall) {
        Restore-RegistryValueState $Before
        return $true
    }
    throw (
        "Refusing to overwrite a protected registry value changed by another actor: " +
        "HKCU\$($Before.SubKeyPath)\$($Before.ValueName)"
    )
}

function Test-RegistryKeyExists([string]$SubKeyPath) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKeyPath, $false)
    if ($null -eq $key) { return $false }
    $key.Dispose()
    return $true
}

function Remove-RegistryKeyIfEmpty([string]$SubKeyPath) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKeyPath, $false)
    if ($null -eq $key) { return }
    try {
        $isEmpty = $key.GetValueNames().Count -eq 0 -and $key.GetSubKeyNames().Count -eq 0
    } finally {
        $key.Dispose()
    }
    if ($isEmpty) {
        [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKey($SubKeyPath, $false)
    }
}

function Get-DirectoryFingerprint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return '{"exists":false,"entries":[]}'
    }
    $root = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $entries = @(
        Get-ChildItem -LiteralPath $root -Recurse -Force |
            Sort-Object FullName |
            ForEach-Object {
                $relative = $_.FullName.Substring($root.Length).TrimStart('\')
                if ($_.PSIsContainer) {
                    [pscustomobject]@{ path = $relative; kind = "directory"; sha256 = "" }
                } else {
                    [pscustomobject]@{
                        path = $relative
                        kind = "file"
                        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
                    }
                }
            }
    )
    return ([pscustomobject]@{ exists = $true; entries = $entries } | ConvertTo-Json -Depth 4 -Compress)
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

function Restore-Shortcut($State, $Before, [string]$InstallDirectory) {
    $violation = Get-ShortcutFingerprintViolation $Before "pre-recovery"
    if (-not $violation) { return $false }

    $after = Get-ShortcutFingerprint $State.Path
    if ($State.Existed) {
        if ($after.Exists -and -not (Test-PathInside $after.TargetPath $InstallDirectory)) {
            throw "Refusing to overwrite a protected shortcut changed by another actor: $($State.Path)"
        }
        if (-not (Test-Path -LiteralPath $State.Backup -PathType Leaf)) {
            throw "Shortcut backup is missing: $($State.Backup)"
        }
        $parent = Split-Path -Parent $State.Path
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -LiteralPath $State.Backup -Destination $State.Path -Force
        return $true
    }
    if (-not $after.Exists) { return $false }
    if (-not (Test-PathInside $after.TargetPath $InstallDirectory)) {
        throw "Refusing to remove a shortcut not created for the isolated install: $($State.Path)"
    }
    Remove-Item -LiteralPath $State.Path -Force
    return $true
}

$installerManifest = $null
$installerManifestSha256 = ""
$expectedInstalledAppSize = 0
if ($TrustMode -eq "UnsignedInternal") {
    if (-not $InstallerManifestPath) {
        $InstallerManifestPath = Join-Path `
            (Split-Path -Parent $InstallerPath) `
            "installer-candidate-manifest.json"
    }
    $InstallerManifestPath = [System.IO.Path]::GetFullPath($InstallerManifestPath)
    if (-not (Test-SamePath `
        (Split-Path -Parent $InstallerManifestPath) `
        (Split-Path -Parent $InstallerPath)
    )) {
        throw "UnsignedInternal installer manifest must be beside the installer."
    }
    $installerManifestItem = Assert-RegularFile `
        -PathToCheck $InstallerManifestPath `
        -Label "UnsignedInternal installer manifest"
    $installerManifestSha256 = (Get-FileHash `
        -LiteralPath $InstallerManifestPath `
        -Algorithm SHA256).Hash
    if ($installerManifestSha256 -cne $ExpectedInstallerManifestSha256) {
        throw "UnsignedInternal installer manifest does not match the external SHA-256 anchor."
    }
    try {
        $installerManifest = Get-Content -LiteralPath $InstallerManifestPath -Raw | ConvertFrom-Json
    } catch {
        throw "UnsignedInternal installer manifest is invalid JSON: $($_.Exception.Message)"
    }
    $manifestInstaller = $installerManifest.installer
    $manifestCanonical = $installerManifest.canonical_candidate
    $actualInstaller = Assert-RegularFile `
        -PathToCheck $InstallerPath `
        -Label "UnsignedInternal installer"
    $actualInstallerSha256 = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
    if (
        [long]$installerManifest.schema_version -ne 4 -or
        [string]$installerManifest.kind -cne "product-atelier-unsigned-nsis-candidate" -or
        [string]$installerManifest.git_commit -cne $ExpectedGitCommit.ToLowerInvariant() -or
        [string]$manifestInstaller.filename -cne $actualInstaller.Name -or
        [string]$manifestInstaller.sha256 -cne $actualInstallerSha256 -or
        [long]$manifestInstaller.size_bytes -ne [long]$actualInstaller.Length -or
        [string]$manifestInstaller.authenticode_status -cne "NotSigned" -or
        [string]$manifestInstaller.bundle_identity_algorithm -cne $TauriBundleIdentityAlgorithm
    ) {
        throw "UnsignedInternal installer manifest is not bound to this installer and Git commit."
    }
    foreach ($hash in @(
        [string]$manifestCanonical.app_sha256,
        [string]$manifestInstaller.raw_rebuild_app_sha256,
        [string]$manifestInstaller.portable_app_sha256,
        [string]$manifestInstaller.bundle_input_app_sha256,
        [string]$manifestInstaller.expected_installed_app_sha256,
        [string]$manifestInstaller.post_bundle_restored_app_sha256
    )) {
        if ($hash -notmatch '^[0-9A-F]{64}$') {
            throw "UnsignedInternal installer manifest contains an invalid App SHA-256: $hash"
        }
    }
    if (
        [string]$manifestInstaller.portable_app_sha256 -cne [string]$manifestCanonical.app_sha256 -or
        [string]$manifestInstaller.bundle_input_app_sha256 -cne [string]$manifestCanonical.app_sha256 -or
        [string]$manifestInstaller.post_bundle_restored_app_sha256 -cne [string]$manifestCanonical.app_sha256 -or
        [long]$manifestInstaller.portable_app_size_bytes -le 0 -or
        [long]$manifestInstaller.bundle_input_app_size_bytes -ne [long]$manifestInstaller.portable_app_size_bytes -or
        [long]$manifestInstaller.expected_installed_app_size_bytes -ne [long]$manifestInstaller.portable_app_size_bytes -or
        [long]$manifestInstaller.post_bundle_restored_app_size_bytes -ne [long]$manifestInstaller.portable_app_size_bytes -or
        [long]$manifestInstaller.bundle_type_marker_offset -lt 0 -or
        [string]$manifestInstaller.bundle_type_marker_source -cne "__TAURI_BUNDLE_TYPE_VAR_UNK" -or
        [string]$manifestInstaller.bundle_type_marker_installed -cne "__TAURI_BUNDLE_TYPE_VAR_NSS" -or
        [long]$manifestInstaller.bundle_type_changed_byte_count -ne 3 -or
        @($manifestInstaller.bundle_type_changed_byte_offsets).Count -ne 3
    ) {
        throw "UnsignedInternal installer manifest contains an invalid App bundle identity chain."
    }
    $manifestExpectedInstalledAppSha256 = [string]$manifestInstaller.expected_installed_app_sha256
    if ($ExpectedInstalledAppSha256 -cne $manifestExpectedInstalledAppSha256) {
        throw "Installed App SHA-256 anchor does not match the installer manifest."
    }
    $expectedInstalledAppSize = [long]$manifestInstaller.expected_installed_app_size_bytes
    $markerSource = [string]$manifestInstaller.bundle_type_marker_source
    $markerInstalled = [string]$manifestInstaller.bundle_type_marker_installed
    $expectedChangedOffsets = @()
    for ($index = 0; $index -lt $markerSource.Length; $index++) {
        if ($markerSource[$index] -cne $markerInstalled[$index]) {
            $expectedChangedOffsets += [long]$manifestInstaller.bundle_type_marker_offset + $index
        }
    }
    $manifestChangedOffsets = @(
        $manifestInstaller.bundle_type_changed_byte_offsets |
            ForEach-Object { [long]$_ }
    )
    if (
        ($expectedChangedOffsets -join ",") -cne ($manifestChangedOffsets -join ",") -or
        $expectedChangedOffsets.Count -ne 3
    ) {
        throw "UnsignedInternal installer manifest contains invalid marker diff offsets."
    }
}

$existingInstallations = Get-ProductUninstallEntries
if ($existingInstallations.Count -gt 0) {
    throw "A registered Product Atelier installation already exists; refusing an isolated installer test that could overwrite it."
}

if ($TrustMode -eq "Signed") {
    & $CodeSigningTool `
        -Mode Verify `
        -ArtifactPath $InstallerPath `
        -CertificateThumbprint $CertificateThumbprint
} else {
    Assert-UnsignedArtifacts @($InstallerPath)
}

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

$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "$ProductName.lnk"
$programsDirectory = [Environment]::GetFolderPath("Programs")
$programFolder = Join-Path $programsDirectory $ProductName
$programFolderExisted = Test-Path -LiteralPath $programFolder -PathType Container
$shortcutPaths = @(
    $desktopShortcut,
    (Join-Path $programsDirectory "$ProductName.lnk"),
    (Join-Path $programFolder "$ProductName.lnk")
)
$shortcutStates = @()
$shortcutFingerprints = @($shortcutPaths | ForEach-Object { Get-ShortcutFingerprint $_ })
$programFolderFingerprint = Get-DirectoryFingerprint $programFolder
for ($index = 0; $index -lt $shortcutPaths.Count; $index++) {
    $shortcutStates += Backup-Shortcut $shortcutPaths[$index] $safetyRoot $index
}

$manufacturerRegistryPath = "Software\$NsisManufacturer"
$productRegistryPath = "$manufacturerRegistryPath\$ProductName"
$manufacturerRegistryKeyExisted = Test-RegistryKeyExists $manufacturerRegistryPath
$productRegistryKeyExisted = Test-RegistryKeyExists $productRegistryPath
$productRegistryStates = @(
    (Get-RegistryValueState $productRegistryPath ""),
    (Get-RegistryValueState $productRegistryPath "Installer Language")
)
$runRegistryState = Get-RegistryValueState `
    "Software\Microsoft\Windows\CurrentVersion\Run" `
    $ProductName
$registrySnapshotPath = Join-Path $safetyRoot "registry-state.clixml"
[pscustomobject]@{
    format_version = 1
    captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    product_name = $ProductName
    bundle_identifier = $bundleIdentifier
    manufacturer_registry_path = $manufacturerRegistryPath
    manufacturer_key_existed = $manufacturerRegistryKeyExisted
    product_registry_path = $productRegistryPath
    product_key_existed = $productRegistryKeyExisted
    product_values = $productRegistryStates
    run_value = $runRegistryState
} | Export-Clixml -LiteralPath $registrySnapshotPath -Encoding UTF8 -Depth 6
$recoveryNotePath = Join-Path $safetyRoot "RECOVERY.txt"
[System.IO.File]::WriteAllLines(
    $recoveryNotePath,
    @(
        "Product Atelier isolated installer safety backup.",
        "Do not delete this directory unless the gate reports successful recovery.",
        "registry-state.clixml contains the typed pre-install HKCU value snapshot.",
        "Shortcut backups are named shortcut-*.lnk."
    ),
    [System.Text.UTF8Encoding]::new($true)
)
Write-Host "Installer safety backup prepared: $safetyRoot"
$executionInstallerPath = Join-Path $safetyRoot "installer-execution-copy.exe"

$installSucceeded = $false
$installAttempted = $false
$uninstallAttempted = $false
$uninstaller = ""
$gateError = $null
$protectionViolations = [System.Collections.Generic.List[string]]::new()
$cleanupErrors = [System.Collections.Generic.List[string]]::new()
$safetyBackupRetained = $false
try {
    $sourceInstaller = Assert-RegularFile `
        -PathToCheck $InstallerPath `
        -Label "Installer source"
    $sourceInstallerHashBeforeCopy = (Get-FileHash `
        -LiteralPath $InstallerPath `
        -Algorithm SHA256).Hash
    if ($TrustMode -eq "UnsignedInternal") {
        if (
            (Get-FileHash -LiteralPath $InstallerManifestPath -Algorithm SHA256).Hash -cne
                $ExpectedInstallerManifestSha256 -or
            $sourceInstallerHashBeforeCopy -cne [string]$installerManifest.installer.sha256
        ) {
            throw "UnsignedInternal installer or manifest changed before installation."
        }
    }
    if (Test-Path -LiteralPath $executionInstallerPath) {
        throw "Owned installer execution copy already exists: $executionInstallerPath"
    }
    Copy-Item -LiteralPath $InstallerPath -Destination $executionInstallerPath
    $executionInstaller = Assert-RegularFile `
        -PathToCheck $executionInstallerPath `
        -Label "Owned installer execution copy"
    $sourceInstallerHashAfterCopy = (Get-FileHash `
        -LiteralPath $InstallerPath `
        -Algorithm SHA256).Hash
    $executionInstallerHash = (Get-FileHash `
        -LiteralPath $executionInstallerPath `
        -Algorithm SHA256).Hash
    if (
        $sourceInstallerHashBeforeCopy -cne $sourceInstallerHashAfterCopy -or
        $sourceInstallerHashBeforeCopy -cne $executionInstallerHash -or
        [long]$sourceInstaller.Length -ne [long]$executionInstaller.Length
    ) {
        throw "Installer changed or its owned execution copy is not exact."
    }
    if ($TrustMode -eq "Signed") {
        & $CodeSigningTool `
            -Mode Verify `
            -ArtifactPath $executionInstallerPath `
            -CertificateThumbprint $CertificateThumbprint
    } else {
        Assert-UnsignedArtifacts @($executionInstallerPath)
    }
    $installerArguments = @("/S", "/D=$installDirectory")
    if ($TrustMode -eq "UnsignedInternal") {
        # Tauri CLI 2.11.4 maps /NS to NoShortcutMode. Backups below remain
        # the recovery boundary if a future installer ever ignores the switch.
        $installerArguments = @("/S", "/NS", "/D=$installDirectory")
    }
    $installAttempted = $true
    $installProcess = Start-Process `
        -FilePath $executionInstallerPath `
        -ArgumentList $installerArguments `
        -Wait `
        -PassThru `
        -WindowStyle Hidden
    if ($installProcess.ExitCode -ne 0) {
        throw "NSIS installer returned exit code $($installProcess.ExitCode)"
    }
    $installSucceeded = $true
    if ($TrustMode -eq "UnsignedInternal") {
        foreach ($shortcutFingerprint in $shortcutFingerprints) {
            $violation = Get-ShortcutFingerprintViolation $shortcutFingerprint "post-install"
            if ($violation) { $protectionViolations.Add($violation) }
        }
        if ((Get-DirectoryFingerprint $programFolder) -ne $programFolderFingerprint) {
            $protectionViolations.Add(
                "post-install changed protected Start Menu content: $programFolder"
            )
        }
        if ($protectionViolations.Count -gt 0) {
            throw "Unsigned internal installer changed protected shortcuts; recovery is required"
        }
    }

    $appExe = Join-Path $installDirectory "Product Atelier.exe"
    $sidecarExe = Join-Path $installDirectory "python-server\python-server.exe"
    $installedApp = Assert-RegularFile -PathToCheck $appExe -Label "Installed application"
    $installedSidecar = Assert-RegularFile -PathToCheck $sidecarExe -Label "Installed sidecar"
    $uninstallers = @(Get-ChildItem -LiteralPath $installDirectory -File -Filter "uninstall*.exe")
    if ($uninstallers.Count -ne 1) {
        throw "Expected exactly one installed NSIS uninstaller, found $($uninstallers.Count)"
    }
    $uninstaller = $uninstallers[0].FullName
    $installedUninstaller = Assert-RegularFile `
        -PathToCheck $uninstaller `
        -Label "Installed NSIS uninstaller"

    if ($TrustMode -eq "UnsignedInternal") {
        $installedIdentity = Invoke-InstalledNsisIdentityValidation `
            -AppPath $appExe `
            -ExpectedSha256 $ExpectedInstalledAppSha256
        if (
            [long]$installedIdentity.installed_app_size_bytes -ne $expectedInstalledAppSize -or
            [long]$installedApp.Length -ne $expectedInstalledAppSize
        ) {
            throw "Installed NSIS app size does not match the installer manifest."
        }
        if ([long]$installedIdentity.marker_offset -ne [long]$manifestInstaller.bundle_type_marker_offset) {
            throw "Installed NSIS app marker offset does not match the installer manifest."
        }
        if ((Get-FileHash -LiteralPath $appExe -Algorithm SHA256).Hash -cne $ExpectedInstalledAppSha256) {
            throw "Installed NSIS app changed after identity validation."
        }
    }

    if ($TrustMode -eq "Signed") {
        & $CodeSigningTool `
            -Mode Verify `
            -ArtifactPath @($appExe, $sidecarExe, $uninstaller) `
            -CertificateThumbprint $CertificateThumbprint
    } else {
        Assert-UnsignedArtifacts @($appExe, $sidecarExe, $uninstaller)
    }
    & "$PSScriptRoot\Test-Portable.ps1" `
        -PortableDir $installDirectory `
        -ExpectedGitCommit $ExpectedGitCommit
    if (-not $SkipAppSmoke) {
        & "$PSScriptRoot\Test-Portable-App.ps1" `
            -PortableDir $installDirectory `
            -ExpectedGitCommit $ExpectedGitCommit
    }
    & python.exe $SchemaGate --sidecar-dir (Join-Path $installDirectory "python-server")
    if ($LASTEXITCODE -ne 0) {
        throw "Installed schema upgrade and local-edit gate failed"
    }
} catch {
    $gateError = $_
} finally {
    try {
        Stop-TestProcesses $installDirectory
    } catch {
        $cleanupErrors.Add("Could not stop isolated test processes: $($_.Exception.Message)")
    }
    try {
        if (-not $uninstaller -and (Test-Path -LiteralPath $installDirectory -PathType Container)) {
            Assert-NoReparsePath `
                -PathToCheck $installDirectory `
                -Label "Isolated install directory"
            $cleanupUninstallers = @(
                Get-ChildItem -LiteralPath $installDirectory -File -Filter "uninstall*.exe"
            )
            if ($cleanupUninstallers.Count -eq 1) {
                $uninstaller = $cleanupUninstallers[0].FullName
            }
        }
    } catch {
        $cleanupErrors.Add("Could not inspect the isolated uninstaller: $($_.Exception.Message)")
    }
    if ($installAttempted -and $uninstaller -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        try {
            Assert-RegularFile `
                -PathToCheck $uninstaller `
                -Label "Isolated NSIS uninstaller before cleanup" | Out-Null
            $uninstallAttempted = $true
            $uninstallProcess = Start-Process `
                -FilePath $uninstaller `
                -ArgumentList @("/S", "/UPDATE") `
                -Wait `
                -PassThru `
                -WindowStyle Hidden
            if ($uninstallProcess.ExitCode -ne 0) {
                $cleanupErrors.Add(
                    "NSIS uninstaller returned exit code $($uninstallProcess.ExitCode)"
                )
            }
        } catch {
            $cleanupErrors.Add("NSIS uninstaller failed: $($_.Exception.Message)")
        }
    }

    if ($TrustMode -eq "UnsignedInternal") {
        foreach ($shortcutFingerprint in $shortcutFingerprints) {
            try {
                $violation = Get-ShortcutFingerprintViolation $shortcutFingerprint "post-uninstall"
                if ($violation) { $protectionViolations.Add($violation) }
            } catch {
                $cleanupErrors.Add(
                    "Could not inspect protected shortcut $($shortcutFingerprint.Path): " +
                    $_.Exception.Message
                )
            }
        }
        try {
            if ((Get-DirectoryFingerprint $programFolder) -ne $programFolderFingerprint) {
                $protectionViolations.Add(
                    "post-uninstall changed protected Start Menu content: $programFolder"
                )
            }
        } catch {
            $cleanupErrors.Add(
                "Could not inspect protected Start Menu content: $($_.Exception.Message)"
            )
        }
    }

    try {
        $runViolation = Get-RegistryValueViolation $runRegistryState "post-uninstall"
        if ($runViolation) {
            $protectionViolations.Add($runViolation)
            Restore-RegistryValueIfOwned $runRegistryState $installDirectory | Out-Null
        }
    } catch {
        $cleanupErrors.Add(
            "Could not inspect or recover the protected Product Atelier autostart value: " +
            $_.Exception.Message
        )
    }

    for ($index = 0; $index -lt $shortcutStates.Count; $index++) {
        try {
            Restore-Shortcut `
                $shortcutStates[$index] `
                $shortcutFingerprints[$index] `
                $installDirectory | Out-Null
        } catch {
            $cleanupErrors.Add(
                "Could not restore protected shortcut $($shortcutStates[$index].Path): " +
                $_.Exception.Message
            )
        }
    }
    if (-not $programFolderExisted -and (Test-Path -LiteralPath $programFolder -PathType Container)) {
        try {
            $remainingProgramEntries = @(Get-ChildItem -LiteralPath $programFolder -Force)
            if ($remainingProgramEntries.Count -gt 0) {
                $cleanupErrors.Add(
                    "The isolated installer left unexpected Start Menu content: $programFolder"
                )
            } else {
                Remove-Item -LiteralPath $programFolder -Force
            }
        } catch {
            $cleanupErrors.Add("Could not clean the isolated Start Menu folder: $($_.Exception.Message)")
        }
    }

    foreach ($shortcutFingerprint in $shortcutFingerprints) {
        try {
            $violation = Get-ShortcutFingerprintViolation $shortcutFingerprint "post-recovery"
            if ($violation) {
                $cleanupErrors.Add("Shortcut recovery is incomplete: $violation")
            }
        } catch {
            $cleanupErrors.Add(
                "Could not verify recovered shortcut $($shortcutFingerprint.Path): " +
                $_.Exception.Message
            )
        }
    }
    try {
        if ((Get-DirectoryFingerprint $programFolder) -ne $programFolderFingerprint) {
            $cleanupErrors.Add(
                "Start Menu recovery is incomplete: $programFolder"
            )
        }
    } catch {
        $cleanupErrors.Add("Could not verify Start Menu recovery: $($_.Exception.Message)")
    }

    foreach ($registryState in $productRegistryStates) {
        try {
            Restore-RegistryValueState $registryState
        } catch {
            $displayName = if ($registryState.ValueName) {
                $registryState.ValueName
            } else {
                "(Default)"
            }
            $cleanupErrors.Add(
                "Could not restore HKCU\$productRegistryPath\$displayName`: " +
                $_.Exception.Message
            )
        }
    }
    try {
        if (-not $productRegistryKeyExisted) {
            Remove-RegistryKeyIfEmpty $productRegistryPath
        }
        if (-not $manufacturerRegistryKeyExisted) {
            Remove-RegistryKeyIfEmpty $manufacturerRegistryPath
        }
    } catch {
        $cleanupErrors.Add("Could not remove empty installer registry keys: $($_.Exception.Message)")
    }
    foreach ($registryState in $productRegistryStates) {
        try {
            $violation = Get-RegistryValueViolation $registryState "post-recovery"
            if ($violation) {
                $cleanupErrors.Add("Installer registry recovery is incomplete: $violation")
            }
        } catch {
            $cleanupErrors.Add(
                "Could not verify recovered installer registry state: $($_.Exception.Message)"
            )
        }
    }
    try {
        if ((Test-RegistryKeyExists $productRegistryPath) -ne $productRegistryKeyExisted) {
            $cleanupErrors.Add("Installer registry key existence was not restored: HKCU\$productRegistryPath")
        }
        if ((Test-RegistryKeyExists $manufacturerRegistryPath) -ne $manufacturerRegistryKeyExisted) {
            $cleanupErrors.Add("Manufacturer registry key existence was not restored: HKCU\$manufacturerRegistryPath")
        }
        $runViolation = Get-RegistryValueViolation $runRegistryState "post-recovery"
        if ($runViolation) {
            $cleanupErrors.Add("Autostart registry recovery is incomplete: $runViolation")
        }
    } catch {
        $cleanupErrors.Add("Could not verify final protected registry state: $($_.Exception.Message)")
    }

    if ($installAttempted) {
        $deadline = (Get-Date).AddSeconds($CleanupTimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            if (-not (Test-Path -LiteralPath $installDirectory)) { break }
            Start-Sleep -Milliseconds 500
        }
        if (Test-Path -LiteralPath $installDirectory) {
            $cleanupErrors.Add(
                "NSIS uninstall did not remove the isolated install directory: $installDirectory"
            )
        }
        if ((Get-ProductUninstallEntries).Count -gt 0) {
            $cleanupErrors.Add("NSIS uninstall left a Product Atelier registry entry")
        }
        if ($installSucceeded -and -not $uninstallAttempted) {
            $cleanupErrors.Add(
                "The isolated installation succeeded but no uninstaller could be executed"
            )
        }
    }

    if (Test-Path -LiteralPath $safetyRoot) {
        if ($cleanupErrors.Count -eq 0) {
            try {
                Remove-Item -LiteralPath $safetyRoot -Recurse -Force
            } catch {
                $cleanupErrors.Add("Could not remove the shortcut safety backup: $($_.Exception.Message)")
            }
        }
        if (Test-Path -LiteralPath $safetyRoot) {
            $safetyBackupRetained = $true
        }
    }
}

$failures = [System.Collections.Generic.List[string]]::new()
if ($gateError) {
    $failures.Add("Installed-state gate failed: $($gateError.Exception.Message)")
}
if ($protectionViolations.Count -gt 0) {
    $failures.Add(
        "Protected user-state violation detected and recovery attempted: " +
        ($protectionViolations -join "; ")
    )
}
if ($cleanupErrors.Count -gt 0) {
    $failures.Add("Cleanup or recovery failed: " + ($cleanupErrors -join "; "))
}
if ($safetyBackupRetained) {
    $failures.Add("Shortcut safety backup retained at: $safetyRoot")
}
if ($failures.Count -gt 0) {
    throw ($failures -join [Environment]::NewLine)
}

$scope = if ($SkipAppSmoke) { "headless preflight" } else { "full installed-state gate" }
Write-Host "$TrustMode NSIS $scope, uninstall, and shortcut protection passed." -ForegroundColor Green
Write-Host "Installer SHA-256: $((Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash)"
Write-Host "Git commit: $ExpectedGitCommit"
