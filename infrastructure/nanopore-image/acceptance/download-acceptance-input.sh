#!/usr/bin/env bash

set -euo pipefail

if ! command -v base64 >/dev/null 2>&1; then
  echo "base64 is not installed or is not on PATH" >&2
  exit 1
fi

if ! command -v azcopy >/dev/null 2>&1; then
  echo "AzCopy is not installed or is not on PATH" >&2
  exit 1
fi

CONTAINER_SAS_URL_BASE64="${CONTAINER_SAS_URL_BASE64:?CONTAINER_SAS_URL_BASE64 is required}"

CONTAINER_SAS_URL="$(
  printf '%s' "$CONTAINER_SAS_URL_BASE64" |
    base64 --decode
)"

unset CONTAINER_SAS_URL_BASE64

if [[ -z "$CONTAINER_SAS_URL" ]]; then
  echo "Decoded container SAS URL is empty" >&2
  exit 1
fi

ACCEPTANCE_POD5_PATH="${ACCEPTANCE_POD5_PATH:?ACCEPTANCE_POD5_PATH is required}"
INPUT_DIRECTORY="${INPUT_DIRECTORY:-${AZ_BATCH_TASK_WORKING_DIR:-/mnt/resource}/nanopore-acceptance/input}"

echo "Preparing acceptance input directory: ${INPUT_DIRECTORY}"

mkdir -p "$INPUT_DIRECTORY"

echo "Downloading acceptance POD5 blob"
echo "Blob: ${ACCEPTANCE_POD5_PATH}"

azcopy copy \
  "$CONTAINER_SAS_URL" \
  "$INPUT_DIRECTORY" \
  --recursive=true \
  --include-path="$ACCEPTANCE_POD5_PATH"

pod5_file="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' \
    -print \
    -quit
)"

if [[ -z "$pod5_file" ]]; then
  echo \
    "No POD5 file was downloaded beneath ${INPUT_DIRECTORY}" \
    >&2
  exit 1
fi

pod5_count="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' |
  wc -l
)"

pod5_bytes="$(
  find \
    "$INPUT_DIRECTORY" \
    -type f \
    -iname '*.pod5' \
    -printf '%s\n' |
  awk '{total += $1} END {print total + 0}'
)"

unset CONTAINER_SAS_URL

echo "Acceptance POD5 download completed"
echo "POD5 file count: ${pod5_count}"
echo "POD5 bytes: ${pod5_bytes}"
echo "First POD5 file: ${pod5_file}"