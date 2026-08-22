@echo off
chcp 65001 >nul
echo ========================================
echo Product Atelier - NSIS Installer Build
echo ========================================

set PATH=C:\mingw64\bin;%USERPROFILE%\.cargo\bin;C:\Program Files\nodejs;%PATH%
set CARGO_TARGET_DIR=D:\rust-target
cd /d "%~dp0"

echo [1/3] Building Python backend (PyInstaller)...
pyinstaller python-server.spec --distpath src-tauri/bin --workpath build/pyinstaller --noconfirm
if errorlevel 1 ( echo Python build failed & pause & exit /b 1 )

echo [2/3] Building Tauri app + NSIS installer...
call npm run tauri build
if errorlevel 1 ( echo Tauri build failed & pause & exit /b 1 )

echo.
echo ========================================
echo Build complete!
echo Installer: D:\rust-target\release\bundle\nsis\Product Atelier_1.0.0_x64-setup.exe
echo ========================================
pause
