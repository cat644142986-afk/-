@echo off
chcp 65001 >nul
echo ========================================
echo Product Atelier - NSIS Installer Build
echo ========================================

set PATH=C:\mingw64\bin;%USERPROFILE%\.cargo\bin;C:\Program Files\nodejs;%PATH%
set CARGO_TARGET_DIR=D:\rust-target
cd /d "%~dp0"

echo [1/3] Running the verified portable release gate...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\dev.ps1" -NoScreenshot
if errorlevel 1 ( echo Verified portable release failed & pause & exit /b 1 )

echo [2/3] Building Tauri app + NSIS installer...
call npx.cmd tauri build --features custom-protocol
if errorlevel 1 ( echo Tauri build failed & pause & exit /b 1 )

set "INSTALLER=D:\rust-target\release\bundle\nsis\Product Atelier_1.0.0_x64-setup.exe"
if not exist "%INSTALLER%" ( echo Installer artifact is missing & pause & exit /b 1 )

echo [3/3] Recording installer SHA-256...
certutil -hashfile "%INSTALLER%" SHA256
if errorlevel 1 ( echo Installer hash failed & pause & exit /b 1 )

echo.
echo ========================================
echo Installer candidate built. Installed NSIS runtime smoke is still required.
echo Installer: %INSTALLER%
echo ========================================
pause
