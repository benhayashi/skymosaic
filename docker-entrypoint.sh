#!/bin/sh
set -e
mkdir -p /app/data/archive /app/data/keograms /app/data/startrails /app/data/timelapses /app/data/test_shots /app/data/masks /app/data/tle
if [ ! -f /app/data/config.json ]; then
  echo "[skymosaic] No config.json found - seeding from example"
  cp /opt/skymosaic/config.example.json /app/data/config.json
fi
exec "$@"
