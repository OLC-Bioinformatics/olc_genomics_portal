#!/usr/bin/env bash
set -euo pipefail

for command_name in azcopy base64 find wc awk; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "${command_name} is not installed or is not on PATH" >&2; exit 1; }
done

CONTAINER_SAS_URL_BASE64="${CONTAINER_SAS_URL_BASE64:?CONTAINER_SAS_URL_BASE64 is required}"
CONTAINER_SAS_URL="$(printf '%s' "$CONTAINER_SAS_URL_BASE64" | base64 --decode)"
unset CONTAINER_SAS_URL_BASE64
[[ -n "$CONTAINER_SAS_URL" ]] || { echo "Decoded container SAS URL is empty" >&2; exit 1; }
ACCEPTANCE_POD5_DIRECTORY="${ACCEPTANCE_POD5_DIRECTORY:?ACCEPTANCE_POD5_DIRECTORY is required}"
ACCEPTANCE_POD5_FILES_BASE64="${ACCEPTANCE_POD5_FILES_BASE64:?ACCEPTANCE_POD5_FILES_BASE64 is required}"
ACCEPTANCE_POD5_FILES="$(printf '%s' "$ACCEPTANCE_POD5_FILES_BASE64" | base64 --decode)"
unset ACCEPTANCE_POD5_FILES_BASE64
[[ -n "$ACCEPTANCE_POD5_FILES" ]] || { echo "Decoded POD5 allowlist is empty" >&2; exit 1; }
EXPECTED_POD5_COUNT="${EXPECTED_POD5_COUNT:-23}"
INPUT_DIRECTORY="${INPUT_DIRECTORY:-${AZ_BATCH_TASK_WORKING_DIR:-/mnt/resource}/nanopore-acceptance/download}"
case "$EXPECTED_POD5_COUNT" in ''|*[!0-9]*|0) echo "EXPECTED_POD5_COUNT must be positive" >&2; exit 1;; esac
IFS=';' read -r -a pod5_files <<<"$ACCEPTANCE_POD5_FILES"
[[ "${#pod5_files[@]}" -eq "$EXPECTED_POD5_COUNT" ]] || { echo "Allowlist count does not match EXPECTED_POD5_COUNT" >&2; exit 1; }
rm -rf "$INPUT_DIRECTORY"; mkdir -p "$INPUT_DIRECTORY"
for pod5_name in "${pod5_files[@]}"; do
  [[ -n "$pod5_name" && "$pod5_name" != */* && "$pod5_name" == *.pod5 ]] || { echo "Invalid POD5 entry: ${pod5_name}" >&2; exit 1; }
  blob_path="${ACCEPTANCE_POD5_DIRECTORY%/}/${pod5_name}"
  echo "Downloading acceptance POD5 blob: ${blob_path}"
  azcopy copy "$CONTAINER_SAS_URL" "$INPUT_DIRECTORY" --recursive=true --include-path="$blob_path"
done
unset CONTAINER_SAS_URL
pod5_count="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' | wc -l)"
pod5_bytes="$(find "$INPUT_DIRECTORY" -type f -iname '*.pod5' -printf '%s\n' | awk '{n+=$1} END {print n+0}')"
[[ "$pod5_count" -eq "$EXPECTED_POD5_COUNT" && "$pod5_bytes" -gt 0 ]] || { echo "Downloaded POD5 inventory is invalid" >&2; exit 1; }
echo "Acceptance POD5 download completed"
echo "POD5 file count: ${pod5_count}"
echo "POD5 bytes: ${pod5_bytes}"
find "$INPUT_DIRECTORY" -type f -iname '*.pod5' -print | LC_ALL=C sort -V
