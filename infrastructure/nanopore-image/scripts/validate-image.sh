#!/usr/bin/env bash

set -euo pipefail

test -f /etc/foodport/image.json

jq -e \
  '.image == "nanopore"' \
  /etc/foodport/image.json >/dev/null

jq -e \
  '.build_stage == "connectivity-proof"' \
  /etc/foodport/image.json >/dev/null

command -v python3 >/dev/null
command -v curl >/dev/null
command -v jq >/dev/null
command -v waagent >/dev/null

python3 --version
curl --version
jq --version
waagent -version

echo "FoodPort image validation completed successfully"
