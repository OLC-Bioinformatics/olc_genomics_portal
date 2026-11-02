#!/usr/bin/env bash

set -euo pipefail

DORADO_VERSION="2.1.2"
DORADO_PLATFORM="linux-x64"
DORADO_MODEL="dna_r10.4.1_e8.2_400bps_fast@v5.2.0"
MODEL_CHECKSUM_MANIFEST="/etc/foodport/${DORADO_MODEL}.sha256"

DORADO_ARCHIVE="dorado-${DORADO_VERSION}-${DORADO_PLATFORM}.tar.gz"
DORADO_URL="https://cdn.oxfordnanoportal.com/software/analysis/${DORADO_ARCHIVE}"

DORADO_ROOT="/opt/ont/dorado"
DORADO_INSTALL_DIRECTORY="${DORADO_ROOT}/${DORADO_VERSION}"
DORADO_MODELS_DIRECTORY="/opt/ont/models"

DOWNLOAD_DIRECTORY="/var/tmp/dorado"
ARCHIVE_PATH="${DOWNLOAD_DIRECTORY}/${DORADO_ARCHIVE}"

DORADO_SHA256="f4ed83acfb75cf07ffe8a0fc78e26828fc911fcfc8177920be6104e1d0e02485"

temporary_extract_directory=""
INSECURE_CURL_DIRECTORY=""

cleanup() {
  local exit_status=$?

  echo "Cleaning temporary Dorado build files"

  if [[ -n "${INSECURE_CURL_DIRECTORY:-}" ]]; then
    sudo rm -rf \
      "$INSECURE_CURL_DIRECTORY" \
      2>/dev/null || true
  fi

  if [[ -n "${temporary_extract_directory:-}" ]]; then
    sudo rm -rf \
      "$temporary_extract_directory" \
      2>/dev/null || true
  fi

  sudo rm -f \
    "$ARCHIVE_PATH" \
    2>/dev/null || true

  return "$exit_status"
}

trap cleanup EXIT

echo "Installing Dorado ${DORADO_VERSION}"

sudo install \
  -d \
  -m 0755 \
  "$DOWNLOAD_DIRECTORY" \
  "$DORADO_ROOT" \
  "$DORADO_MODELS_DIRECTORY" \
  /etc/foodport

echo "Downloading ${DORADO_ARCHIVE}"

sudo curl \
  --insecure \
  --fail \
  --location \
  --retry 5 \
  --retry-all-errors \
  --connect-timeout 30 \
  --output "$ARCHIVE_PATH" \
  "$DORADO_URL"

sudo test -s "$ARCHIVE_PATH"

echo "${DORADO_SHA256}  ${ARCHIVE_PATH}" |
  sudo sha256sum --check -

temporary_extract_directory="$(
  sudo mktemp \
    --directory \
    "${DOWNLOAD_DIRECTORY}/extract.XXXXXXXX"
)"

sudo tar \
  -xzf "$ARCHIVE_PATH" \
  -C "$temporary_extract_directory"

echo "Locating the extracted Dorado executable"

extracted_dorado_binary="$(
  sudo find \
    "$temporary_extract_directory" \
    -type f \
    -path "*/bin/dorado" \
    -print \
    -quit
)"

if [[ -z "$extracted_dorado_binary" ]]; then
  echo "Dorado executable was not found after extraction" >&2

  echo "Extracted archive contents:" >&2
  sudo find \
    "$temporary_extract_directory" \
    -mindepth 1 \
    -maxdepth 4 \
    -printf '%y %m %p\n' \
    >&2

  exit 1
fi

extracted_bin_directory="$(
  dirname "$extracted_dorado_binary"
)"

extracted_directory="$(
  dirname "$extracted_bin_directory"
)"

echo "Extracted Dorado binary: $extracted_dorado_binary"
echo "Extracted Dorado root: $extracted_directory"

if ! sudo test -f "${extracted_directory}/bin/dorado"; then
  echo \
    "Derived Dorado installation root does not contain bin/dorado: " \
    "$extracted_directory" \
    >&2
  exit 1
fi

sudo chmod 0755 \
  "${extracted_directory}/bin/dorado"

echo "Installing Dorado into ${DORADO_INSTALL_DIRECTORY}"

sudo rm -rf "$DORADO_INSTALL_DIRECTORY"

sudo mv \
  "$extracted_directory" \
  "$DORADO_INSTALL_DIRECTORY"

if ! sudo test -x "${DORADO_INSTALL_DIRECTORY}/bin/dorado"; then
  echo \
    "Installed Dorado executable is missing or not executable: " \
    "${DORADO_INSTALL_DIRECTORY}/bin/dorado" \
    >&2
  exit 1
fi

if ! sudo test -d "${DORADO_INSTALL_DIRECTORY}/lib"; then
  echo \
    "Installed Dorado library directory is missing: " \
    "${DORADO_INSTALL_DIRECTORY}/lib" \
    >&2
  exit 1
fi

sudo ln -sfn \
  "${DORADO_INSTALL_DIRECTORY}/bin/dorado" \
  /usr/local/bin/dorado

sudo ln -sfn \
  "${DORADO_INSTALL_DIRECTORY}/bin" \
  "${DORADO_ROOT}/bin"

resolved_dorado="$(
  readlink \
    --canonicalize \
    /usr/local/bin/dorado
)"

expected_dorado="${DORADO_INSTALL_DIRECTORY}/bin/dorado"

if [[ "$resolved_dorado" != "$expected_dorado" ]]; then
  echo "Unexpected Dorado symlink target: $resolved_dorado" >&2
  exit 1
fi

echo "Dorado command: /usr/local/bin/dorado"
echo "Dorado target: $resolved_dorado"

echo "Checking Dorado version"

dorado_version_output="$(
  /usr/local/bin/dorado --version 2>&1
)"

echo "$dorado_version_output"

if [[ "$dorado_version_output" != *"${DORADO_VERSION}"* ]]; then
  echo "Dorado did not report the expected version" >&2
  exit 1
fi

echo "Downloading Dorado model ${DORADO_MODEL}"

INSECURE_CURL_DIRECTORY="${DOWNLOAD_DIRECTORY}/insecure-curl"
INSECURE_CURL_WRAPPER="${INSECURE_CURL_DIRECTORY}/curl"

sudo install \
  -d \
  -m 0755 \
  "$INSECURE_CURL_DIRECTORY"

sudo tee "$INSECURE_CURL_WRAPPER" >/dev/null <<'EOF'
#!/usr/bin/env bash

set -euo pipefail

exec /usr/bin/curl \
  --insecure \
  "$@"
EOF

sudo chmod 0755 \
  "$INSECURE_CURL_WRAPPER"

if ! sudo env \
    PATH="${INSECURE_CURL_DIRECTORY}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    /usr/local/bin/dorado download \
      --model "$DORADO_MODEL" \
      --models-directory "$DORADO_MODELS_DIRECTORY"; then
  echo "Dorado model download failed: ${DORADO_MODEL}" >&2
  exit 1
fi

sudo rm -rf "$INSECURE_CURL_DIRECTORY"
INSECURE_CURL_DIRECTORY=""

MODEL_DIRECTORY="${DORADO_MODELS_DIRECTORY}/${DORADO_MODEL}"

if ! sudo test -d "$MODEL_DIRECTORY"; then
  echo "Dorado model directory was not created: ${MODEL_DIRECTORY}" >&2
  exit 1
fi

model_file="$(
  sudo find \
    "$MODEL_DIRECTORY" \
    -type f \
    -print \
    -quit
)"

if [[ -z "$model_file" ]]; then
  echo "Dorado model directory contains no files: ${MODEL_DIRECTORY}" >&2
  exit 1
fi

echo "Dorado model files were downloaded successfully"

sudo chown \
  -R \
  root:root \
  "$MODEL_DIRECTORY"

sudo find \
  "$MODEL_DIRECTORY" \
  -type d \
  -exec chmod 0755 {} +

sudo find \
  "$MODEL_DIRECTORY" \
  -type f \
  -exec chmod 0644 {} +

echo "Generating model checksum manifest"

sudo find \
  "$MODEL_DIRECTORY" \
  -type f \
  -print0 |
LC_ALL=C sort -z |
xargs \
  --null \
  --no-run-if-empty \
  sha256sum |
sudo tee "$MODEL_CHECKSUM_MANIFEST" >/dev/null

if ! sudo test -s "$MODEL_CHECKSUM_MANIFEST"; then
  echo \
    "Dorado model checksum manifest is missing or empty: " \
    "$MODEL_CHECKSUM_MANIFEST" \
    >&2
  exit 1
fi

echo "Dorado model checksum manifest created: $MODEL_CHECKSUM_MANIFEST"

echo "Recording Dorado metadata"

sudo tee /etc/foodport/dorado.json >/dev/null <<EOF
{
  "version": "${DORADO_VERSION}",
  "platform": "${DORADO_PLATFORM}",
  "archive": "${DORADO_ARCHIVE}",
  "archive_sha256": "${DORADO_SHA256}",
  "source": "${DORADO_URL}",
  "install_path": "${DORADO_INSTALL_DIRECTORY}",
  "models_path": "${DORADO_MODELS_DIRECTORY}",
  "default_model_alias": "fast",
  "default_model": "${DORADO_MODEL}"
}
EOF

sudo chmod 0644 \
  /etc/foodport/dorado.json \
  "$MODEL_CHECKSUM_MANIFEST"

echo "Dorado ${DORADO_VERSION} installation completed"

cleanup
trap - EXIT
