#!/usr/bin/env bash

set -euo pipefail

MICROMAMBA="/opt/micromamba/bin/micromamba"
MAMBA_ROOT_PREFIX="/opt/micromamba/root"

ENVIRONMENT_NAME="poresippr"
ENVIRONMENT_DIRECTORY="${MAMBA_ROOT_PREFIX}/envs/${ENVIRONMENT_NAME}"
ENVIRONMENT_FILE="/tmp/poresippr-environment.yml"

MANIFEST_DIRECTORY="/opt/ont/manifests"
INSTALLED_ENVIRONMENT_FILE="${MANIFEST_DIRECTORY}/poresippr-environment.yml"
PACKAGE_MANIFEST="${MANIFEST_DIRECTORY}/poresippr-conda-list.txt"
EXPLICIT_MANIFEST="${MANIFEST_DIRECTORY}/poresippr-conda-explicit.txt"
RUNTIME_MANIFEST="/etc/foodport/poresippr-runtime.json"

get_pod5_version() {
  local pod5_binary="$1"
  local python_binary="$2"

  if "$pod5_binary" --version 2>/dev/null; then
    return 0
  fi

  "$python_binary" - <<'PY'
from importlib.metadata import version

print("pod5 {0}".format(version("pod5")))
PY
}

if [[ ! -x "$MICROMAMBA" ]]; then
  echo "Micromamba is missing: ${MICROMAMBA}" >&2
  exit 1
fi

if [[ ! -f "$ENVIRONMENT_FILE" ]]; then
  echo \
    "PoreSippR environment file is missing: ${ENVIRONMENT_FILE}" \
    >&2
  exit 1
fi

echo "Creating PoreSippR Micromamba environment"

sudo install \
  -d \
  -m 0755 \
  "$MAMBA_ROOT_PREFIX" \
  "$MANIFEST_DIRECTORY" \
  /etc/foodport

environment_source_sha256="$(
  sha256sum "$ENVIRONMENT_FILE" |
    awk '{print $1}'
)"

if [[ -z "$environment_source_sha256" ]]; then
  echo \
    "Could not calculate the PoreSippR environment checksum" \
    >&2
  exit 1
fi

sudo install \
  -m 0644 \
  "$ENVIRONMENT_FILE" \
  "$INSTALLED_ENVIRONMENT_FILE"

sudo env \
  MAMBA_ROOT_PREFIX="$MAMBA_ROOT_PREFIX" \
  "$MICROMAMBA" \
    create \
    --yes \
    --name "$ENVIRONMENT_NAME" \
    --file "$ENVIRONMENT_FILE"

if [[ ! -d "$ENVIRONMENT_DIRECTORY" ]]; then
  echo \
    "PoreSippR environment directory was not created: " \
    "$ENVIRONMENT_DIRECTORY" \
    >&2
  exit 1
fi

required_commands=(
  python
  minimap2
  samtools
  pod5
)

for command_name in "${required_commands[@]}"; do
  command_path="${ENVIRONMENT_DIRECTORY}/bin/${command_name}"

  if [[ ! -x "$command_path" ]]; then
    echo \
      "Required environment command is missing: ${command_path}" \
      >&2
    exit 1
  fi
done

echo "Validating installed PoreSippR commands"

"${ENVIRONMENT_DIRECTORY}/bin/python" --version
"${ENVIRONMENT_DIRECTORY}/bin/minimap2" --version
"${ENVIRONMENT_DIRECTORY}/bin/samtools" --version

get_pod5_version \
  "${ENVIRONMENT_DIRECTORY}/bin/pod5" \
  "${ENVIRONMENT_DIRECTORY}/bin/python"

echo "Validating installed Python packages"

"${ENVIRONMENT_DIRECTORY}/bin/python" - <<'PY'
from importlib.metadata import version

import pandas
import pod5
import pysam
import requests
import yaml

print("Python package imports succeeded")

for package_name in (
    "pandas",
    "pod5",
    "pysam",
    "requests",
    "PyYAML",
):
    print(
        "{0}: {1}".format(
            package_name,
            version(package_name),
        )
    )
PY

echo "Recording the resolved PoreSippR environment"

sudo env \
  MAMBA_ROOT_PREFIX="$MAMBA_ROOT_PREFIX" \
  "$MICROMAMBA" \
    list \
    --name "$ENVIRONMENT_NAME" |
  sudo tee \
    "$PACKAGE_MANIFEST" \
    >/dev/null

sudo env \
  MAMBA_ROOT_PREFIX="$MAMBA_ROOT_PREFIX" \
  "$MICROMAMBA" \
    list \
    --name "$ENVIRONMENT_NAME" \
    --explicit |
  sudo tee \
    "$EXPLICIT_MANIFEST" \
    >/dev/null

if [[ ! -s "$PACKAGE_MANIFEST" ]]; then
  echo \
    "PoreSippR package manifest is missing or empty: " \
    "$PACKAGE_MANIFEST" \
    >&2
  exit 1
fi

if [[ ! -s "$EXPLICIT_MANIFEST" ]]; then
  echo \
    "PoreSippR explicit environment manifest is missing or empty: " \
    "$EXPLICIT_MANIFEST" \
    >&2
  exit 1
fi

python_version="$(
  "${ENVIRONMENT_DIRECTORY}/bin/python" \
    --version \
    2>&1
)"

minimap2_version="$(
  "${ENVIRONMENT_DIRECTORY}/bin/minimap2" \
    --version \
    2>&1 |
  head -n 1
)"

samtools_version="$(
  "${ENVIRONMENT_DIRECTORY}/bin/samtools" \
    --version \
    2>&1 |
  head -n 1
)"

pod5_version="$(
  get_pod5_version \
    "${ENVIRONMENT_DIRECTORY}/bin/pod5" \
    "${ENVIRONMENT_DIRECTORY}/bin/python" |
  head -n 1
)"

echo "Writing PoreSippR runtime metadata"

sudo tee "$RUNTIME_MANIFEST" >/dev/null <<EOF
{
  "environment_name": "${ENVIRONMENT_NAME}",
  "environment_path": "${ENVIRONMENT_DIRECTORY}",
  "environment_source": "${INSTALLED_ENVIRONMENT_FILE}",
  "environment_source_sha256": "${environment_source_sha256}",
  "python": "${python_version}",
  "minimap2": "${minimap2_version}",
  "samtools": "${samtools_version}",
  "pod5": "${pod5_version}",
  "package_manifest": "${PACKAGE_MANIFEST}",
  "explicit_manifest": "${EXPLICIT_MANIFEST}"
}
EOF

sudo chmod 0644 \
  "$RUNTIME_MANIFEST" \
  "$INSTALLED_ENVIRONMENT_FILE" \
  "$PACKAGE_MANIFEST" \
  "$EXPLICIT_MANIFEST"

echo "Validating PoreSippR runtime metadata"

jq -e \
  --arg expected_name "$ENVIRONMENT_NAME" \
  --arg expected_path "$ENVIRONMENT_DIRECTORY" \
  --arg expected_source "$INSTALLED_ENVIRONMENT_FILE" \
  --arg expected_sha256 "$environment_source_sha256" \
  --arg expected_package_manifest "$PACKAGE_MANIFEST" \
  --arg expected_explicit_manifest "$EXPLICIT_MANIFEST" \
  '
    .environment_name == $expected_name
    and .environment_path == $expected_path
    and .environment_source == $expected_source
    and .environment_source_sha256 == $expected_sha256
    and .package_manifest == $expected_package_manifest
    and .explicit_manifest == $expected_explicit_manifest
    and (.python | type == "string" and length > 0)
    and (.minimap2 | type == "string" and length > 0)
    and (.samtools | type == "string" and length > 0)
    and (.pod5 | type == "string" and length > 0)
  ' \
  "$RUNTIME_MANIFEST" \
  >/dev/null || {
    echo "Unexpected PoreSippR runtime metadata" >&2
    cat "$RUNTIME_MANIFEST" >&2
    exit 1
  }

installed_environment_sha256="$(
  sha256sum "$INSTALLED_ENVIRONMENT_FILE" |
    awk '{print $1}'
)"

if [[ "$installed_environment_sha256" != "$environment_source_sha256" ]]; then
  echo \
    "Installed environment specification checksum does not match" \
    >&2
  echo "Expected: ${environment_source_sha256}" >&2
  echo "Actual:   ${installed_environment_sha256}" >&2
  exit 1
fi

echo "Cleaning Micromamba package caches"

sudo env \
  MAMBA_ROOT_PREFIX="$MAMBA_ROOT_PREFIX" \
  "$MICROMAMBA" \
    clean \
    --all \
    --yes

sudo chown -R \
  root:root \
  /opt/micromamba \
  "$MANIFEST_DIRECTORY"

sudo rm -f "$ENVIRONMENT_FILE"

echo "PoreSippR environment installation completed"