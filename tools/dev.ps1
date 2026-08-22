# ============================================================
# Product Atelier - One-Click Build, Deploy & Test
# Usage: powershell -File tools\dev.ps1 [-Quick] [-SkipSidecar] [-NoScreenshot]
#   -Quick: Skip Rust rebuild. The Python sidecar still rebuilds unless explicitly skipped.
#   -SkipSidecar: Use only when the current packaged sidecar manifest already matches source.
#   -NoScreenshot: Skip screenshot after launch
# ============================================================
param(
    [switch]$Quick,
    [switch]$SkipSidecar,
    [switch]$NoScreenshot,
    [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }

# Setup paths
$env:PATH = "$env:APPDATA\npm;C:\Program Files\nodejs;$env:USERPROFILE\.cargo\bin;C:\msys64\mingw64\bin;$env:PATH"
$env:CARGO_TARGET_DIR = "D:\rust-target"

$SourceExe = "D:\rust-target\release\product-atelier.exe"
$PortableDir = "$ProjectRoot\release\ProductAtelier-Portable"
$TargetExe = "$PortableDir\Product Atelier.exe"
$PythonServer = "$PortableDir\python-server\python-server.exe"

Write-Host "=== Product Atelier Dev Build ===" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"
Write-Host "Quick mode: $Quick"
Write-Host "Skip sidecar: $SkipSidecar"
Write-Host ""

# Step 1: Kill running instances
Write-Host "[1/7] Killing running instances..." -ForegroundColor Yellow
Get-Process -Name "Product Atelier", "product-atelier", "python-server" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# Step 2: Build Python sidecar from current source
Write-Host "[2/7] Building Python sidecar..." -ForegroundColor Yellow
if (-not $SkipSidecar) {
    & "$PSScriptRoot\Build-Sidecar.ps1" -DeployPortable
    if ($LASTEXITCODE -ne 0) { throw "Sidecar build failed" }
} else {
    Write-Host "  Sidecar rebuild explicitly skipped" -ForegroundColor DarkGray
}

# Step 3: Build frontend
Write-Host "[3/7] Building frontend (Vite)..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    npm run build 2>&1 | ForEach-Object {
        if ($_ -match "error|ERR|fail") { Write-Host "  $_" -ForegroundColor Red }
    }
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
} finally {
    Pop-Location
}
Write-Host "  Frontend built OK" -ForegroundColor Green

# Step 4: Build Rust (skip in quick mode)
if (-not $Quick) {
    Write-Host "[4/7] Building Rust binary (Tauri)..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        npx tauri build --no-bundle 2>&1 | ForEach-Object {
            if ($_ -match "error\[|error:") { Write-Host "  $_" -ForegroundColor Red }
        }
        if ($LASTEXITCODE -ne 0) { throw "Rust build failed" }
    } finally {
        Pop-Location
    }
    Write-Host "  Rust build OK" -ForegroundColor Green
} else {
    Write-Host "[4/7] Skipping Rust build (-Quick mode)" -ForegroundColor DarkGray
}

# Step 5: Deploy to portable dir
Write-Host "[5/7] Deploying to portable directory..." -ForegroundColor Yellow
if (-not (Test-Path $SourceExe) -and -not $Quick) {
    throw "Built exe not found at $SourceExe"
}
if (Test-Path $SourceExe) {
    Copy-Item $SourceExe $TargetExe -Force
    Write-Host "  Copied exe to portable dir" -ForegroundColor Green
} else {
    Write-Host "  Warning: Source exe not found, using existing" -ForegroundColor Yellow
}

# Verify Python server exists
if (-not (Test-Path $PythonServer)) {
    throw "Python server not found at $PythonServer"
}

# Update desktop shortcut
$desktopShortcut = "$env:USERPROFILE\Desktop\Product Atelier.lnk"
if (Test-Path $desktopShortcut) {
    $WshShell = New-Object -ComObject WScript.Shell
    $shortcut = $WshShell.CreateShortcut($desktopShortcut)
    $shortcut.TargetPath = $TargetExe
    $shortcut.WorkingDirectory = $PortableDir
    $shortcut.Save()
    Write-Host "  Desktop shortcut updated" -ForegroundColor Green
}

# Step 6: Verify the exact packaged sidecar before launch
Write-Host "[6/7] Verifying portable sidecar..." -ForegroundColor Yellow
& "$PSScriptRoot\Test-Portable.ps1" -PortableDir $PortableDir
if ($LASTEXITCODE -ne 0) { throw "Portable verification failed" }

# Step 7: Launch & Screenshot
Write-Host "[7/7] Launching application..." -ForegroundColor Yellow
Start-Process $TargetExe -WorkingDirectory $PortableDir
Write-Host "  Waiting $WaitSeconds seconds for app to start..." -ForegroundColor DarkGray
Start-Sleep -Seconds $WaitSeconds

if (-not $NoScreenshot) {
    $screenshotScript = "$ProjectRoot\tools\screenshot.py"
    if (Test-Path $screenshotScript) {
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $screenshotPath = "$ProjectRoot\tools\last_capture.png"
        python $screenshotScript --window "Product Atelier" --pad 30 -o $screenshotPath 2>&1 | Out-Null
        if (Test-Path $screenshotPath) {
            Write-Host "  Screenshot saved: $screenshotPath" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "=== Build & Deploy Complete ===" -ForegroundColor Green
Write-Host "App launched from: $TargetExe"
