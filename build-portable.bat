@echo off
chcp 65001 >nul
echo ========================================
echo Product Atelier - Portable Build Script
echo ========================================
echo.

set PATH=C:\mingw64\bin;%USERPROFILE%\.cargo\bin;C:\Program Files\nodejs;%PATH%
set CARGO_TARGET_DIR=D:\rust-target
cd /d "%~dp0"

echo [1/5] Building current Python sidecar...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\Build-Sidecar.ps1"
if errorlevel 1 ( echo Sidecar build failed & pause & exit /b 1 )

echo [2/5] Building embedded Tauri release...
call npx.cmd tauri build --no-bundle --features custom-protocol
if errorlevel 1 ( echo Tauri release build failed & pause & exit /b 1 )

echo [3/5] Assembling portable folder...
if exist "dist\ProductAtelier-Portable" rmdir /s /q "dist\ProductAtelier-Portable"
mkdir "dist\ProductAtelier-Portable"

copy "D:\rust-target\release\product-atelier.exe" "dist\ProductAtelier-Portable\Product Atelier.exe"
xcopy "src-tauri\bin\python-server" "dist\ProductAtelier-Portable\python-server\" /e /i /q /y

echo @echo off > "dist\ProductAtelier-Portable\Start.bat"
echo start "" "%%~dp0Product Atelier.exe" >> "dist\ProductAtelier-Portable\Start.bat"

echo [4/5] Verifying sidecar and embedded frontend...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\Test-Portable.ps1" -PortableDir "dist\ProductAtelier-Portable"
if errorlevel 1 ( echo Portable sidecar verification failed & pause & exit /b 1 )
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\Test-Portable-App.ps1" -PortableDir "dist\ProductAtelier-Portable"
if errorlevel 1 ( echo Portable app verification failed & pause & exit /b 1 )

echo [5/5] Updating the single desktop shortcut...
powershell -NoProfile -Command "$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'Product Atelier.lnk')); $l.TargetPath=[IO.Path]::GetFullPath('dist\ProductAtelier-Portable\Product Atelier.exe'); $l.WorkingDirectory=[IO.Path]::GetFullPath('dist\ProductAtelier-Portable'); $l.IconLocation=$l.TargetPath+',0'; $l.Save()"
if errorlevel 1 ( echo Desktop shortcut update failed & pause & exit /b 1 )

echo Portable build ready: dist\ProductAtelier-Portable\ (~375MB)
pause
