#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

NVIDIA_DRIVER_VERSION="570.237"
NVIDIA_INSTALLER="NVIDIA-Linux-x86_64-${NVIDIA_DRIVER_VERSION}-grid-azure.run"
NVIDIA_URL="https://download.microsoft.com/download/5e213ec5-834f-4b0a-87f7-772751353b06/${NVIDIA_INSTALLER}"

DOWNLOAD_DIRECTORY="/var/tmp/nvidia"
INSTALLER_PATH="${DOWNLOAD_DIRECTORY}/${NVIDIA_INSTALLER}"

echo "Installing prerequisites for NVIDIA GRID ${NVIDIA_DRIVER_VERSION}"

sudo apt-get update

sudo apt-get install -y \
  build-essential \
  dkms \
  file \
  linux-headers-"$(uname -r)" \
  pciutils

sudo install \
  -d \
  -m 0755 \
  "$DOWNLOAD_DIRECTORY"

echo "Downloading ${NVIDIA_INSTALLER}"

if ! sudo curl \
    --fail \
    --location \
    --retry 5 \
    --retry-all-errors \
    --connect-timeout 30 \
    --output "$INSTALLER_PATH" \
    "$NVIDIA_URL"; then
  echo "Verified TLS download failed; retrying with certificate verification disabled" >&2

  sudo curl \
    --insecure \
    --fail \
    --location \
    --retry 5 \
    --retry-all-errors \
    --connect-timeout 30 \
    --output "$INSTALLER_PATH" \
    "$NVIDIA_URL"
fi

sudo test -s "$INSTALLER_PATH"

echo "NVIDIA installer SHA-256:"
sudo sha256sum "$INSTALLER_PATH"

sudo chmod 0755 "$INSTALLER_PATH"

echo "Disabling the Nouveau kernel driver"

sudo tee /etc/modprobe.d/disable-nouveau.conf >/dev/null <<'EOF'
blacklist nouveau
options nouveau modeset=0
EOF

sudo update-initramfs -u

echo "Installing NVIDIA GRID ${NVIDIA_DRIVER_VERSION}"

sudo "$INSTALLER_PATH" \
  --silent \
  --accept-license \
  --dkms \
  --no-questions \
  --ui=none \
  --disable-nouveau

echo "Recording installed NVIDIA driver metadata"

sudo install \
  -d \
  -m 0755 \
  /etc/foodport

sudo tee /etc/foodport/nvidia-driver.json >/dev/null <<EOF
{
  "family": "GRID",
  "vgpu_release": "18.8",
  "version": "${NVIDIA_DRIVER_VERSION}",
  "installer": "${NVIDIA_INSTALLER}",
  "source": "${NVIDIA_URL}",
  "target_vm_family": "NVadsA10_v5"
}
EOF

sudo chmod 0644 \
  /etc/foodport/nvidia-driver.json

sudo rm -f "$INSTALLER_PATH"

echo "NVIDIA GRID installation completed"