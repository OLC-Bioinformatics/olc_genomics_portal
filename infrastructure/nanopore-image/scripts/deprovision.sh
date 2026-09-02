#!/usr/bin/env bash

set -euo pipefail

sudo waagent -deprovision+user -force

echo "Azure VM deprovisioning completed"
