#!/usr/bin/env bash
set -euo pipefail

# Ensure work dir exists and is writable
mkdir -p /app
chmod 777 /app || true

# Basic diagnostics
echo "[batch] Python: $(python -V || true)"
echo "[batch] Pip: $(pip --version || true)"
echo "[batch] Working dir: $(pwd)"
echo "[batch] Listing /app:"
ls -la /app || true

# Warn if API file is missing
if [ ! -f /app/api.py ]; then
  echo "[batch] WARNING: /app/api.py not found. Is the volume mounted?"
fi

# Start gunicorn
exec gunicorn --workers "${WORKERS:-2}" --bind 0.0.0.0:5000 --timeout "${GUNICORN_TIMEOUT:-3600}" api:app
