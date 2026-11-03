#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update

sudo apt-get install -y \
  bzip2 \
  ca-certificates \
  curl \
  file \
  git \
  gzip \
  jq \
  python3 \
  python3-pip \
  rsync \
  tar \
  unzip \
  walinuxagent


sudo install \
  -d \
  -m 0755 \
  /opt/foodport \
  /etc/foodport

sudo install \
  -m 0644 \
  /tmp/foodport-image.json \
  /etc/foodport/image.json

echo "Base FoodPort image provisioning completed"
