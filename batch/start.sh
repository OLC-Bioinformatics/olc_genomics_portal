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

# Warn if API or env file missing
if [ ! -f /app/api.py ]; then
  echo "[batch] WARNING: /app/api.py not found. Is the volume mounted?"
fi
if [ ! -f /app/env ]; then
  echo "[batch] WARNING: /app/env not found. Batch jobs will fail without credentials."
fi

# Start gunicorn
exec gunicorn --workers "${WORKERS:-2}" --bind 0.0.0.0:5000 --timeout "${GUNICORN_TIMEOUT:-3600}" api:app
