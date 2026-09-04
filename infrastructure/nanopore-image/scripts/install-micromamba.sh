#!/usr/bin/env bash

set -euo pipefail

MICROMAMBA_VERSION="2.9.0"
MICROMAMBA_RELEASE="2.9.0-0"
MICROMAMBA_ARCHITECTURE="linux-64"

MICROMAMBA_INSTALL_ROOT="/opt/micromamba"
MICROMAMBA_BIN_DIRECTORY="${MICROMAMBA_INSTALL_ROOT}/bin"
MAMBA_ROOT_PREFIX="${MICROMAMBA_INSTALL_ROOT}/root"
MICROMAMBA_BINARY="${MICROMAMBA_BIN_DIRECTORY}/micromamba"

MICROMAMBA_ARCHIVE="micromamba-${MICROMAMBA_ARCHITECTURE}"
MICROMAMBA_URL="https://github.com/mamba-org/micromamba-releases/releases/download/${MICROMAMBA_RELEASE}/${MICROMAMBA_ARCHIVE}"

MICROMAMBA_SHA256="366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3"

DOWNLOAD_DIRECTORY="/var/tmp/micromamba"
DOWNLOAD_PATH="${DOWNLOAD_DIRECTORY}/${MICROMAMBA_ARCHIVE}"

cleanup() {
  local exit_status=$?

  sudo rm -rf \
    "$DOWNLOAD_DIRECTORY" \
    2>/dev/null || true

  return "$exit_status"
}

trap cleanup EXIT

echo "Installing Micromamba ${MICROMAMBA_VERSION}"

sudo install \
  -d \
  -m 0755 \
  "$DOWNLOAD_DIRECTORY" \
  "$MICROMAMBA_BIN_DIRECTORY" \
  "$MAMBA_ROOT_PREFIX" \
  /etc/foodport

echo "Downloading Micromamba from ${MICROMAMBA_URL}"

sudo curl \
  --fail \
  --location \
  --retry 5 \
  --retry-all-errors \
  --connect-timeout 30 \
  --output "$DOWNLOAD_PATH" \
  "$MICROMAMBA_URL"

sudo test -s "$DOWNLOAD_PATH"

echo "${MICROMAMBA_SHA256}  ${DOWNLOAD_PATH}" |
  sudo sha256sum --check -

sudo install \
  -m 0755 \
  "$DOWNLOAD_PATH" \
  "$MICROMAMBA_BINARY"

sudo ln -sfn \
  "$MICROMAMBA_BINARY" \
  /usr/local/bin/micromamba

resolved_micromamba="$(
  readlink \
    --canonicalize \
    /usr/local/bin/micromamba
)"

if [[ "$resolved_micromamba" != "$MICROMAMBA_BINARY" ]]; then
  echo \
    "Unexpected Micromamba symlink target: ${resolved_micromamba}" \
    >&2
  exit 1
fi

micromamba_version_output="$(
  "$MICROMAMBA_BINARY" --version
)"

echo "Micromamba version: ${micromamba_version_output}"

if [[ "$micromamba_version_output" != "$MICROMAMBA_VERSION" ]]; then
  echo \
    "Unexpected Micromamba version: ${micromamba_version_output}" \
    >&2
  exit 1
fi

sudo tee /etc/foodport/micromamba.json >/dev/null <<EOF
{
  "version": "${MICROMAMBA_VERSION}",
  "release": "${MICROMAMBA_RELEASE}",
  "architecture": "${MICROMAMBA_ARCHITECTURE}",
  "source": "${MICROMAMBA_URL}",
  "sha256": "${MICROMAMBA_SHA256}",
  "binary": "${MICROMAMBA_BINARY}",
  "root_prefix": "${MAMBA_ROOT_PREFIX}"
}
EOF

sudo chmod 0644 \
  /etc/foodport/micromamba.json

sudo chown -R \
  root:root \
  "$MICROMAMBA_INSTALL_ROOT"

echo "Micromamba installation completed"

cleanup
trap - EXIT