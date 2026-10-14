#!/usr/bin/env bash
# ==============================================================================
# SkyMosaic — Linux / macOS Launcher Script
# ==============================================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

PORT="${PORT:-8080}"
HOST="${HOST:-0.0.0.0}"

echo "=================================================================="
echo "🌌 Starting SkyMosaic (Linux / macOS)"
echo "📡 Port: $PORT | Host: $HOST"
echo "=================================================================="

# Check if uv is installed, otherwise fallback to python3 venv
if command -v uv &> /dev/null; then
    echo "⚡ Using 'uv' package runner..."
    uv run python main.py --host "$HOST" --port "$PORT" "$@"
elif [ -d ".venv" ]; then
    echo "🐍 Using local virtual environment (.venv)..."
    source .venv/bin/activate
    python main.py --host "$HOST" --port "$PORT" "$@"
else
    echo "⚙️ Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install fastapi "uvicorn[standard]" opencv-python-headless numpy scipy pydantic pytz httpx "imageio[ffmpeg]"
    python main.py --host "$HOST" --port "$PORT" "$@"
fi
