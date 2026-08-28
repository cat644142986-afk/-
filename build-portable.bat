@echo off
chcp 65001 >nul
echo ========================================
echo Product Atelier - Verified Portable Release
echo ========================================
echo.

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\dev.ps1" -NoScreenshot
if errorlevel 1 ( echo Verified portable release failed & pause & exit /b 1 )

echo Verified portable release is ready under release\ProductAtelier-Portable.
pause
