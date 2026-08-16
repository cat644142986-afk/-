@echo off
chcp 65001 >nul
echo ========================================
echo Product Atelier - Portable Build Script
echo ========================================
echo.

set PATH=C:\mingw64\bin;C:\Users\64414\.cargo\bin;C:\Program Files\nodejs;%PATH%
set CARGO_TARGET_DIR=D:\rust-target
cd /d "%~dp0"

echo [1/3] Building frontend...
call npm run build
if errorlevel 1 ( echo Frontend build failed & pause & exit /b 1 )

echo [2/3] Building Rust shell (Release)...
cd src-tauri
cargo build --release
if errorlevel 1 ( echo Rust build failed & pause & exit /b 1 )
cd ..

echo [3/3] Assembling portable folder...
if exist "dist\ProductAtelier-Portable" rmdir /s /q "dist\ProductAtelier-Portable"
mkdir "dist\ProductAtelier-Portable"

copy "D:\rust-target\release\product-atelier.exe" "dist\ProductAtelier-Portable\Product Atelier.exe"
xcopy "src-tauri\bin\python-server" "dist\ProductAtelier-Portable\python-server\" /e /i /q /y

echo @echo off > "dist\ProductAtelier-Portable\Start.bat"
echo start "" "%%~dp0Product Atelier.exe" >> "dist\ProductAtelier-Portable\Start.bat"

echo Portable build ready: dist\ProductAtelier-Portable\ (~375MB)
pause
