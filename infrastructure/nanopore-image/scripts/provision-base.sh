#!/usr/bin/env bash

set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update

sudo apt-get install -y \
  ca-certificates \
  curl \
  jq \
  python3 \
  python3-pip \
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
