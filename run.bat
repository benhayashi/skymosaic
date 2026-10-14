@echo off
REM ==============================================================================
REM SkyMosaic — Windows Launcher Script
REM ==============================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "PORT=8080"
set "HOST=0.0.0.0"

echo ==================================================================
echo 🌌 Starting SkyMosaic (Windows)
echo 📡 Port: %PORT% ^| Host: %HOST%
echo ==================================================================

where uv >nul 2>nul
if %errorlevel% equ 0 (
    echo ⚡ Using 'uv' package runner...
    uv run python main.py --host %HOST% --port %PORT% %*
    goto :eof
)

if exist .venv\Scripts\activate.bat (
    echo 🐍 Using local virtual environment (.venv)...
    call .venv\Scripts\activate.bat
    python main.py --host %HOST% --port %PORT% %*
    goto :eof
)

echo ⚙️ Creating Python virtual environment...
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install fastapi "uvicorn[standard]" opencv-python-headless numpy scipy pydantic pytz httpx "imageio[ffmpeg]"
python main.py --host %HOST% --port %PORT% %*
