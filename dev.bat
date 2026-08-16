@echo off
chcp 65001 >nul
set PATH=C:\mingw64\bin;C:\Users\64414\.cargo\bin;C:\Program Files\nodejs;%PATH%
set CARGO_TARGET_DIR=D:\rust-target
cd /d "%~dp0"
echo Starting Product Atelier dev mode...
call npm run tauri dev
