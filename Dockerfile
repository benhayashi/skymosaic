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
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application source code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY main.py .

# Persistent storage (mount a volume / host path at /app/data). The entrypoint seeds config.json
# from the bundled example on first start so empty mounts (e.g. TrueNAS datasets) just work.
COPY data/config.example.json /opt/skymosaic/config.example.json
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p data/archive data/keograms data/startrails data/timelapses data/test_shots data/masks data/tle
VOLUME ["/app/data"]

# Expose web dashboard & API port
EXPOSE 8080

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/config > /dev/null || exit 1

# Launch SkyMosaic
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "8080"]
