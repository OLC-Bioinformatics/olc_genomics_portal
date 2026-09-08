#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(
  cd "$(dirname "${BASH_SOURCE[0]}")" &&
  pwd
)"

STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-carlingst01}"
CONTAINER_NAME="${CONTAINER_NAME:-poresippr-data}"
SAS_HOURS="${SAS_HOURS:-4}"
SAS_AUTH_MODE="${SAS_AUTH_MODE:-key}"
STORAGE_RESOURCE_GROUP="${STORAGE_RESOURCE_GROUP:-CFDC-FoodPort-Batch-rg}"

IMAGE_VERSION="${IMAGE_VERSION:-0.0.5}"
DORADO_MODEL="${DORADO_MODEL:-dna_r10.4.1_e8.2_400bps_fast@v5.2.0}"
BARCODE_KIT="${BARCODE_KIT:-SQK-RBK114-24}"
BARCODE_VALUES="${BARCODE_VALUES:-12,16,20,22}"
EXPECTED_MAPPING_BARCODE="${EXPECTED_MAPPING_BARCODE:-22}"
EXPECTED_MAPPING_TARGET="${EXPECTED_MAPPING_TARGET:-gntK}"
EXPECTED_MAPPING_READS="${EXPECTED_MAPPING_READS:-1}"
ACCEPTANCE_POD5_PATH="${ACCEPTANCE_POD5_PATH:-MIN-20260804/no_sample/20260804_1121_MN49535_FBF37799_4af93e9c/pod5/FBF37799_4af93e9c_4ce0ae7c_0.pod5}"

PORESIPPR_ENVIRONMENT="${PORESIPPR_ENVIRONMENT:-/opt/micromamba/root/envs/poresippr}"
PORESIPPR_SCHEDULER="${PORESIPPR_SCHEDULER:-/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py}"
PORESIPPR_TARGETS="${PORESIPPR_TARGETS:-/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta}"
PORESIPPR_REPOSITORY_COMMIT="${PORESIPPR_REPOSITORY_COMMIT:-2108b9428c51f2335ed4cd3f0e1c417db3ba0563}"
PORESIPPR_TARGETS_SHA256="${PORESIPPR_TARGETS_SHA256:-6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9}"
PORESIPPR_TARGETS_SEQUENCE_COUNT="${PORESIPPR_TARGETS_SEQUENCE_COUNT:-6663}"

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-dcdc7934-5cce-43de-a6ed-22e2e163c2e1}"
LOCATION="${LOCATION:-canadacentral}"
RESOURCE_GROUP="${RESOURCE_GROUP:-CFDC-FoodPort-Batch-rg}"
GPU_VM_SIZE="${GPU_VM_SIZE:-Standard_NV18ads_A10_v5}"
GPU_ADMIN_USER="${GPU_ADMIN_USER:-cfiaadmin}"

SUBNET_RESOURCE_ID="${SUBNET_RESOURCE_ID:-/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/CFDC-FoodPort-network-rg/providers/Microsoft.Network/virtualNetworks/CFDC-FoodPort-vnet/subnets/CFDC-FoodPort-BatchNodes-snet}"
IMAGE_RESOURCE_ID="${IMAGE_RESOURCE_ID:-/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/CFDC-FoodPort-Batch-rg/providers/Microsoft.Compute/galleries/development/images/nanopore/versions/${IMAGE_VERSION}}"

SSH_PRIVATE_KEY="${SSH_PRIVATE_KEY:-${HOME}/.ssh/portal_ssh_key}"
SSH_PUBLIC_KEY="${SSH_PUBLIC_KEY:-${HOME}/.ssh/portal_ssh_key.pub}"
KEEP_RESOURCES_ON_FAILURE="${KEEP_RESOURCES_ON_FAILURE:-false}"
DIAGNOSTIC_SCHEDULER_OVERRIDE="${DIAGNOSTIC_SCHEDULER_OVERRIDE:-false}"

REMOTE_ROOT="${REMOTE_ROOT:-/mnt/resource/nanopore-acceptance}"
REMOTE_INPUT_DIRECTORY="${REMOTE_ROOT}/input"
REMOTE_OUTPUT_DIRECTORY="${REMOTE_ROOT}/output"
REMOTE_SCRIPT_DIRECTORY="${REMOTE_SCRIPT_DIRECTORY:-/tmp/nanopore-acceptance}"
LOCAL_RESULT_DIRECTORY="${LOCAL_RESULT_DIRECTORY:-${SCRIPT_DIRECTORY}/../docs/acceptance-results/${IMAGE_VERSION}}"

DOWNLOAD_SCRIPT="${SCRIPT_DIRECTORY}/download-acceptance-input.sh"
ACCEPTANCE_SCRIPT="${SCRIPT_DIRECTORY}/run-gpu-acceptance.sh"
SAS_SCRIPT="${SCRIPT_DIRECTORY}/create-input-sas.sh"

run_suffix="$(date --utc '+%Y%m%d%H%M%S')"
safe_version="${IMAGE_VERSION//./-}"
RESOURCE_PREFIX="${RESOURCE_PREFIX:-nanopore-accept-${safe_version}-${run_suffix}}"
GPU_VM_NAME="${GPU_VM_NAME:-${RESOURCE_PREFIX}-vm}"
NIC_NAME="${NIC_NAME:-${RESOURCE_PREFIX}-nic}"
OS_DISK_NAME="${OS_DISK_NAME:-${RESOURCE_PREFIX}-osdisk}"
SSH_KNOWN_HOSTS_FILE="$(mktemp "/tmp/${RESOURCE_PREFIX}-known-hosts.XXXXXXXX")"

NIC_CREATION_STARTED=false
VM_CREATION_STARTED=false
ACCEPTANCE_SUCCEEDED=false
CONTAINER_SAS_URL=""
CONTAINER_SAS_URL_BASE64=""
GPU_HOST=""
GPU_SSH_TARGET=""

SSH_OPTIONS=(
  -i "$SSH_PRIVATE_KEY"
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$SSH_KNOWN_HOSTS_FILE"
)

validate_input_sas() {
  local container_sas_url="$1"
  local sas_token
  local content_length

  sas_token="${container_sas_url#*\?}"

  if [[ -z "$sas_token" ]] || [[ "$sas_token" == "$container_sas_url" ]]; then
    echo "Could not extract a SAS token from the generated URL" >&2
    return 1
  fi

  content_length="$(
    az storage blob show \
      --account-name "$STORAGE_ACCOUNT" \
      --container-name "$CONTAINER_NAME" \
      --name "$ACCEPTANCE_POD5_PATH" \
      --sas-token "$sas_token" \
      --query 'properties.contentLength' \
      --output tsv
  )"

  if [[ -z "$content_length" ]]; then
    echo "The generated SAS could not retrieve the acceptance POD5 metadata" >&2
    return 1
  fi

  echo "Generated SAS successfully authorized the acceptance POD5"
  echo "Acceptance POD5 bytes: ${content_length}"
}

cleanup() {
  local exit_status=$?
  local remaining_resources=""

  trap - EXIT INT TERM
  unset CONTAINER_SAS_URL || true
  unset CONTAINER_SAS_URL_BASE64 || true

  if [[ -n "${SSH_KNOWN_HOSTS_FILE:-}" ]]; then
    rm -f "$SSH_KNOWN_HOSTS_FILE" || true
  fi

  echo

  if [[ "$ACCEPTANCE_SUCCEEDED" == true ]]; then
    echo "Acceptance completed successfully."
  else
    echo "Acceptance failed with status ${exit_status}." >&2
  fi

  if [[ "$KEEP_RESOURCES_ON_FAILURE" == true ]] &&
     [[ "$ACCEPTANCE_SUCCEEDED" != true ]]; then
    echo "Preserving temporary resources for investigation:"
    echo "  Resource group: ${RESOURCE_GROUP}"
    echo "  VM:             ${GPU_VM_NAME}"
    echo "  NIC:            ${NIC_NAME}"
    echo "  OS disk:        ${OS_DISK_NAME}"
    return "$exit_status"
  fi

  if [[ "$VM_CREATION_STARTED" == true ]]; then
    if az vm show --resource-group "$RESOURCE_GROUP" --name "$GPU_VM_NAME" >/dev/null 2>&1; then
      echo "Deleting temporary GPU VM: ${GPU_VM_NAME}"
      az vm delete --resource-group "$RESOURCE_GROUP" --name "$GPU_VM_NAME" --yes >/dev/null 2>&1 || true
      echo "Waiting for Azure to detach VM resources"
      sleep 15
    fi

    if az disk show --resource-group "$RESOURCE_GROUP" --name "$OS_DISK_NAME" >/dev/null 2>&1; then
      echo "Deleting remaining OS disk: ${OS_DISK_NAME}"
      az disk delete --resource-group "$RESOURCE_GROUP" --name "$OS_DISK_NAME" --yes >/dev/null 2>&1 || true
    fi
  fi

  if [[ "$NIC_CREATION_STARTED" == true ]]; then
    if az network nic show --resource-group "$RESOURCE_GROUP" --name "$NIC_NAME" >/dev/null 2>&1; then
      echo "Deleting remaining NIC: ${NIC_NAME}"
      az network nic delete --resource-group "$RESOURCE_GROUP" --name "$NIC_NAME" >/dev/null 2>&1 || true
    fi
  fi

  for cleanup_check in $(seq 1 6); do
    remaining_resources="$(
      az resource list \
        --resource-group "$RESOURCE_GROUP" \
        --query "[?starts_with(name, '${RESOURCE_PREFIX}')].{name:name,type:type}" \
        --output tsv \
        2>/dev/null || true
    )"

    if [[ -z "$remaining_resources" ]]; then
      break
    fi

    if [[ "$cleanup_check" -lt 6 ]]; then
      echo "Temporary resources are still being removed, check ${cleanup_check}/6"
      sleep 10
    fi
  done

  if [[ -n "$remaining_resources" ]]; then
    echo "WARNING: Temporary acceptance resources remain:" >&2
    printf '%s\n' "$remaining_resources" >&2
    if [[ "$exit_status" -eq 0 ]]; then
      exit_status=1
    fi
  else
    echo "No temporary acceptance resources remain."
  fi

  return "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for command_name in az base64 jq scp seq ssh; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is not installed: ${command_name}" >&2
    exit 1
  fi
done

for script_path in "$DOWNLOAD_SCRIPT" "$ACCEPTANCE_SCRIPT" "$SAS_SCRIPT"; do
  if [[ ! -f "$script_path" ]]; then
    echo "Required script is missing: ${script_path}" >&2
    exit 1
  fi
done

for key_path in "$SSH_PRIVATE_KEY" "$SSH_PUBLIC_KEY"; do
  if [[ ! -f "$key_path" ]]; then
    echo "SSH key is missing: ${key_path}" >&2
    exit 1
  fi
done

if [[ -e "$LOCAL_RESULT_DIRECTORY" ]]; then
  echo "Acceptance result directory already exists: ${LOCAL_RESULT_DIRECTORY}" >&2
  echo "Move or remove the previous candidate results before continuing." >&2
  exit 1
fi

echo
echo "Nanopore GPU scheduler acceptance configuration:"
echo "  Subscription:       ${SUBSCRIPTION_ID}"
echo "  Resource group:     ${RESOURCE_GROUP}"
echo "  Location:           ${LOCATION}"
echo "  Image version:      ${IMAGE_VERSION}"
echo "  Image resource ID:  ${IMAGE_RESOURCE_ID}"
echo "  VM size:            ${GPU_VM_SIZE}"
echo "  Storage account:    ${STORAGE_ACCOUNT}"
echo "  Container:          ${CONTAINER_NAME}"
echo "  POD5 blob:          ${ACCEPTANCE_POD5_PATH}"
echo "  Dorado model:       ${DORADO_MODEL}"
echo "  Barcode kit:        ${BARCODE_KIT}"
echo "  Barcode values:     ${BARCODE_VALUES}"
echo "  Expected mapping:   barcode${EXPECTED_MAPPING_BARCODE} ${EXPECTED_MAPPING_TARGET}=${EXPECTED_MAPPING_READS}"
echo "  PoreSippR env:      ${PORESIPPR_ENVIRONMENT}"
echo "  Scheduler:          ${PORESIPPR_SCHEDULER}"
echo "  Targets:            ${PORESIPPR_TARGETS}"
echo "  Repository commit:  ${PORESIPPR_REPOSITORY_COMMIT}"
echo "  Targets SHA-256:    ${PORESIPPR_TARGETS_SHA256}"
echo "  Target records:     ${PORESIPPR_TARGETS_SEQUENCE_COUNT}"
echo "  Resource prefix:    ${RESOURCE_PREFIX}"
echo "  Keep on failure:    ${KEEP_RESOURCES_ON_FAILURE}"
echo "  Diagnostic override: ${DIAGNOSTIC_SCHEDULER_OVERRIDE}"
echo "  SAS auth mode:      ${SAS_AUTH_MODE}"
echo

echo "Validating local acceptance scripts"
bash -n "${BASH_SOURCE[0]}" "$DOWNLOAD_SCRIPT" "$ACCEPTANCE_SCRIPT" "$SAS_SCRIPT"

az account set --subscription "$SUBSCRIPTION_ID"

for resource_name in "$GPU_VM_NAME" "$NIC_NAME" "$OS_DISK_NAME"; do
  resource_count="$(
    az resource list \
      --resource-group "$RESOURCE_GROUP" \
      --query "[?name == '${resource_name}'] | length(@)" \
      --output tsv
  )"
  if [[ "$resource_count" != "0" ]]; then
    echo "A resource with the generated name already exists: ${resource_name}" >&2
    exit 1
  fi
done

echo "Generating a short-lived read/list SAS"
CONTAINER_SAS_URL="$(
  STORAGE_ACCOUNT="$STORAGE_ACCOUNT" \
  CONTAINER_NAME="$CONTAINER_NAME" \
  SAS_HOURS="$SAS_HOURS" \
  SAS_AUTH_MODE="$SAS_AUTH_MODE" \
  STORAGE_RESOURCE_GROUP="$STORAGE_RESOURCE_GROUP" \
  "$SAS_SCRIPT"
)"

if [[ -z "$CONTAINER_SAS_URL" ]]; then
  echo "Generated container SAS URL is empty" >&2
  exit 1
fi

validate_input_sas "$CONTAINER_SAS_URL"

CONTAINER_SAS_URL_BASE64="$(printf '%s' "$CONTAINER_SAS_URL" | base64 --wrap=0)"
if [[ -z "$CONTAINER_SAS_URL_BASE64" ]]; then
  echo "Base64-encoded container SAS URL is empty" >&2
  exit 1
fi

echo "Creating temporary NIC: ${NIC_NAME}"
NIC_CREATION_STARTED=true
az network nic create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$NIC_NAME" \
  --location "$LOCATION" \
  --subnet "$SUBNET_RESOURCE_ID" \
  --accelerated-networking false \
  --tags purpose=nanopore-image-acceptance image-version="$IMAGE_VERSION" temporary=true acceptance-prefix="$RESOURCE_PREFIX" \
  >/dev/null

echo "Creating ephemeral GPU VM: ${GPU_VM_NAME}"
VM_CREATION_STARTED=true
az vm create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$GPU_VM_NAME" \
  --location "$LOCATION" \
  --nics "$NIC_NAME" \
  --image "$IMAGE_RESOURCE_ID" \
  --size "$GPU_VM_SIZE" \
  --admin-username "$GPU_ADMIN_USER" \
  --ssh-key-values "$SSH_PUBLIC_KEY" \
  --security-type TrustedLaunch \
  --enable-secure-boot false \
  --enable-vtpm false \
  --os-disk-name "$OS_DISK_NAME" \
  --os-disk-delete-option Delete \
  --nic-delete-option Delete \
  --tags purpose=nanopore-image-acceptance image-version="$IMAGE_VERSION" temporary=true acceptance-prefix="$RESOURCE_PREFIX" \
  >/dev/null

GPU_HOST="$(az network nic show --resource-group "$RESOURCE_GROUP" --name "$NIC_NAME" --query 'ipConfigurations[0].privateIPAddress' --output tsv)"
if [[ -z "$GPU_HOST" ]]; then
  echo "Could not determine the GPU VM private IP" >&2
  exit 1
fi
GPU_SSH_TARGET="${GPU_ADMIN_USER}@${GPU_HOST}"
echo "GPU VM private IP: ${GPU_HOST}"

echo "Waiting for SSH"
ssh_ready=false
for attempt in $(seq 1 40); do
  if ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" 'printf "SSH ready on %s\n" "$(hostname)"' 2>/dev/null; then
    ssh_ready=true
    break
  fi
  echo "SSH not ready, attempt ${attempt}/40"
  sleep 15
done
if [[ "$ssh_ready" != true ]]; then
  echo "The GPU VM did not become reachable over SSH" >&2
  exit 1
fi

echo "Verifying GPU availability"
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" \
  'nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader'

echo "Installing AzCopy on the acceptance VM"
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
if command -v azcopy >/dev/null 2>&1; then
  azcopy --version
  exit 0
fi
work_directory="/tmp/azcopy-install"
rm -rf "$work_directory"
mkdir -p "$work_directory"
curl --insecure --fail --location --retry 5 --retry-all-errors \
  --output "${work_directory}/azcopy.tar.gz" \
  https://aka.ms/downloadazcopy-v10-linux
tar -xzf "${work_directory}/azcopy.tar.gz" -C "$work_directory"
azcopy_binary="$(find "$work_directory" -type f -name azcopy -print -quit)"
if [[ -z "$azcopy_binary" ]]; then
  echo "AzCopy executable was not found" >&2
  exit 1
fi
sudo install -m 0755 "$azcopy_binary" /usr/local/bin/azcopy
rm -rf "$work_directory"
azcopy --version
REMOTE

echo "Copying acceptance scripts to GPU VM"
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" "mkdir -p '${REMOTE_SCRIPT_DIRECTORY}'"
scp "${SSH_OPTIONS[@]}" "$DOWNLOAD_SCRIPT" "$ACCEPTANCE_SCRIPT" \
  "${GPU_SSH_TARGET}:${REMOTE_SCRIPT_DIRECTORY}/"

echo "Preparing acceptance directories"
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" bash -s -- \
  "$REMOTE_ROOT" "$REMOTE_INPUT_DIRECTORY" "$REMOTE_OUTPUT_DIRECTORY" "$REMOTE_SCRIPT_DIRECTORY" <<'REMOTE'
set -euo pipefail
remote_root="$1"
input_directory="$2"
output_directory="$3"
script_directory="$4"
sudo mkdir -p "$remote_root"
sudo chown -R "$(id -u):$(id -g)" "$remote_root"
rm -rf "$input_directory" "$output_directory"
mkdir -p "$input_directory" "$output_directory"
chmod 0755 "${script_directory}/download-acceptance-input.sh" "${script_directory}/run-gpu-acceptance.sh"
REMOTE

echo "Downloading acceptance input on GPU VM"
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" env \
  "CONTAINER_SAS_URL_BASE64=${CONTAINER_SAS_URL_BASE64}" \
  "ACCEPTANCE_POD5_PATH=${ACCEPTANCE_POD5_PATH}" \
  "INPUT_DIRECTORY=${REMOTE_INPUT_DIRECTORY}" \
  "${REMOTE_SCRIPT_DIRECTORY}/download-acceptance-input.sh"

unset CONTAINER_SAS_URL
unset CONTAINER_SAS_URL_BASE64

echo "Running standalone PoreSippR scheduler acceptance test"
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" env \
  "IMAGE_VERSION=${IMAGE_VERSION}" \
  "DORADO_MODEL=${DORADO_MODEL}" \
  "BARCODE_KIT=${BARCODE_KIT}" \
  "BARCODE_VALUES=${BARCODE_VALUES}" \
  "EXPECTED_MAPPING_BARCODE=${EXPECTED_MAPPING_BARCODE}" \
  "EXPECTED_MAPPING_TARGET=${EXPECTED_MAPPING_TARGET}" \
  "EXPECTED_MAPPING_READS=${EXPECTED_MAPPING_READS}" \
  "INPUT_DIRECTORY=${REMOTE_INPUT_DIRECTORY}" \
  "OUTPUT_DIRECTORY=${REMOTE_OUTPUT_DIRECTORY}" \
  "PORESIPPR_ENVIRONMENT=${PORESIPPR_ENVIRONMENT}" \
  "PORESIPPR_SCHEDULER=${PORESIPPR_SCHEDULER}" \
  "PORESIPPR_TARGETS=${PORESIPPR_TARGETS}" \
  "PORESIPPR_REPOSITORY_COMMIT=${PORESIPPR_REPOSITORY_COMMIT}" \
  "PORESIPPR_TARGETS_SHA256=${PORESIPPR_TARGETS_SHA256}" \
  "PORESIPPR_TARGETS_SEQUENCE_COUNT=${PORESIPPR_TARGETS_SEQUENCE_COUNT}" \
  "DIAGNOSTIC_SCHEDULER_OVERRIDE=${DIAGNOSTIC_SCHEDULER_OVERRIDE}" \
  "${REMOTE_SCRIPT_DIRECTORY}/run-gpu-acceptance.sh"

echo "Copying acceptance results to portal VM"
mkdir -p "$LOCAL_RESULT_DIRECTORY"
for result_name in \
  acceptance-result.json \
  acceptance-result.md \
  summary.tsv \
  barcode-counts.tsv \
  scheduler-state.json \
  scheduler-status.json \
  scheduler-run.csv \
  scheduler-metadata.csv \
  scheduler-evidence.tar.gz; do
  scp "${SSH_OPTIONS[@]}" \
    "${GPU_SSH_TARGET}:${REMOTE_OUTPUT_DIRECTORY}/${result_name}" \
    "$LOCAL_RESULT_DIRECTORY/"
done

LOCAL_RESULT_JSON="${LOCAL_RESULT_DIRECTORY}/acceptance-result.json"
for result_file in "$LOCAL_RESULT_DIRECTORY"/*; do
  if [[ ! -s "$result_file" ]]; then
    echo "Copied acceptance report is missing or empty: ${result_file}" >&2
    exit 1
  fi
done

jq -e \
  --arg expected_image_version "$IMAGE_VERSION" \
  --arg expected_model "$DORADO_MODEL" \
  --arg expected_barcode_kit "$BARCODE_KIT" \
  --arg expected_mapping_target "$EXPECTED_MAPPING_TARGET" \
  --argjson expected_mapping_barcode "$EXPECTED_MAPPING_BARCODE" \
  --argjson expected_mapping_reads "$EXPECTED_MAPPING_READS" \
  --arg expected_commit "$PORESIPPR_REPOSITORY_COMMIT" \
  --arg expected_targets_sha256 "$PORESIPPR_TARGETS_SHA256" \
  --argjson expected_targets_count "$PORESIPPR_TARGETS_SEQUENCE_COUNT" \
  --argjson diagnostic_scheduler_override "$DIAGNOSTIC_SCHEDULER_OVERRIDE" \
  '
    .schema_version == 2
    and .diagnostic_scheduler_override
        == $diagnostic_scheduler_override
    and .accepted == ($diagnostic_scheduler_override | not)
    and .image_version == $expected_image_version
    and .dorado.model == $expected_model
    and .dorado.barcode_kit == $expected_barcode_kit
    and .gpu.name != ""
    and .gpu.driver_version != ""
    and .gpu.memory_mib > 0
    and .input.pod5_count == 1
    and .input.pod5_bytes > 0
    and .poresippr.repository_commit == $expected_commit
    and .poresippr.targets_sha256 == $expected_targets_sha256
    and .poresippr.targets_sequence_count == $expected_targets_count
    and .scheduler.first_exit_status == 0
    and .scheduler.second_exit_status == 0
    and .scheduler.status == "completed"
    and .scheduler.reason == "once-complete"
    and .scheduler.processed_pod5_count == .input.pod5_count
    and .scheduler.batch_count > 0
    and .scheduler.duplicate_processing_prevented == true
    and .scheduler.retained_fastq_count > 0
    and .scheduler.result_csv_count > 0
    and .scheduler.mapping_bam_count > 0
    and .scheduler.mapping_bai_count > 0
    and .test.read_count > 0
    and .test.bam_bytes > 0
    and .test.demux_file_count > 0
    and .test.elapsed_seconds > 0
    and .test.classified_reads >= 0
    and .test.unknown_reads >= 0
    and (
      .test.classified_reads + .test.unknown_reads
      == .test.read_count
    )
    and (
      ([.test.barcode_counts[]] | add)
      == .test.read_count
    )
    and .mapping.result_row_count > 0
    and .mapping.total_mapped_reads > 0
    and .mapping.expected.barcode == $expected_mapping_barcode
    and .mapping.expected.target == $expected_mapping_target
    and .mapping.expected.reads == $expected_mapping_reads
    and .mapping.observed_expected_target_reads == $expected_mapping_reads
    and .mapping.targets[$expected_mapping_target] == $expected_mapping_reads
  ' "$LOCAL_RESULT_JSON" >/dev/null

echo
echo "Acceptance result summary:"
jq -r '
  "  Accepted:             \(.accepted)",
  "  Image version:        \(.image_version)",
  "  GPU:                  \(.gpu.name)",
  "  NVIDIA driver:        \(.gpu.driver_version)",
  "  Dorado:               \(.dorado.version)",
  "  POD5 files:           \(.input.pod5_count)",
  "  Reads summarized:     \(.test.read_count)",
  "  Scheduler batches:    \(.scheduler.batch_count)",
  "  Processed POD5:       \(.scheduler.processed_pod5_count)",
  "  Duplicate prevented:  \(.scheduler.duplicate_processing_prevented)",
  "  Retained FASTQ:       \(.scheduler.retained_fastq_count)",
  "  Mapping result CSVs:  \(.scheduler.result_csv_count)",
  "  Total mapped reads:   \(.mapping.total_mapped_reads)",
  "  Expected mapping:     barcode\(.mapping.expected.barcode) \(.mapping.expected.target)=\(.mapping.expected.reads)",
  "  Observed target reads: \(.mapping.observed_expected_target_reads)",
  "  Target records:       \(.poresippr.targets_sequence_count)"
' "$LOCAL_RESULT_JSON"

ACCEPTANCE_SUCCEEDED=true

echo
echo "GPU scheduler acceptance reports copied and validated successfully"
echo "Results: ${LOCAL_RESULT_DIRECTORY}"
echo "Temporary Azure resources will now be removed"
