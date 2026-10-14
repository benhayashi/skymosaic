# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8080

# Install system dependencies required for OpenCV, FFmpeg, and V4L2 USB camera support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    v4l-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python package installer uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency specifications
COPY pyproject.toml .

# Install dependencies using uv
RUN uv pip install --system --no-cache -r <(uv pip compile pyproject.toml)

# Copy application source code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY main.py .

# Create persistent storage directories
RUN mkdir -p data/archive data/keograms data/startrails data/timelapses data/test_shots data/masks

# Default configuration template if not mounted
COPY data/config.example.json ./data/config.example.json
RUN cp ./data/config.example.json ./data/config.json

# Expose web dashboard & API port
EXPOSE 8080

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/sky/live.jpg > /dev/null || exit 1

# Launch SkyMosaic
ENTRYPOINT ["python", "main.py", "--host", "0.0.0.0", "--port", "8080"]
