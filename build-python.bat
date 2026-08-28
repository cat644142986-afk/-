@echo off
chcp 65001 >nul
set PATH=C:\mingw64\bin;%USERPROFILE%\.cargo\bin;C:\Program Files\nodejs;%PATH%
cd /d "%~dp0"
echo Building Python backend...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\Build-Sidecar.ps1"
if errorlevel 1 ( echo Python build failed & pause & exit /b 1 )
echo Done.
pause
