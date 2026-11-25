#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STORAGE_ACCOUNT="${STORAGE_ACCOUNT:-carlingst01}"
CONTAINER_NAME="${CONTAINER_NAME:-poresippr-dataset2}"
SAS_HOURS="${SAS_HOURS:-4}"
SAS_AUTH_MODE="${SAS_AUTH_MODE:-key}"
STORAGE_RESOURCE_GROUP="${STORAGE_RESOURCE_GROUP:-CFDC-FoodPort-Batch-rg}"
IMAGE_VERSION="${IMAGE_VERSION:-0.0.17}"
EXPECTED_WRAPPER_VERSION="${EXPECTED_WRAPPER_VERSION:-${IMAGE_VERSION}}"
DORADO_MODEL="${DORADO_MODEL:-dna_r10.4.1_e8.2_400bps_fast@v5.2.0}"
BARCODE_KIT="${BARCODE_KIT:-SQK-RBK114-24}"
BARCODE_VALUES="${BARCODE_VALUES:-1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24}"
BATCH_FILE_COUNTS="${BATCH_FILE_COUNTS:-3,7,2,5,6}"
ACCEPTANCE_POD5_DIRECTORY="${ACCEPTANCE_POD5_DIRECTORY:-20260421_MIN}"
ACCEPTANCE_POD5_FILES="${ACCEPTANCE_POD5_FILES:-FBF69780_2a40bb31_a027dece_0.pod5;FBF69780_2a40bb31_a027dece_1.pod5;FBF69780_2a40bb31_a027dece_2.pod5;FBF69780_2a40bb31_a027dece_3.pod5;FBF69780_2a40bb31_a027dece_4.pod5;FBF69780_2a40bb31_a027dece_5.pod5;FBF69780_2a40bb31_a027dece_6.pod5;FBF69780_2a40bb31_a027dece_7.pod5;FBF69780_2a40bb31_a027dece_8.pod5;FBF69780_2a40bb31_a027dece_9.pod5;FBF69780_2a40bb31_a027dece_10.pod5;FBF69780_2a40bb31_a027dece_11.pod5;FBF69780_2a40bb31_a027dece_12.pod5;FBF69780_2a40bb31_a027dece_13.pod5;FBF69780_2a40bb31_a027dece_14.pod5;FBF69780_2a40bb31_a027dece_15.pod5;FBF69780_2a40bb31_a027dece_16.pod5;FBF69780_2a40bb31_a027dece_17.pod5;FBF69780_2a40bb31_a027dece_18.pod5;FBF69780_2a40bb31_a027dece_19.pod5;FBF69780_2a40bb31_a027dece_20.pod5;FBF69780_2a40bb31_a027dece_21.pod5;FBF69780_2a40bb31_a027dece_22.pod5}"
ACCEPTANCE_VALIDATION_POD5_PATH="${ACCEPTANCE_VALIDATION_POD5_PATH:-${ACCEPTANCE_POD5_DIRECTORY}/FBF69780_2a40bb31_a027dece_0.pod5}"
EXPECTED_POD5_COUNT="${EXPECTED_POD5_COUNT:-23}"
EXPECTED_BATCH_COUNT="${EXPECTED_BATCH_COUNT:-5}"
PORESIPPR_ENVIRONMENT="${PORESIPPR_ENVIRONMENT:-/opt/micromamba/root/envs/poresippr}"
PORESIPPR_SCHEDULER="${PORESIPPR_SCHEDULER:-/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py}"
PORESIPPR_SCHEDULER_SOURCE="${PORESIPPR_SCHEDULER_SOURCE:-}"
PORESIPPR_TARGETS="${PORESIPPR_TARGETS:-/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta}"
PORESIPPR_REPOSITORY_COMMIT="${PORESIPPR_REPOSITORY_COMMIT:-045baf1ba8372813e05bd981dd57886739e052df}"
PORESIPPR_TARGETS_SHA256="${PORESIPPR_TARGETS_SHA256:-6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9}"
PORESIPPR_TARGETS_SEQUENCE_COUNT="${PORESIPPR_TARGETS_SEQUENCE_COUNT:-6663}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-dcdc7934-5cce-43de-a6ed-22e2e163c2e1}"
LOCATION="${LOCATION:-canadacentral}"
RESOURCE_GROUP="${RESOURCE_GROUP:-CFDC-FoodPort-Batch-rg}"
GPU_VM_SIZE="${GPU_VM_SIZE:-Standard_NV18ads_A10_v5}"
GPU_ADMIN_USER="${GPU_ADMIN_USER:-cfiaadmin}"
VM_ALLOCATION_ATTEMPTS="${VM_ALLOCATION_ATTEMPTS:-10}"
VM_ALLOCATION_RETRY_SECONDS="${VM_ALLOCATION_RETRY_SECONDS:-180}"
SUBNET_RESOURCE_ID="${SUBNET_RESOURCE_ID:-/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/CFDC-FoodPort-network-rg/providers/Microsoft.Network/virtualNetworks/CFDC-FoodPort-vnet/subnets/CFDC-FoodPort-BatchNodes-snet}"
IMAGE_RESOURCE_ID="${IMAGE_RESOURCE_ID:-/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Compute/galleries/development/images/nanopore/versions/${IMAGE_VERSION}}"
SSH_PRIVATE_KEY="${SSH_PRIVATE_KEY:-${HOME}/.ssh/portal_ssh_key}"
SSH_PUBLIC_KEY="${SSH_PUBLIC_KEY:-${HOME}/.ssh/portal_ssh_key.pub}"
AZCOPY_BINARY="${AZCOPY_BINARY:-}"
KEEP_RESOURCES_ON_FAILURE="${KEEP_RESOURCES_ON_FAILURE:-false}"
DIAGNOSTIC_SCHEDULER_OVERRIDE="${DIAGNOSTIC_SCHEDULER_OVERRIDE:-false}"
REMOTE_ROOT="${REMOTE_ROOT:-/mnt/resource/nanopore-acceptance}"
REMOTE_INPUT_DIRECTORY="${REMOTE_ROOT}/download"
REMOTE_OUTPUT_DIRECTORY="${REMOTE_ROOT}/output"
REMOTE_SCRIPT_DIRECTORY="${REMOTE_SCRIPT_DIRECTORY:-/tmp/nanopore-acceptance}"
LOCAL_RESULT_DIRECTORY="${LOCAL_RESULT_DIRECTORY:-${SCRIPT_DIRECTORY}/../docs/acceptance-results/${IMAGE_VERSION}}"
DOWNLOAD_SCRIPT="${SCRIPT_DIRECTORY}/download-acceptance-input.sh"
ACCEPTANCE_SCRIPT="${SCRIPT_DIRECTORY}/run-gpu-acceptance.sh"
SAS_SCRIPT="${SCRIPT_DIRECTORY}/create-input-sas.sh"

run_suffix="$(date --utc '+%Y%m%d%H%M%S')"; safe_version="${IMAGE_VERSION//./-}"
RESOURCE_PREFIX="${RESOURCE_PREFIX:-nanopore-accept-${safe_version}-${run_suffix}}"
GPU_VM_NAME="${GPU_VM_NAME:-${RESOURCE_PREFIX}-vm}"; NIC_NAME="${NIC_NAME:-${RESOURCE_PREFIX}-nic}"; OS_DISK_NAME="${OS_DISK_NAME:-${RESOURCE_PREFIX}-osdisk}"
SSH_KNOWN_HOSTS_FILE="$(mktemp "/tmp/${RESOURCE_PREFIX}-known-hosts.XXXXXXXX")"
NIC_CREATION_STARTED=false; VM_CREATION_STARTED=false; ACCEPTANCE_SUCCEEDED=false
CONTAINER_SAS_URL=""; CONTAINER_SAS_URL_BASE64=""; ACCEPTANCE_POD5_FILES_BASE64=""; VM_CREATE_ERROR_FILE=""; GPU_HOST=""; GPU_SSH_TARGET=""
SSH_OPTIONS=(-i "$SSH_PRIVATE_KEY" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$SSH_KNOWN_HOSTS_FILE")

wait_for_absence() { local kind="$1" name="$2"; for _ in $(seq 1 24); do if [[ "$kind" == vm ]]; then ! az vm show -g "$RESOURCE_GROUP" -n "$name" >/dev/null 2>&1 && return 0; else ! az disk show -g "$RESOURCE_GROUP" -n "$name" >/dev/null 2>&1 && return 0; fi; sleep 5; done; return 1; }
cleanup_failed_attempt() { if az vm show -g "$RESOURCE_GROUP" -n "$GPU_VM_NAME" >/dev/null 2>&1; then az vm delete -g "$RESOURCE_GROUP" -n "$GPU_VM_NAME" --yes >/dev/null 2>&1 || true; wait_for_absence vm "$GPU_VM_NAME" || return 1; fi; if az disk show -g "$RESOURCE_GROUP" -n "$OS_DISK_NAME" >/dev/null 2>&1; then az disk delete -g "$RESOURCE_GROUP" -n "$OS_DISK_NAME" --yes >/dev/null 2>&1 || true; wait_for_absence disk "$OS_DISK_NAME" || return 1; fi; az network nic show -g "$RESOURCE_GROUP" -n "$NIC_NAME" >/dev/null 2>&1; }
cleanup() { local status=$?; trap - EXIT INT TERM; rm -f "${SSH_KNOWN_HOSTS_FILE:-}" "${VM_CREATE_ERROR_FILE:-}" || true; unset CONTAINER_SAS_URL CONTAINER_SAS_URL_BASE64 ACCEPTANCE_POD5_FILES_BASE64 || true; echo; [[ "$ACCEPTANCE_SUCCEEDED" == true ]] && echo "Acceptance completed successfully." || echo "Acceptance failed with status ${status}." >&2; if [[ "$KEEP_RESOURCES_ON_FAILURE" == true && "$ACCEPTANCE_SUCCEEDED" != true ]]; then echo "Preserving ${GPU_VM_NAME}, ${NIC_NAME}, ${OS_DISK_NAME}"; return "$status"; fi; if [[ "$VM_CREATION_STARTED" == true ]] && az vm show -g "$RESOURCE_GROUP" -n "$GPU_VM_NAME" >/dev/null 2>&1; then echo "Deleting temporary GPU VM: ${GPU_VM_NAME}"; az vm delete -g "$RESOURCE_GROUP" -n "$GPU_VM_NAME" --yes >/dev/null 2>&1 || true; wait_for_absence vm "$GPU_VM_NAME" || true; fi; if az disk show -g "$RESOURCE_GROUP" -n "$OS_DISK_NAME" >/dev/null 2>&1; then az disk delete -g "$RESOURCE_GROUP" -n "$OS_DISK_NAME" --yes >/dev/null 2>&1 || true; fi; if [[ "$NIC_CREATION_STARTED" == true ]] && az network nic show -g "$RESOURCE_GROUP" -n "$NIC_NAME" >/dev/null 2>&1; then az network nic delete -g "$RESOURCE_GROUP" -n "$NIC_NAME" >/dev/null 2>&1 || true; fi; return "$status"; }
trap cleanup EXIT; trap 'exit 130' INT; trap 'exit 143' TERM

for cmd in az base64 grep jq mktemp scp seq ssh; do command -v "$cmd" >/dev/null 2>&1 || { echo "Missing command: ${cmd}" >&2; exit 1; }; done
for file in "$DOWNLOAD_SCRIPT" "$ACCEPTANCE_SCRIPT" "$SAS_SCRIPT" "$SSH_PRIVATE_KEY" "$SSH_PUBLIC_KEY"; do [[ -f "$file" ]] || { echo "Missing file: ${file}" >&2; exit 1; }; done
if [[ -n "$AZCOPY_BINARY" ]]; then [[ -f "$AZCOPY_BINARY" && -x "$AZCOPY_BINARY" ]] || { echo "AZCOPY_BINARY must point to an executable file" >&2; exit 1; }; fi
for value_name in EXPECTED_POD5_COUNT EXPECTED_BATCH_COUNT VM_ALLOCATION_ATTEMPTS VM_ALLOCATION_RETRY_SECONDS; do value="${!value_name}"; case "$value" in ''|*[!0-9]*) echo "${value_name} must be numeric" >&2; exit 1;; esac; done
case "$KEEP_RESOURCES_ON_FAILURE:$DIAGNOSTIC_SCHEDULER_OVERRIDE" in true:true|true:false|false:true|false:false) ;; *) echo "Boolean settings must be true or false" >&2; exit 1;; esac
IFS=';' read -r -a files <<<"$ACCEPTANCE_POD5_FILES"; [[ "${#files[@]}" -eq "$EXPECTED_POD5_COUNT" ]] || { echo "POD5 allowlist count mismatch" >&2; exit 1; }
IFS=',' read -r -a counts <<<"$BATCH_FILE_COUNTS"; total=0; for raw in "${counts[@]}"; do n="${raw//[[:space:]]/}"; case "$n" in ''|*[!0-9]*|0) exit 1;; esac; total="$((total+n))"; done
[[ "${#counts[@]}" -eq "$EXPECTED_BATCH_COUNT" && "$total" -eq "$EXPECTED_POD5_COUNT" ]] || { echo "Batch plan mismatch" >&2; exit 1; }
[[ ! -e "$LOCAL_RESULT_DIRECTORY" ]] || { echo "Result directory already exists: ${LOCAL_RESULT_DIRECTORY}" >&2; exit 1; }
ACCEPTANCE_POD5_FILES_BASE64="$(printf '%s' "$ACCEPTANCE_POD5_FILES" | base64 --wrap=0)"

bash -n "${BASH_SOURCE[0]}" "$DOWNLOAD_SCRIPT" "$ACCEPTANCE_SCRIPT" "$SAS_SCRIPT"
az account set --subscription "$SUBSCRIPTION_ID"
CONTAINER_SAS_URL="$(STORAGE_ACCOUNT="$STORAGE_ACCOUNT" CONTAINER_NAME="$CONTAINER_NAME" SAS_HOURS="$SAS_HOURS" SAS_AUTH_MODE="$SAS_AUTH_MODE" STORAGE_RESOURCE_GROUP="$STORAGE_RESOURCE_GROUP" "$SAS_SCRIPT")"
[[ -n "$CONTAINER_SAS_URL" ]] || { echo "Generated SAS URL is empty" >&2; exit 1; }
sas_token="${CONTAINER_SAS_URL#*\?}"; [[ "$sas_token" != "$CONTAINER_SAS_URL" ]] || { echo "SAS token missing" >&2; exit 1; }
content_length="$(az storage blob show --account-name "$STORAGE_ACCOUNT" --container-name "$CONTAINER_NAME" --name "$ACCEPTANCE_VALIDATION_POD5_PATH" --sas-token "$sas_token" --query properties.contentLength -o tsv)"
[[ "$content_length" =~ ^[1-9][0-9]*$ ]] || { echo "SAS validation failed" >&2; exit 1; }
CONTAINER_SAS_URL_BASE64="$(printf '%s' "$CONTAINER_SAS_URL" | base64 --wrap=0)"; unset sas_token

echo "Creating temporary NIC: ${NIC_NAME}"; NIC_CREATION_STARTED=true
az network nic create -g "$RESOURCE_GROUP" -n "$NIC_NAME" --location "$LOCATION" --subnet "$SUBNET_RESOURCE_ID" --accelerated-networking false --tags purpose=nanopore-image-acceptance temporary=true acceptance-prefix="$RESOURCE_PREFIX" >/dev/null

echo "Creating ephemeral GPU VM: ${GPU_VM_NAME}"; VM_CREATION_STARTED=true; allocated=false; VM_CREATE_ERROR_FILE="$(mktemp "/tmp/${RESOURCE_PREFIX}-vm-create-error.XXXXXXXX")"
for attempt in $(seq 1 "$VM_ALLOCATION_ATTEMPTS"); do
  echo "GPU VM allocation attempt ${attempt}/${VM_ALLOCATION_ATTEMPTS}"; : >"$VM_CREATE_ERROR_FILE"
  if az vm create -g "$RESOURCE_GROUP" -n "$GPU_VM_NAME" --location "$LOCATION" --nics "$NIC_NAME" --image "$IMAGE_RESOURCE_ID" --size "$GPU_VM_SIZE" --admin-username "$GPU_ADMIN_USER" --ssh-key-values "$SSH_PUBLIC_KEY" --security-type TrustedLaunch --enable-secure-boot false --enable-vtpm false --os-disk-name "$OS_DISK_NAME" --os-disk-delete-option Delete --nic-delete-option Detach >/dev/null 2>"$VM_CREATE_ERROR_FILE"; then allocated=true; break; fi
  cat "$VM_CREATE_ERROR_FILE" >&2
  grep -qE 'AllocationFailed|OverconstrainedAllocationRequest|ZonalAllocationFailed' "$VM_CREATE_ERROR_FILE" || exit 1
  cleanup_failed_attempt || exit 1
  [[ "$attempt" -eq "$VM_ALLOCATION_ATTEMPTS" ]] || sleep "$VM_ALLOCATION_RETRY_SECONDS"
done
rm -f "$VM_CREATE_ERROR_FILE"; VM_CREATE_ERROR_FILE=""; [[ "$allocated" == true ]] || { echo "GPU allocation failed" >&2; exit 1; }

GPU_HOST="$(az network nic show -g "$RESOURCE_GROUP" -n "$NIC_NAME" --query 'ipConfigurations[0].privateIPAddress' -o tsv)"; [[ -n "$GPU_HOST" ]] || exit 1; GPU_SSH_TARGET="${GPU_ADMIN_USER}@${GPU_HOST}"
for attempt in $(seq 1 40); do ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" 'true' 2>/dev/null && break; [[ "$attempt" -lt 40 ]] || exit 1; sleep 15; done
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" 'nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader'
if [[ -n "$AZCOPY_BINARY" ]]; then
  echo "Transferring host-provided AzCopy binary"
  scp "${SSH_OPTIONS[@]}" "$AZCOPY_BINARY" "${GPU_SSH_TARGET}:/tmp/foodport-azcopy"
  ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" 'sudo install -m 0755 /tmp/foodport-azcopy /usr/local/bin/azcopy && rm -f /tmp/foodport-azcopy'
fi
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" 'bash -s' <<'REMOTE'
set -euo pipefail
if ! command -v azcopy >/dev/null 2>&1; then d=/tmp/azcopy-install; rm -rf "$d"; mkdir -p "$d"; curl --fail --location --retry 5 --retry-all-errors -o "$d/azcopy.tar.gz" https://aka.ms/downloadazcopy-v10-linux; tar -xzf "$d/azcopy.tar.gz" -C "$d"; binary="$(find "$d" -type f -name azcopy -print -quit)"; [[ -n "$binary" ]]; sudo install -m 0755 "$binary" /usr/local/bin/azcopy; rm -rf "$d"; fi
azcopy --version
REMOTE

ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" "mkdir -p '${REMOTE_SCRIPT_DIRECTORY}'"
scp "${SSH_OPTIONS[@]}" "$DOWNLOAD_SCRIPT" "$ACCEPTANCE_SCRIPT" "${GPU_SSH_TARGET}:${REMOTE_SCRIPT_DIRECTORY}/"
if [[ -n "$PORESIPPR_SCHEDULER_SOURCE" ]]; then scp "${SSH_OPTIONS[@]}" "$PORESIPPR_SCHEDULER_SOURCE" "${GPU_SSH_TARGET}:${REMOTE_SCRIPT_DIRECTORY}/poresippr_incremental_dorado_scheduler.py"; PORESIPPR_SCHEDULER="${REMOTE_SCRIPT_DIRECTORY}/poresippr_incremental_dorado_scheduler.py"; fi
ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" bash -s -- "$REMOTE_ROOT" "$REMOTE_INPUT_DIRECTORY" "$REMOTE_OUTPUT_DIRECTORY" "$REMOTE_SCRIPT_DIRECTORY" <<'REMOTE'
set -euo pipefail
sudo mkdir -p "$1"; sudo chown -R "$(id -u):$(id -g)" "$1"; rm -rf "$2" "$3"; mkdir -p "$2" "$3"; chmod 0755 "$4"/*.sh
REMOTE

ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" env "CONTAINER_SAS_URL_BASE64=${CONTAINER_SAS_URL_BASE64}" "ACCEPTANCE_POD5_DIRECTORY=${ACCEPTANCE_POD5_DIRECTORY}" "ACCEPTANCE_POD5_FILES_BASE64=${ACCEPTANCE_POD5_FILES_BASE64}" "EXPECTED_POD5_COUNT=${EXPECTED_POD5_COUNT}" "INPUT_DIRECTORY=${REMOTE_INPUT_DIRECTORY}" "${REMOTE_SCRIPT_DIRECTORY}/download-acceptance-input.sh"
unset CONTAINER_SAS_URL CONTAINER_SAS_URL_BASE64 ACCEPTANCE_POD5_FILES_BASE64

ssh "${SSH_OPTIONS[@]}" "$GPU_SSH_TARGET" env "IMAGE_VERSION=${IMAGE_VERSION}" "EXPECTED_WRAPPER_VERSION=${EXPECTED_WRAPPER_VERSION}" "DORADO_MODEL=${DORADO_MODEL}" "BARCODE_KIT=${BARCODE_KIT}" "BARCODE_VALUES=${BARCODE_VALUES}" "BATCH_FILE_COUNTS=${BATCH_FILE_COUNTS}" "EXPECTED_POD5_COUNT=${EXPECTED_POD5_COUNT}" "EXPECTED_BATCH_COUNT=${EXPECTED_BATCH_COUNT}" "INPUT_DIRECTORY=${REMOTE_INPUT_DIRECTORY}" "OUTPUT_DIRECTORY=${REMOTE_OUTPUT_DIRECTORY}" "PORESIPPR_ENVIRONMENT=${PORESIPPR_ENVIRONMENT}" "PORESIPPR_SCHEDULER=${PORESIPPR_SCHEDULER}" "PORESIPPR_TARGETS=${PORESIPPR_TARGETS}" "PORESIPPR_REPOSITORY_COMMIT=${PORESIPPR_REPOSITORY_COMMIT}" "PORESIPPR_TARGETS_SHA256=${PORESIPPR_TARGETS_SHA256}" "PORESIPPR_TARGETS_SEQUENCE_COUNT=${PORESIPPR_TARGETS_SEQUENCE_COUNT}" "DIAGNOSTIC_SCHEDULER_OVERRIDE=${DIAGNOSTIC_SCHEDULER_OVERRIDE}" "${REMOTE_SCRIPT_DIRECTORY}/run-gpu-acceptance.sh"

mkdir -p "$LOCAL_RESULT_DIRECTORY"
for name in acceptance-result.json acceptance-result.md summary.tsv barcode-counts.tsv mapping-discovery.tsv assertion-results.tsv scheduler-state.json scheduler-status.json scheduler-run.csv scheduler-metadata.csv scheduler-evidence.tar.gz; do scp "${SSH_OPTIONS[@]}" "${GPU_SSH_TARGET}:${REMOTE_OUTPUT_DIRECTORY}/${name}" "$LOCAL_RESULT_DIRECTORY/"; done
for file in "$LOCAL_RESULT_DIRECTORY"/*; do [[ -s "$file" ]] || { echo "Empty result: ${file}" >&2; exit 1; }; done
jq -e --arg version "$IMAGE_VERSION" --arg commit "$PORESIPPR_REPOSITORY_COMMIT" --argjson pod5 "$EXPECTED_POD5_COUNT" --argjson batches "$EXPECTED_BATCH_COUNT" --argjson diagnostic "$DIAGNOSTIC_SCHEDULER_OVERRIDE" '.schema_version==4 and .diagnostic_scheduler_override==$diagnostic and .accepted==($diagnostic|not) and .image_version==$version and .poresippr.repository_commit==$commit and .input.pod5_count==$pod5 and .scheduler.processed_pod5_count==$pod5 and .scheduler.batch_count==$batches and .scheduler.duplicate_processing_prevented==true and .mapping.discovery_mode==false and .mapping.per_iteration_assertions==true and .mapping.assertion_count==40 and .mapping.assertion_pass_count==40' "$LOCAL_RESULT_DIRECTORY/acceptance-result.json" >/dev/null
jq -r '"Accepted: \(.accepted)\nImage: \(.image_version)\nPOD5: \(.input.pod5_count)\nBatch plan: \(.input.batch_plan)\nBatches: \(.scheduler.batch_count)\nMapping assertions: \(.mapping.assertion_pass_count)/\(.mapping.assertion_count)\nDuplicate prevention: \(.scheduler.duplicate_processing_prevented)"' "$LOCAL_RESULT_DIRECTORY/acceptance-result.json"
ACCEPTANCE_SUCCEEDED=true
echo "Results: ${LOCAL_RESULT_DIRECTORY}"
