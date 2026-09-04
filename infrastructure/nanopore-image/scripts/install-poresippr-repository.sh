#!/usr/bin/env bash

set -euo pipefail

: "${PORESIPPR_REPOSITORY_URL:?Repository URL is required}"
: "${PORESIPPR_REPOSITORY_COMMIT:?Repository commit is required}"

PORESIPPR_INSTALL_DIRECTORY="/opt/foodport/poresippr"
PORESIPPR_SCHEDULER_NAME="poresippr_incremental_dorado_scheduler.py"
PORESIPPR_SCHEDULER="${PORESIPPR_INSTALL_DIRECTORY}/${PORESIPPR_SCHEDULER_NAME}"

PORESIPPR_TEST_RELATIVE_PATH="$(
  printf '%s' \
    "tests/" \
    "test_poresippr_incremental_dorado_scheduler.py"
)"

PORESIPPR_TEST="$(
  printf '%s' \
    "${PORESIPPR_INSTALL_DIRECTORY}/" \
    "$PORESIPPR_TEST_RELATIVE_PATH"
)"

PORESIPPR_PYTHON="/opt/micromamba/root/envs/poresippr/bin/python"
PORESIPPR_MANIFEST="/etc/foodport/poresippr-repository.json"

temporary_directory=""

cleanup() {
  local exit_status=$?

  if [[ -n "${temporary_directory:-}" ]]; then
    rm -rf \
      "$temporary_directory" \
      2>/dev/null || true
  fi

  return "$exit_status"
}

trap cleanup EXIT

if [[ ! "$PORESIPPR_REPOSITORY_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo \
    "PoreSippR commit must be a full lowercase 40-character SHA-1: " \
    "$PORESIPPR_REPOSITORY_COMMIT" \
    >&2
  exit 1
fi

if [[ ! -x "$PORESIPPR_PYTHON" ]]; then
  echo \
    "PoreSippR environment Python is missing: " \
    "$PORESIPPR_PYTHON" \
    >&2
  exit 1
fi

temporary_directory="$(
  mktemp \
    --directory \
    /var/tmp/poresippr-repository.XXXXXXXX
)"

repository_directory="${temporary_directory}/repository"

echo \
  "Retrieving PoreSippR commit " \
  "$PORESIPPR_REPOSITORY_COMMIT"

git init \
  --quiet \
  "$repository_directory"

git \
  -C "$repository_directory" \
  remote add \
  origin \
  "$PORESIPPR_REPOSITORY_URL"

git \
  -C "$repository_directory" \
  fetch \
  --quiet \
  --depth 1 \
  origin \
  "$PORESIPPR_REPOSITORY_COMMIT"

git \
  -C "$repository_directory" \
  checkout \
  --quiet \
  --detach \
  FETCH_HEAD

resolved_commit="$(
  git \
    -C "$repository_directory" \
    rev-parse \
    HEAD
)"

if [[ "$resolved_commit" != "$PORESIPPR_REPOSITORY_COMMIT" ]]; then
  echo "Unexpected PoreSippR repository commit" >&2
  echo "Expected: ${PORESIPPR_REPOSITORY_COMMIT}" >&2
  echo "Actual:   ${resolved_commit}" >&2
  exit 1
fi

source_scheduler="${repository_directory}/${PORESIPPR_SCHEDULER_NAME}"

if [[ ! -f "$source_scheduler" ]]; then
  echo \
    "Incremental Dorado scheduler is missing from the repository: " \
    "$source_scheduler" \
    >&2
  exit 1
fi

if grep -Eq \
    '&(amp|gt|lt);' \
    "$source_scheduler"; then
  echo \
    "Incremental Dorado scheduler contains HTML-escaped source text" \
    >&2
  exit 1
fi

source_test="${repository_directory}/${PORESIPPR_TEST_RELATIVE_PATH}"

if [[ ! -f "$source_test" ]]; then
  echo \
    "Incremental Dorado scheduler tests are missing: " \
    "$source_test" \
    >&2
  exit 1
fi

echo \
  "Installing PoreSippR into " \
  "$PORESIPPR_INSTALL_DIRECTORY"

sudo rm -rf \
  "$PORESIPPR_INSTALL_DIRECTORY"

sudo install \
  -d \
  -m 0755 \
  "$PORESIPPR_INSTALL_DIRECTORY" \
  /etc/foodport

sudo rsync \
  --archive \
  --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "${repository_directory}/" \
  "${PORESIPPR_INSTALL_DIRECTORY}/"

sudo chmod 0755 \
  "$PORESIPPR_SCHEDULER"

if [[ ! -f "$PORESIPPR_TEST" ]]; then
  echo \
    "Installed scheduler test file is missing: " \
    "$PORESIPPR_TEST" \
    >&2
  exit 1
fi

sudo chown -R \
  root:root \
  "$PORESIPPR_INSTALL_DIRECTORY"

echo "Validating installed scheduler syntax"

sudo "$PORESIPPR_PYTHON" \
  -m py_compile \
  "$PORESIPPR_SCHEDULER"

echo "Validating installed scheduler command-line interface"

"$PORESIPPR_PYTHON" \
  "$PORESIPPR_SCHEDULER" \
  --help \
  >/dev/null

sudo find \
  "$PORESIPPR_INSTALL_DIRECTORY" \
  -type d \
  -name '__pycache__' \
  -prune \
  -exec rm -rf {} +

sudo find \
  "$PORESIPPR_INSTALL_DIRECTORY" \
  -type f \
  -name '*.pyc' \
  -delete

scheduler_sha256="$(
  sha256sum "$PORESIPPR_SCHEDULER" |
    awk '{print $1}'
)"

echo "Recording PoreSippR repository metadata"

sudo tee "$PORESIPPR_MANIFEST" >/dev/null <<EOF
{
  "repository": "${PORESIPPR_REPOSITORY_URL}",
  "commit": "${PORESIPPR_REPOSITORY_COMMIT}",
  "source_branch": "madhubioinfo-dorado-patch1",
  "install_path": "${PORESIPPR_INSTALL_DIRECTORY}",
  "scheduler": "${PORESIPPR_SCHEDULER}",
  "scheduler_test": "${PORESIPPR_TEST}",
  "scheduler_sha256": "${scheduler_sha256}"
}
EOF

sudo chmod 0644 \
  "$PORESIPPR_MANIFEST"

jq -e \
  --arg repository "$PORESIPPR_REPOSITORY_URL" \
  --arg commit "$PORESIPPR_REPOSITORY_COMMIT" \
  --arg install_path "$PORESIPPR_INSTALL_DIRECTORY" \
  --arg scheduler "$PORESIPPR_SCHEDULER" \
  --arg scheduler_test "$PORESIPPR_TEST" \
  --arg scheduler_sha256 "$scheduler_sha256" \
  '
    .repository == $repository
    and .commit == $commit
    and .install_path == $install_path
    and .scheduler == $scheduler
    and .scheduler_test == $scheduler_test
    and .scheduler_sha256 == $scheduler_sha256
  ' \
  "$PORESIPPR_MANIFEST" \
  >/dev/null || {
    echo "Unexpected PoreSippR repository metadata" >&2
    cat "$PORESIPPR_MANIFEST" >&2
    exit 1
  }

echo "Installed PoreSippR repository commit: ${resolved_commit}"
echo "Installed scheduler: ${PORESIPPR_SCHEDULER}"
echo "Scheduler SHA-256: ${scheduler_sha256}"
echo "PoreSippR repository installation completed"

cleanup
trap - EXIT