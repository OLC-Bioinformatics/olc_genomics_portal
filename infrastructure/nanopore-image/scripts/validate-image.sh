#!/usr/bin/env bash

set -euo pipefail

IMAGE_MANIFEST="/etc/foodport/image.json"
NVIDIA_MANIFEST="/etc/foodport/nvidia-driver.json"
NOUVEAU_CONFIGURATION="/etc/modprobe.d/disable-nouveau.conf"

echo "Validating FoodPort image metadata"

if [[ ! -f "$IMAGE_MANIFEST" ]]; then
  echo "Missing image metadata: $IMAGE_MANIFEST" >&2
  exit 1
fi

jq -e \
  '.image == "nanopore"' \
  "$IMAGE_MANIFEST" >/dev/null || {
    echo "Image metadata does not identify the image as nanopore" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.environment == "development"' \
  "$IMAGE_MANIFEST" >/dev/null || {
    echo "Unexpected image environment" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.image_version == "0.0.4"' \
  "$IMAGE_MANIFEST" >/dev/null || {
    echo "Unexpected image version" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.build_stage == "poresippr-runtime"' \
  "$IMAGE_MANIFEST" >/dev/null || {
    echo "Unexpected image build stage" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

echo "Validating base image commands"

for command_name in python3 curl jq waagent; do
  if ! command -v "$command_name" >/dev/null; then
    echo "Required command is missing: $command_name" >&2
    exit 1
  fi
done

echo "Base component versions:"
python3 --version
curl_version_output="$(
  curl --version
)"

printf '%s\n' "$curl_version_output" |
  head -n 1
jq --version
waagent -version

echo "Validating NVIDIA driver metadata"

if [[ ! -f "$NVIDIA_MANIFEST" ]]; then
  echo "Missing NVIDIA driver metadata: $NVIDIA_MANIFEST" >&2
  exit 1
fi

jq -e \
  '.family == "GRID"' \
  "$NVIDIA_MANIFEST" >/dev/null || {
    echo "Unexpected NVIDIA driver family" >&2
    cat "$NVIDIA_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.vgpu_release == "18.8"' \
  "$NVIDIA_MANIFEST" >/dev/null || {
    echo "Unexpected NVIDIA vGPU release" >&2
    cat "$NVIDIA_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.version == "570.237"' \
  "$NVIDIA_MANIFEST" >/dev/null || {
    echo "Unexpected NVIDIA driver version" >&2
    cat "$NVIDIA_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.target_vm_family == "NVadsA10_v5"' \
  "$NVIDIA_MANIFEST" >/dev/null || {
    echo "Unexpected NVIDIA target VM family" >&2
    cat "$NVIDIA_MANIFEST" >&2
    exit 1
  }

echo "Validating Nouveau configuration"

if [[ ! -f "$NOUVEAU_CONFIGURATION" ]]; then
  echo "Missing Nouveau configuration: $NOUVEAU_CONFIGURATION" >&2
  exit 1
fi

grep -qxF \
  "blacklist nouveau" \
  "$NOUVEAU_CONFIGURATION" || {
    echo "Nouveau is not blocklisted" >&2
    cat "$NOUVEAU_CONFIGURATION" >&2
    exit 1
  }

grep -qxF \
  "options nouveau modeset=0" \
  "$NOUVEAU_CONFIGURATION" || {
    echo "Nouveau modesetting is not disabled" >&2
    cat "$NOUVEAU_CONFIGURATION" >&2
    exit 1
  }

echo "Validating NVIDIA utilities"

NVIDIA_SMI_PATH="$(command -v nvidia-smi || true)"

if [[ -z "$NVIDIA_SMI_PATH" ]]; then
  echo "nvidia-smi is not installed" >&2
  exit 1
fi

if [[ ! -x "$NVIDIA_SMI_PATH" ]]; then
  echo "nvidia-smi is not executable: $NVIDIA_SMI_PATH" >&2
  exit 1
fi

echo "nvidia-smi executable: $NVIDIA_SMI_PATH"

file "$NVIDIA_SMI_PATH"

echo \
  "Skipping NVIDIA device communication because the Packer build VM " \
  "does not contain an NVIDIA GPU"

echo "Validating NVIDIA kernel module metadata"

if modinfo nvidia >/dev/null 2>&1; then
  module_version="$(
    modinfo \
      -F version \
      nvidia
  )"

  echo "NVIDIA kernel module version: $module_version"

  if [[ "$module_version" != "570.237" ]]; then
    echo \
      "Unexpected NVIDIA module version: ${module_version}" \
      >&2
    exit 1
  fi

  modinfo nvidia |
    grep -E '^(filename|version|vermagic):'
else
  echo "NVIDIA kernel module metadata is unavailable" >&2
  exit 1
fi

NVIDIA_MODULE_PATH="$(modinfo -F filename nvidia)"

if [[ -z "$NVIDIA_MODULE_PATH" ]]; then
  echo "Could not determine the NVIDIA kernel module path" >&2
  exit 1
fi

if [[ ! -f "$NVIDIA_MODULE_PATH" ]]; then
  echo "NVIDIA kernel module does not exist: $NVIDIA_MODULE_PATH" >&2
  exit 1
fi

echo "NVIDIA kernel module: $NVIDIA_MODULE_PATH"

MODULE_VERMAGIC="$(modinfo -F vermagic nvidia)"
RUNNING_KERNEL="$(uname -r)"

echo "NVIDIA module vermagic: $MODULE_VERMAGIC"
echo "Packer build kernel: $RUNNING_KERNEL"

if [[ "$MODULE_VERMAGIC" != "$RUNNING_KERNEL "* ]]; then
  echo \
    "NVIDIA module was not built for the current kernel: " \
    "$MODULE_VERMAGIC" >&2
  exit 1
fi

echo "Installed NVIDIA-related packages:"
dpkg-query \
  -W \
  -f='${Package}\t${Version}\n' |
  grep -E '^(nvidia|libnvidia)' || true

echo "Image metadata:"
jq . "$IMAGE_MANIFEST"

echo "NVIDIA driver metadata:"
jq . "$NVIDIA_MANIFEST"

DORADO_VERSION="2.1.2"
DORADO_MODEL="dna_r10.4.1_e8.2_400bps_fast@v5.2.0"
DORADO_MANIFEST="/etc/foodport/dorado.json"
DORADO_BINARY="/usr/local/bin/dorado"
DORADO_MODEL_DIRECTORY="/opt/ont/models/${DORADO_MODEL}"
DORADO_CHECKSUM_MANIFEST="/etc/foodport/${DORADO_MODEL}.sha256"
DORADO_STABLE_BIN_DIRECTORY="/opt/ont/dorado/bin"
DORADO_VERSIONED_BIN_DIRECTORY="/opt/ont/dorado/${DORADO_VERSION}/bin"

echo "Validating Dorado installation"

if [[ ! -f "$DORADO_MANIFEST" ]]; then
  echo "Missing Dorado metadata: $DORADO_MANIFEST" >&2
  exit 1
fi

jq -e \
  '.version == "2.1.2"' \
  "$DORADO_MANIFEST" >/dev/null || {
    echo "Unexpected Dorado version in metadata" >&2
    cat "$DORADO_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.platform == "linux-x64"' \
  "$DORADO_MANIFEST" >/dev/null || {
    echo "Unexpected Dorado platform in metadata" >&2
    cat "$DORADO_MANIFEST" >&2
    exit 1
  }

jq -e \
  '.default_model == "dna_r10.4.1_e8.2_400bps_fast@v5.2.0"' \
  "$DORADO_MANIFEST" >/dev/null || {
    echo "Unexpected Dorado model in metadata" >&2
    cat "$DORADO_MANIFEST" >&2
    exit 1
  }

if [[ ! -x "$DORADO_BINARY" ]]; then
  echo "Dorado executable is missing: $DORADO_BINARY" >&2
  exit 1
fi

resolved_dorado="$(
  readlink \
    --canonicalize \
    "$DORADO_BINARY"
)"

expected_dorado="/opt/ont/dorado/${DORADO_VERSION}/bin/dorado"

if [[ "$resolved_dorado" != "$expected_dorado" ]]; then
  echo "Unexpected Dorado binary target: $resolved_dorado" >&2
  exit 1
fi

if [[ ! -L "$DORADO_STABLE_BIN_DIRECTORY" ]]; then
  echo \
    "Stable Dorado bin directory symlink is missing: " \
    "$DORADO_STABLE_BIN_DIRECTORY" \
    >&2
  exit 1
fi

resolved_dorado_bin_directory="$(
  readlink \
    --canonicalize \
    "$DORADO_STABLE_BIN_DIRECTORY"
)"

if [[ "$resolved_dorado_bin_directory" != "$DORADO_VERSIONED_BIN_DIRECTORY" ]]; then
  echo \
    "Unexpected stable Dorado bin directory target" \
    >&2
  echo "Expected: ${DORADO_VERSIONED_BIN_DIRECTORY}" >&2
  echo "Actual:   ${resolved_dorado_bin_directory}" >&2
  exit 1
fi

dorado_version_output="$(
  "$DORADO_BINARY" --version 2>&1
)"

echo "$dorado_version_output"

if [[ "$dorado_version_output" != *"$DORADO_VERSION"* ]]; then
  echo "Dorado did not report version ${DORADO_VERSION}" >&2
  exit 1
fi

if [[ ! -d "$DORADO_MODEL_DIRECTORY" ]]; then
  echo "Dorado model is missing: $DORADO_MODEL_DIRECTORY" >&2
  exit 1
fi

if [[ ! -f "$DORADO_CHECKSUM_MANIFEST" ]]; then
  echo "Dorado model checksum manifest is missing" >&2
  exit 1
fi

echo "Verifying Dorado model checksums"

if ! sha256sum \
    --check \
    "$DORADO_CHECKSUM_MANIFEST"; then
  echo "Dorado model checksum validation failed" >&2
  exit 1
fi


if [[ -z "$(
  find \
    "$DORADO_MODEL_DIRECTORY" \
    -type f \
    -print \
    -quit
)" ]]; then
  echo "Dorado model directory contains no files" >&2
  exit 1
fi

echo "Dorado executable: $resolved_dorado"
echo "Dorado model: $DORADO_MODEL_DIRECTORY"
echo "Dorado metadata:"
jq . "$DORADO_MANIFEST"

MICROMAMBA_VERSION="2.9.0"
MICROMAMBA_MANIFEST="/etc/foodport/micromamba.json"
MICROMAMBA_BINARY="/opt/micromamba/bin/micromamba"
MICROMAMBA_SYMLINK="/usr/local/bin/micromamba"
MAMBA_ROOT_PREFIX="/opt/micromamba/root"
MICROMAMBA_CONFIGURATION="${MAMBA_ROOT_PREFIX}/.mambarc"

PORESIPPR_MANIFEST="/etc/foodport/poresippr-runtime.json"
PORESIPPR_ENVIRONMENT_NAME="poresippr"
PORESIPPR_ENVIRONMENT="${MAMBA_ROOT_PREFIX}/envs/${PORESIPPR_ENVIRONMENT_NAME}"
PORESIPPR_BIN_DIRECTORY="${PORESIPPR_ENVIRONMENT}/bin"
PORESIPPR_PACKAGE_MANIFEST="/opt/ont/manifests/poresippr-conda-list.txt"
PORESIPPR_EXPLICIT_MANIFEST="/opt/ont/manifests/poresippr-conda-explicit.txt"
PORESIPPR_ENVIRONMENT_FILE="/opt/ont/manifests/poresippr-environment.yml"
PORESIPPR_REPOSITORY_MANIFEST="/etc/foodport/poresippr-repository.json"
PORESIPPR_REPOSITORY_URL="https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git"
PORESIPPR_REPOSITORY_COMMIT="691b3a3c2944139cb0093f81909331f7b8d46983"
PORESIPPR_INSTALL_DIRECTORY="/opt/foodport/poresippr"
PORESIPPR_SCHEDULER="${PORESIPPR_INSTALL_DIRECTORY}/poresippr_incremental_dorado_scheduler.py"
PORESIPPR_TARGETS_NAME="PoreSippR_DB_251110.fasta"
PORESIPPR_TARGETS_DIRECTORY="/opt/foodport/poresippr-data"
PORESIPPR_TARGETS_PATH="${PORESIPPR_TARGETS_DIRECTORY}/${PORESIPPR_TARGETS_NAME}"
PORESIPPR_TARGETS_MANIFEST="/etc/foodport/poresippr-targets.json"

PORESIPPR_TEST="$(
  printf '%s' \
    "${PORESIPPR_INSTALL_DIRECTORY}/tests/" \
    "test_poresippr_incremental_dorado_scheduler.py"
)"

echo "Validating Micromamba installation"

if [[ ! -f "$MICROMAMBA_MANIFEST" ]]; then
  echo "Missing Micromamba metadata: ${MICROMAMBA_MANIFEST}" >&2
  exit 1
fi

jq -e \
  --arg expected_version "$MICROMAMBA_VERSION" \
  --arg expected_binary "$MICROMAMBA_BINARY" \
  --arg expected_root_prefix "$MAMBA_ROOT_PREFIX" \
  --arg expected_configuration "$MICROMAMBA_CONFIGURATION" \
  '
    .version == $expected_version
    and .binary == $expected_binary
    and .root_prefix == $expected_root_prefix
    and .configuration == $expected_configuration
    and .ssl_verify == false
  ' \
  "$MICROMAMBA_MANIFEST" \
  >/dev/null || {
    echo "Unexpected Micromamba metadata" >&2
    cat "$MICROMAMBA_MANIFEST" >&2
    exit 1
  }

echo "Validating Micromamba TLS configuration"

if [[ ! -f "$MICROMAMBA_CONFIGURATION" ]]; then
  echo \
    "Micromamba configuration is missing: " \
    "$MICROMAMBA_CONFIGURATION" \
    >&2
  exit 1
fi

grep -Eq \
  '^[[:space:]]*ssl_verify:[[:space:]]*false[[:space:]]*$' \
  "$MICROMAMBA_CONFIGURATION" || {
    echo \
      "Micromamba configuration does not disable SSL verification" \
      >&2
    cat "$MICROMAMBA_CONFIGURATION" >&2
    exit 1
  }


if [[ ! -x "$MICROMAMBA_BINARY" ]]; then
  echo \
    "Micromamba executable is missing or not executable: " \
    "$MICROMAMBA_BINARY" \
    >&2
  exit 1
fi

if [[ ! -L "$MICROMAMBA_SYMLINK" ]]; then
  echo "Micromamba symlink is missing: ${MICROMAMBA_SYMLINK}" >&2
  exit 1
fi

resolved_micromamba="$(
  readlink \
    --canonicalize \
    "$MICROMAMBA_SYMLINK"
)"

if [[ "$resolved_micromamba" != "$MICROMAMBA_BINARY" ]]; then
  echo \
    "Unexpected Micromamba symlink target: " \
    "$resolved_micromamba" \
    >&2
  exit 1
fi

micromamba_version_output="$(
  "$MICROMAMBA_BINARY" --version
)"

echo "Micromamba version: ${micromamba_version_output}"

if [[ "$micromamba_version_output" != "$MICROMAMBA_VERSION" ]]; then
  echo \
    "Unexpected Micromamba version: " \
    "$micromamba_version_output" \
    >&2
  exit 1
fi

if [[ ! -d "$MAMBA_ROOT_PREFIX" ]]; then
  echo \
    "Micromamba root prefix is missing: " \
    "$MAMBA_ROOT_PREFIX" \
    >&2
  exit 1
fi

echo "Validating PoreSippR runtime metadata"

if [[ ! -f "$PORESIPPR_MANIFEST" ]]; then
  echo "Missing PoreSippR metadata: ${PORESIPPR_MANIFEST}" >&2
  exit 1
fi

jq -e \
  --arg expected_name "$PORESIPPR_ENVIRONMENT_NAME" \
  --arg expected_path "$PORESIPPR_ENVIRONMENT" \
  --arg expected_source "$PORESIPPR_ENVIRONMENT_FILE" \
  --arg expected_package_manifest "$PORESIPPR_PACKAGE_MANIFEST" \
  --arg expected_explicit_manifest "$PORESIPPR_EXPLICIT_MANIFEST" \
  '
    .environment_name == $expected_name
    and .environment_path == $expected_path
    and .environment_source == $expected_source
    and (
      (.environment_source_sha256 | type) == "string"
      and (.environment_source_sha256 | length) == 64
    )
    and .package_manifest == $expected_package_manifest
    and .explicit_manifest == $expected_explicit_manifest
    and (
      (.python | type) == "string"
      and (.python | length) > 0
    )
    and (
      (.minimap2 | type) == "string"
      and (.minimap2 | length) > 0
    )
    and (
      (.samtools | type) == "string"
      and (.samtools | length) > 0
    )
    and (
      (.pod5 | type) == "string"
      and (.pod5 | length) > 0
    )
    and (
      (.pytest | type) == "string"
      and (.pytest | length) > 0
    )
  ' \
  "$PORESIPPR_MANIFEST" \
  >/dev/null || {
    echo "Unexpected PoreSippR runtime metadata" >&2
    cat "$PORESIPPR_MANIFEST" >&2
    exit 1
  }

echo "Validating retained PoreSippR environment specification"

if [[ ! -s "$PORESIPPR_ENVIRONMENT_FILE" ]]; then
  echo \
    "PoreSippR environment specification is missing or empty: " \
    "$PORESIPPR_ENVIRONMENT_FILE" \
    >&2
  exit 1
fi

expected_environment_sha256="$(
  jq -r \
    '.environment_source_sha256 // empty' \
    "$PORESIPPR_MANIFEST"
)"

if [[ -z "$expected_environment_sha256" ]]; then
  echo \
    "PoreSippR metadata does not contain an environment checksum" \
    >&2
  exit 1
fi

actual_environment_sha256="$(
  sha256sum "$PORESIPPR_ENVIRONMENT_FILE" |
    awk '{print $1}'
)"

if [[ "$actual_environment_sha256" != "$expected_environment_sha256" ]]; then
  echo \
    "PoreSippR environment specification checksum mismatch" \
    >&2
  echo "Expected: ${expected_environment_sha256}" >&2
  echo "Actual:   ${actual_environment_sha256}" >&2
  exit 1
fi

echo \
  "PoreSippR environment specification SHA-256: " \
  "$actual_environment_sha256"


if [[ ! -d "$PORESIPPR_ENVIRONMENT" ]]; then
  echo \
    "PoreSippR environment is missing: " \
    "$PORESIPPR_ENVIRONMENT" \
    >&2
  exit 1
fi

if [[ ! -d "$PORESIPPR_BIN_DIRECTORY" ]]; then
  echo \
    "PoreSippR environment bin directory is missing: " \
    "$PORESIPPR_BIN_DIRECTORY" \
    >&2
  exit 1
fi

echo "Validating PoreSippR runtime commands"

required_environment_commands=(
  python
  minimap2
  samtools
  pod5
  pytest
)

for command_name in "${required_environment_commands[@]}"; do
  command_path="${PORESIPPR_BIN_DIRECTORY}/${command_name}"

  if [[ ! -x "$command_path" ]]; then
    echo \
      "Required PoreSippR command is missing or not executable: " \
      "$command_path" \
      >&2
    exit 1
  fi
done

runtime_path="$(
  printf '%s' \
    "${PORESIPPR_BIN_DIRECTORY}:" \
    "/opt/micromamba/bin:" \
    "/opt/ont/dorado/bin:" \
    "/usr/local/bin:" \
    "/usr/bin:" \
    "/bin"
)"

if [[ ! -d "/opt/ont/dorado/bin" ]]; then
  echo \
    "Dorado runtime bin directory is missing: " \
    "/opt/ont/dorado/bin" \
    >&2
  exit 1
fi

export MAMBA_ROOT_PREFIX
export PATH="$runtime_path"

expected_runtime_path="$(
  jq -r \
    '.poresippr.runtime_bin_path // empty' \
    "$IMAGE_MANIFEST"
)"

if [[ -z "$expected_runtime_path" ]]; then
  echo \
    "Image metadata does not define poresippr.runtime_bin_path" \
    >&2
  exit 1
fi

if [[ "$runtime_path" != "$expected_runtime_path" ]]; then
  echo "Runtime PATH does not match image metadata" >&2
  echo "Expected: ${expected_runtime_path}" >&2
  echo "Actual:   ${runtime_path}" >&2
  exit 1
fi

required_runtime_commands=(
  micromamba
  python
  minimap2
  samtools
  pod5
  pytest
  dorado
  git
  jq
)

for command_name in "${required_runtime_commands[@]}"; do
  command_path="$(
    command -v "$command_name" || true
  )"

  if [[ -z "$command_path" ]]; then
    echo \
      "Required runtime command is not on PATH: " \
      "$command_name" \
      >&2
    exit 1
  fi

  if [[ ! -x "$command_path" ]]; then
    echo \
      "Required runtime command is not executable: " \
      "$command_path" \
      >&2
    exit 1
  fi

  echo "${command_name}: ${command_path}"
done

echo "Validating runtime command resolution"

expected_runtime_commands=(
  "python:${PORESIPPR_BIN_DIRECTORY}/python"
  "minimap2:${PORESIPPR_BIN_DIRECTORY}/minimap2"
  "samtools:${PORESIPPR_BIN_DIRECTORY}/samtools"
  "pod5:${PORESIPPR_BIN_DIRECTORY}/pod5"
  "pytest:${PORESIPPR_BIN_DIRECTORY}/pytest"
  "micromamba:${MICROMAMBA_BINARY}"
  "dorado:${DORADO_BINARY}"
)

for command_mapping in "${expected_runtime_commands[@]}"; do
  command_name="${command_mapping%%:*}"
  expected_path="${command_mapping#*:}"

  actual_path="$(
    command -v "$command_name"
  )"

  actual_path="$(
    readlink \
      --canonicalize \
      "$actual_path"
  )"

  expected_path="$(
    readlink \
      --canonicalize \
      "$expected_path"
  )"

  if [[ "$actual_path" != "$expected_path" ]]; then
    echo \
      "Unexpected runtime command path for ${command_name}" \
      >&2
    echo "Expected: ${expected_path}" >&2
    echo "Actual:   ${actual_path}" >&2
    exit 1
  fi

  echo "${command_name} resolved correctly: ${actual_path}"
done

echo "PoreSippR runtime component versions:"

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

micromamba --version
python --version
minimap2 --version
samtools_version_output="$(
  samtools --version
)"

printf '%s\n' "$samtools_version_output" |
  head -n 1

get_pod5_version \
  "${PORESIPPR_BIN_DIRECTORY}/pod5" \
  "${PORESIPPR_BIN_DIRECTORY}/python"
pytest --version
dorado --version
git --version
jq --version

echo "Validating PoreSippR Python packages"

python - <<'PY'
from importlib.metadata import version

import pandas
import pod5
import pysam
import pytest
import requests
import yaml

packages = [
    "pandas",
    "pod5",
    "pysam",
    "pytest",
    "requests",
    "PyYAML",
]

print("PoreSippR Python imports succeeded")

for package in packages:
    print("{0}: {1}".format(package, version(package)))
PY

echo "Validating installed PoreSippR repository"

if [[ ! -f "$PORESIPPR_REPOSITORY_MANIFEST" ]]; then
  echo \
    "Missing PoreSippR repository metadata: " \
    "$PORESIPPR_REPOSITORY_MANIFEST" \
    >&2
  exit 1
fi

jq -e \
  --arg repository "$PORESIPPR_REPOSITORY_URL" \
  --arg commit "$PORESIPPR_REPOSITORY_COMMIT" \
  --arg install_path "$PORESIPPR_INSTALL_DIRECTORY" \
  --arg scheduler "$PORESIPPR_SCHEDULER" \
  --arg scheduler_test "$PORESIPPR_TEST" \
  '
    .repository == $repository
    and .commit == $commit
    and .install_path == $install_path
    and .scheduler == $scheduler
    and .scheduler_test == $scheduler_test
    and (
      (.scheduler_sha256 | type) == "string"
      and (.scheduler_sha256 | length) == 64
    )
  ' \
  "$PORESIPPR_REPOSITORY_MANIFEST" \
  >/dev/null || {
    echo "Unexpected PoreSippR repository metadata" >&2
    cat "$PORESIPPR_REPOSITORY_MANIFEST" >&2
    exit 1
  }

if [[ ! -d "$PORESIPPR_INSTALL_DIRECTORY" ]]; then
  echo \
    "PoreSippR installation directory is missing: " \
    "$PORESIPPR_INSTALL_DIRECTORY" \
    >&2
  exit 1
fi

if [[ -d "${PORESIPPR_INSTALL_DIRECTORY}/.git" ]]; then
  echo \
    "PoreSippR installation unexpectedly contains Git metadata" \
    >&2
  exit 1
fi

if [[ ! -x "$PORESIPPR_SCHEDULER" ]]; then
  echo \
    "PoreSippR scheduler is missing or not executable: " \
    "$PORESIPPR_SCHEDULER" \
    >&2
  exit 1
fi

if [[ ! -f "$PORESIPPR_TEST" ]]; then
  echo \
    "PoreSippR scheduler test file is missing: " \
    "$PORESIPPR_TEST" \
    >&2
  exit 1
fi

if grep -Eq \
    '&(amp|gt|lt);' \
    "$PORESIPPR_SCHEDULER"; then
  echo \
    "PoreSippR scheduler contains HTML-escaped source text" \
    >&2
  exit 1
fi

expected_scheduler_sha256="$(
  jq -r \
    '.scheduler_sha256 // empty' \
    "$PORESIPPR_REPOSITORY_MANIFEST"
)"

if [[ -z "$expected_scheduler_sha256" ]]; then
  echo \
    "PoreSippR repository metadata does not contain a scheduler checksum" \
    >&2
  exit 1
fi

actual_scheduler_sha256="$(
  sha256sum "$PORESIPPR_SCHEDULER" |
    awk '{print $1}'
)"

if [[ "$actual_scheduler_sha256" != "$expected_scheduler_sha256" ]]; then
  echo "PoreSippR scheduler checksum mismatch" >&2
  echo "Expected: ${expected_scheduler_sha256}" >&2
  echo "Actual:   ${actual_scheduler_sha256}" >&2
  exit 1
fi

echo "Validating PoreSippR scheduler syntax"

"$PORESIPPR_BIN_DIRECTORY/python" \
  - "$PORESIPPR_SCHEDULER" <<'PY'
import sys
from pathlib import Path

scheduler_path = Path(sys.argv[1])
scheduler_source = scheduler_path.read_text(encoding="utf-8")

compile(
    scheduler_source,
    str(scheduler_path),
    "exec",
)

print(
    "PoreSippR scheduler syntax validated: {0}".format(
        scheduler_path
    )
)
PY

echo "Validating PoreSippR scheduler command-line interface"

PYTHONDONTWRITEBYTECODE=1 \
  "$PORESIPPR_BIN_DIRECTORY/python" \
  "$PORESIPPR_SCHEDULER" \
  --help \
  >/dev/null

echo "Running incremental Dorado scheduler tests"

test_working_directory="$(
  mktemp \
    --directory \
    /var/tmp/poresippr-tests.XXXXXXXX
)"

if ! (
  cd "$test_working_directory"

  PYTHONDONTWRITEBYTECODE=1 \
    "$PORESIPPR_BIN_DIRECTORY/python" \
    -m pytest \
    --verbose \
    -p no:cacheprovider \
    "$PORESIPPR_TEST"
); then
  rm -rf \
    "$test_working_directory"

  echo \
    "Incremental Dorado scheduler tests failed" \
    >&2
  exit 1
fi

rm -rf \
  "$test_working_directory"

echo "Incremental Dorado scheduler tests passed"

echo "Validating image-level repository metadata"

jq -e \
  --arg repository "$PORESIPPR_REPOSITORY_URL" \
  --arg commit "$PORESIPPR_REPOSITORY_COMMIT" \
  --arg install_path "$PORESIPPR_INSTALL_DIRECTORY" \
  --arg scheduler "$PORESIPPR_SCHEDULER" \
  --arg scheduler_test "$PORESIPPR_TEST" \
  '
    .poresippr_repository.repository == $repository
    and .poresippr_repository.commit == $commit
    and .poresippr_repository.install_path == $install_path
    and .poresippr_repository.scheduler == $scheduler
    and .poresippr_repository.scheduler_test == $scheduler_test
  ' \
  "$IMAGE_MANIFEST" \
  >/dev/null || {
    echo "Unexpected image-level repository metadata" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

echo "PoreSippR repository metadata:"
jq . "$PORESIPPR_REPOSITORY_MANIFEST"

echo "Validating PoreSippR environment manifests"

if [[ ! -s "$PORESIPPR_PACKAGE_MANIFEST" ]]; then
  echo \
    "PoreSippR package manifest is missing or empty: " \
    "$PORESIPPR_PACKAGE_MANIFEST" \
    >&2
  exit 1
fi

if [[ ! -s "$PORESIPPR_EXPLICIT_MANIFEST" ]]; then
  echo \
    "PoreSippR explicit manifest is missing or empty: " \
    "$PORESIPPR_EXPLICIT_MANIFEST" \
    >&2
  exit 1
fi

grep -Eq \
  '(^|[[:space:]])python([[:space:]]|$)' \
  "$PORESIPPR_PACKAGE_MANIFEST" || {
    echo "Python is missing from the package manifest" >&2
    exit 1
  }

grep -Eq \
  '(^|[[:space:]])minimap2([[:space:]]|$)' \
  "$PORESIPPR_PACKAGE_MANIFEST" || {
    echo "Minimap2 is missing from the package manifest" >&2
    exit 1
  }

grep -Eq \
  '(^|[[:space:]])samtools([[:space:]]|$)' \
  "$PORESIPPR_PACKAGE_MANIFEST" || {
    echo "Samtools is missing from the package manifest" >&2
    exit 1
  }

grep -Eq \
  '(^|[[:space:]])pod5([[:space:]]|$)' \
  "$PORESIPPR_PACKAGE_MANIFEST" || {
    echo "POD5 is missing from the package manifest" >&2
    exit 1
  }

grep -Eq \
  '(^|[[:space:]])pytest([[:space:]]|$)' \
  "$PORESIPPR_PACKAGE_MANIFEST" || {
    echo "Pytest is missing from the package manifest" >&2
    exit 1
  }

echo "Validating image-level PoreSippR metadata"

jq -e \
  --arg expected_micromamba_version "$MICROMAMBA_VERSION" \
  --arg expected_micromamba_binary "$MICROMAMBA_BINARY" \
  --arg expected_root_prefix "$MAMBA_ROOT_PREFIX" \
  --arg expected_environment_name "$PORESIPPR_ENVIRONMENT_NAME" \
  --arg expected_environment_path "$PORESIPPR_ENVIRONMENT" \
  --arg expected_runtime_path "$runtime_path" \
  --arg expected_configuration "$MICROMAMBA_CONFIGURATION" \
  '
    .security.security_type == "trustedLaunch"
    and .security.secure_boot_enabled == false
    and .security.vtpm_enabled == false
    and .micromamba.version == $expected_micromamba_version
    and .micromamba.binary == $expected_micromamba_binary
    and .micromamba.root_prefix == $expected_root_prefix
    and .micromamba.configuration == $expected_configuration
    and .micromamba.ssl_verify == false
    and .poresippr.environment_name == $expected_environment_name
    and .poresippr.environment_path == $expected_environment_path
    and .poresippr.runtime_bin_path == $expected_runtime_path
    and .dorado.version == "2.1.2"
    and .dorado.default_model
        == "dna_r10.4.1_e8.2_400bps_fast@v5.2.0"
    and .nvidia_driver.version == "570.237"
    and .nvidia_driver.target_vm_family == "NVadsA10_v5"
  ' \
  "$IMAGE_MANIFEST" \
  >/dev/null || {
    echo "Unexpected image-level PoreSippR metadata" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

echo "Micromamba metadata:"
jq . "$MICROMAMBA_MANIFEST"

echo "PoreSippR runtime metadata:"
jq . "$PORESIPPR_MANIFEST"

echo "PoreSippR package manifest:"
cat "$PORESIPPR_PACKAGE_MANIFEST"

echo "PoreSippR environment specification:"
cat "$PORESIPPR_ENVIRONMENT_FILE"

echo "PoreSippR environment specification checksum:"
printf '%s  %s\n' \
  "$actual_environment_sha256" \
  "$PORESIPPR_ENVIRONMENT_FILE"

echo "Validating installed PoreSippR targets"

if [[ ! -s "$PORESIPPR_TARGETS_PATH" ]]; then
  echo \
    "PoreSippR targets file is missing or empty: " \
    "$PORESIPPR_TARGETS_PATH" \
    >&2
  exit 1
fi

if [[ ! -f "$PORESIPPR_TARGETS_MANIFEST" ]]; then
  echo \
    "PoreSippR targets metadata is missing: " \
    "$PORESIPPR_TARGETS_MANIFEST" \
    >&2
  exit 1
fi

expected_targets_sha256="$(
  jq -r '.sha256 // empty' "$PORESIPPR_TARGETS_MANIFEST"
)"

actual_targets_sha256="$(
  sha256sum "$PORESIPPR_TARGETS_PATH" |
    awk '{print $1}'
)"

if [[ "$actual_targets_sha256" != "$expected_targets_sha256" ]]; then
  echo "PoreSippR targets checksum mismatch" >&2
  echo "Expected: ${expected_targets_sha256}" >&2
  echo "Actual:   ${actual_targets_sha256}" >&2
  exit 1
fi

jq -e \
  --arg name "$PORESIPPR_TARGETS_NAME" \
  --arg path "$PORESIPPR_TARGETS_PATH" \
  --arg sha256 "$actual_targets_sha256" \
  '
    .name == $name
    and .path == $path
    and .sha256 == $sha256
    and ((.bytes | type) == "number")
    and (.bytes > 0)
    and ((.sequence_count | type) == "number")
    and (.sequence_count > 0)
  ' \
  "$PORESIPPR_TARGETS_MANIFEST" \
  >/dev/null || {
    echo "Unexpected PoreSippR targets metadata" >&2
    cat "$PORESIPPR_TARGETS_MANIFEST" >&2
    exit 1
  }

echo "Validating image-level PoreSippR targets metadata"

jq -e \
  --arg name "$PORESIPPR_TARGETS_NAME" \
  --arg path "$PORESIPPR_TARGETS_PATH" \
  --arg manifest "$PORESIPPR_TARGETS_MANIFEST" \
  '
    .poresippr_targets.name == $name
    and .poresippr_targets.path == $path
    and .poresippr_targets.manifest == $manifest
  ' \
  "$IMAGE_MANIFEST" \
  >/dev/null || {
    echo "Unexpected image-level PoreSippR targets metadata" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
  }

first_character="$(head -c 1 "$PORESIPPR_TARGETS_PATH")"
if [[ "$first_character" != ">" ]]; then
  echo "Installed PoreSippR targets are not FASTA-formatted" >&2
  exit 1
fi

expected_sequence_count="$(
  jq -r \
    '.sequence_count // 0' \
    "$PORESIPPR_TARGETS_MANIFEST"
)"

actual_sequence_count="$(
  grep -c '^>' \
    "$PORESIPPR_TARGETS_PATH" || true
)"

if [[ "$actual_sequence_count" -lt 1 ]]; then
  echo \
    "Installed PoreSippR targets contain no FASTA records" \
    >&2
  exit 1
fi

if [[ "$actual_sequence_count" -ne "$expected_sequence_count" ]]; then
  echo "PoreSippR targets sequence-count mismatch" >&2
  echo "Expected: ${expected_sequence_count}" >&2
  echo "Actual:   ${actual_sequence_count}" >&2
  exit 1
fi

echo "PoreSippR targets SHA-256: ${actual_targets_sha256}"
echo "PoreSippR target sequences: ${actual_sequence_count}"

echo "FoodPort Nanopore PoreSippR image validation completed successfully"
