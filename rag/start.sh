#!/usr/bin/env bash
set -euo pipefail
umask 0002

echo "[rag] Starting RedmineAssistant retrieval service"
echo "[rag] Python: $(python --version)"
echo "[rag] Documentation root: ${DOCUMENTATION_ROOT:-/documentation}"
echo "[rag] Database host: ${RAG_DB_HOST:-rag-db}"
echo "[rag] Database name: ${RAG_DB_NAME:-redmine_assistant}"

python -m cli wait-for-db \
    --timeout "${RAG_DB_WAIT_TIMEOUT:-60}" \
    --interval "${RAG_DB_WAIT_INTERVAL:-2}"

python -m cli migrate

exec gunicorn \
    --workers "${RAG_WORKERS:-1}" \
    --threads "${RAG_THREADS:-4}" \
    --bind "0.0.0.0:${RAG_PORT:-8001}" \
    --timeout "${RAG_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile - \
    api:app

