@echo off
chcp 65001 >nul
set PATH=C:\mingw64\bin;%USERPROFILE%\.cargo\bin;C:\Program Files\nodejs;%PATH%
cd /d "%~dp0"
echo Building Python backend...
pyinstaller python-server.spec --distpath src-tauri/bin --workpath build/pyinstaller --noconfirm
echo Done.
pause
