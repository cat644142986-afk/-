@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo Product Atelier - Signed NSIS Release
echo ========================================
echo.
echo Required environment variables:
echo   PRODUCT_ATELIER_SIGN_CERT_SHA1
echo   PRODUCT_ATELIER_SIGN_TIMESTAMP_URL
echo Optional:
echo   PRODUCT_ATELIER_SIGNTOOL_PATH
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "tools\Build-SignedInstaller.ps1"
if errorlevel 1 (
  echo Signed release gate failed. No public installer was published.
  pause
  exit /b 1
)

echo.
echo Signed installer release gate passed.
pause
