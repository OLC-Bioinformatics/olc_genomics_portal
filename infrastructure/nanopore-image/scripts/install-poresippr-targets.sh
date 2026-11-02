#!/usr/bin/env bash

set -euo pipefail

: "${PORESIPPR_TARGETS_URL:?PoreSippR targets SAS URL is required}"
: "${PORESIPPR_TARGETS_SHA256:?PoreSippR targets SHA-256 is required}"

TARGETS_NAME="PoreSippR_DB_251110.fasta"
TARGETS_DIRECTORY="/opt/foodport/poresippr-data"
TARGETS_PATH="${TARGETS_DIRECTORY}/${TARGETS_NAME}"
TARGETS_MANIFEST="/etc/foodport/poresippr-targets.json"
DOWNLOAD_PATH="/var/tmp/${TARGETS_NAME}"
SOURCE_WITHOUT_QUERY="${PORESIPPR_TARGETS_URL%%\?*}"

cleanup() {
  local exit_status=$?

  rm -f \
    "$DOWNLOAD_PATH" \
    2>/dev/null || true

  return "$exit_status"
}

trap cleanup EXIT

if [[ ! "$PORESIPPR_TARGETS_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo \
    "PoreSippR targets checksum must be a lowercase SHA-256" \
    >&2
  exit 1
fi

if [[ "$PORESIPPR_TARGETS_URL" != https://* ]]; then
  echo "PoreSippR targets URL must use HTTPS" >&2
  exit 1
fi

if [[ "$SOURCE_WITHOUT_QUERY" != */"$TARGETS_NAME" ]]; then
  echo \
    "PoreSippR targets URL does not identify ${TARGETS_NAME}" \
    >&2
  exit 1
fi

if [[ "$SOURCE_WITHOUT_QUERY" == *"?"* ]]; then
  echo \
    "PoreSippR targets metadata source unexpectedly contains a query" \
    >&2
  exit 1
fi

echo "Downloading checksum-pinned PoreSippR targets"

echo \
  "TLS certificate verification is disabled for this checksum-pinned " \
  "Azure Blob download"

curl \
  --fail \
  --insecure \
  --location \
  --retry 5 \
  --retry-all-errors \
  --output "$DOWNLOAD_PATH" \
  "$PORESIPPR_TARGETS_URL"

test -s "$DOWNLOAD_PATH"

echo "${PORESIPPR_TARGETS_SHA256}  ${DOWNLOAD_PATH}" |
  sha256sum \
    --check \
    --strict

first_character="$(head -c 1 "$DOWNLOAD_PATH")"
if [[ "$first_character" != ">" ]]; then
  echo "PoreSippR targets file is not FASTA-formatted" >&2
  exit 1
fi

sequence_count="$(
  grep -c '^>' \
    "$DOWNLOAD_PATH" || true
)"

if [[ "$sequence_count" -lt 1 ]]; then
  echo "PoreSippR targets FASTA contains no records" >&2
  exit 1
fi

sudo install \
  -d \
  -m 0755 \
  "$TARGETS_DIRECTORY" \
  /etc/foodport

sudo install \
  -m 0644 \
  "$DOWNLOAD_PATH" \
  "$TARGETS_PATH"

sudo chown \
  root:root \
  "$TARGETS_PATH"

targets_bytes="$(stat --format='%s' "$TARGETS_PATH")"

sudo tee "$TARGETS_MANIFEST" >/dev/null <<EOF
{
  "name": "${TARGETS_NAME}",
  "source": "${SOURCE_WITHOUT_QUERY}",
  "path": "${TARGETS_PATH}",
  "sha256": "${PORESIPPR_TARGETS_SHA256}",
  "bytes": ${targets_bytes},
  "sequence_count": ${sequence_count}
}
EOF

sudo chmod 0644 \
  "$TARGETS_MANIFEST"

jq -e \
  --arg name "$TARGETS_NAME" \
  --arg source "$SOURCE_WITHOUT_QUERY" \
  --arg path "$TARGETS_PATH" \
  --arg sha256 "$PORESIPPR_TARGETS_SHA256" \
  --argjson bytes "$targets_bytes" \
  --argjson sequence_count "$sequence_count" \
  '
    .name == $name
    and .source == $source
    and .path == $path
    and .sha256 == $sha256
    and .bytes == $bytes
    and .sequence_count == $sequence_count
  ' \
  "$TARGETS_MANIFEST" \
  >/dev/null

echo "Installed PoreSippR targets: ${TARGETS_PATH}"
echo "PoreSippR targets SHA-256: ${PORESIPPR_TARGETS_SHA256}"
echo "PoreSippR target sequences: ${sequence_count}"

cleanup
trap - EXIT
