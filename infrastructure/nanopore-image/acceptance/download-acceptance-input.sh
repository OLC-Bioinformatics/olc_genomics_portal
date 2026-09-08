#!/usr/bin/env bash

set -euo pipefail

for command_name in azcopy base64; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "${command_name} is not installed or is not on PATH" >&2
    exit 1
  fi
done

CONTAINER_SAS_URL_BASE64="${CONTAINER_SAS_URL_BASE64:?CONTAINER_SAS_URL_BASE64 is required}"
CONTAINER_SAS_URL="$(printf '%s' "$CONTAINER_SAS_URL_BASE64" | base64 --decode)"
unset CONTAINER_SAS_URL_BASE64

if [[ -z "$CONTAINER_SAS_URL" ]]; then
  echo "Decoded container SAS URL is empty" >&2
  exit 1
fi

ACCEPTANCE_POD5_PATH="${ACCEPTANCE_POD5_PATH:?ACCEPTANCE_POD5_PATH is required}"
INPUT_DIRECTORY="${INPUT_DIRECTORY:-${AZ_BATCH_TASK_WORKING_DIR:-/mnt/resource}/nanopore-acceptance/input}"

rm -rf "$INPUT_DIRECTORY"
mkdir -p "$INPUT_DIRECTORY"

echo "Downloading acceptance POD5 blob"
echo "Blob: ${ACCEPTANCE_POD5_PATH}"

azcopy copy \
  "$CONTAINER_SAS_URL" \
  "$INPUT_DIRECTORY" \
  --recursive=true \
  --include-path="$ACCEPTANCE_POD5_PATH"

unset CONTAINER_SAS_URL

pod5_count="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' | wc -l)"
pod5_bytes="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' -printf '%s\n' | awk '{total += $1} END {print total + 0}')"
pod5_file="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' -print -quit)"

if [[ "$pod5_count" -ne 1 ]] || [[ -z "$pod5_file" ]] || [[ "$pod5_bytes" -lt 1 ]]; then
  echo "Expected exactly one nonempty POD5 file, found ${pod5_count}" >&2
  exit 1
fi

echo "Acceptance POD5 download completed"
echo "POD5 file count: ${pod5_count}"
echo "POD5 bytes: ${pod5_bytes}"
echo "POD5 file: ${pod5_file}"
