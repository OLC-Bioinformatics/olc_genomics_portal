#!/usr/bin/env bash

set -euo pipefail

storage_account_key=""
targets_sas_token=""

cleanup() {
  unset storage_account_key
  unset targets_sas_token
  unset PKR_VAR_poresippr_targets_url
  unset PKR_VAR_poresippr_targets_sha256
}

trap cleanup EXIT

SCRIPT_DIRECTORY="$(
  cd \
    -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"

IMAGE_ROOT="$(
  cd \
    -- "${SCRIPT_DIRECTORY}/.." &&
    pwd
)"

PACKER_DIRECTORY="${IMAGE_ROOT}/packer"
PACKER_TEMPLATE="${PACKER_DIRECTORY}/nanopore.pkr.hcl"
VARIABLE_FILE="${PACKER_DIRECTORY}/development.pkrvars.hcl"

PORESIPPR_TARGETS_STORAGE_ACCOUNT="carlingst01"
PORESIPPR_TARGETS_STORAGE_RESOURCE_GROUP="CFDC-FoodPort-Batch-rg"
PORESIPPR_TARGETS_CONTAINER="poresippr-data"
PORESIPPR_TARGETS_BLOB="PoreSippR_DB_251110.fasta"
PORESIPPR_TARGETS_SHA256="6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9"
PORESIPPR_TARGETS_SAS_LIFETIME_HOURS="4"

IMAGE_VERSION="$(
  awk -F'"' \
    '/^[[:space:]]*image_version[[:space:]]*=/ {
      print $2
      exit
    }' \
    "$VARIABLE_FILE"
)"

SUBSCRIPTION_ID="$(
  awk -F'"' \
    '/^[[:space:]]*subscription_id[[:space:]]*=/ {
      print $2
      exit
    }' \
    "$VARIABLE_FILE"
)"

BUILD_RESOURCE_GROUP="$(
  awk -F'"' \
    '/^[[:space:]]*build_resource_group[[:space:]]*=/ {
      print $2
      exit
    }' \
    "$VARIABLE_FILE"
)"

GALLERY_NAME="$(
  awk -F'"' \
    '/^[[:space:]]*gallery_name[[:space:]]*=/ {
      print $2
      exit
    }' \
    "$VARIABLE_FILE"
)"

IMAGE_NAME="$(
  awk -F'"' \
    '/^[[:space:]]*image_name[[:space:]]*=/ {
      print $2
      exit
    }' \
    "$VARIABLE_FILE"
)"

for required_value in \
  IMAGE_VERSION \
  SUBSCRIPTION_ID \
  BUILD_RESOURCE_GROUP \
  GALLERY_NAME \
  IMAGE_NAME; do
  if [[ -z "${!required_value}" ]]; then
    echo \
      "Unable to read ${required_value} from ${VARIABLE_FILE}" \
      >&2
    exit 1
  fi
done

if [[ ! "$PORESIPPR_TARGETS_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo \
    "PORESIPPR_TARGETS_SHA256 must be a lowercase SHA-256" \
    >&2
  exit 1
fi

if [[ ! "$PORESIPPR_TARGETS_SAS_LIFETIME_HOURS" =~ ^[1-9][0-9]*$ ]]; then
  echo \
    "PORESIPPR_TARGETS_SAS_LIFETIME_HOURS must be a positive integer" \
    >&2
  exit 1
fi

for command_name in az date packer; do
  if ! command -v "$command_name" >/dev/null; then
    echo \
      "Required build command is missing: ${command_name}" \
      >&2
    exit 1
  fi
done

BUILD_LOG="${PACKER_DIRECTORY}/packer-build-${IMAGE_VERSION}.log"

if [[ -e "$BUILD_LOG" ]]; then
  echo \
    "Build log already exists: ${BUILD_LOG}" \
    >&2
  echo \
    "Move or remove the existing log before starting a new build." \
    >&2
  exit 1
fi

echo "Validating Azure CLI authentication"

if ! az account show \
    --query id \
    --output tsv \
    >/dev/null 2>&1; then
  echo \
    "Azure CLI authentication is unavailable. Run az login first." \
    >&2
  exit 1
fi

echo "Selecting Azure subscription: ${SUBSCRIPTION_ID}"

az account set \
  --subscription "$SUBSCRIPTION_ID"

active_subscription="$(
  az account show \
    --query id \
    --output tsv
)"

if [[ "$active_subscription" != "$SUBSCRIPTION_ID" ]]; then
  echo "Unexpected active Azure subscription" >&2
  echo "Expected: ${SUBSCRIPTION_ID}" >&2
  echo "Actual:   ${active_subscription}" >&2
  exit 1
fi

echo "Azure account:"
az account show \
  --query \
    '{subscription:id,name:name,tenant:tenantId,principal:user.name,type:user.type}' \
  --output yaml

echo "Validating target image version availability"

if az sig image-version show \
    --resource-group "$BUILD_RESOURCE_GROUP" \
    --gallery-name "$GALLERY_NAME" \
    --gallery-image-definition "$IMAGE_NAME" \
    --gallery-image-version "$IMAGE_VERSION" \
    >/dev/null 2>&1; then
  echo \
    "Image version ${GALLERY_NAME}/${IMAGE_NAME}/${IMAGE_VERSION} " \
    "already exists and cannot be rebuilt." \
    >&2
  exit 1
fi

echo \
  "Image version ${GALLERY_NAME}/${IMAGE_NAME}/${IMAGE_VERSION} " \
  "is available."

echo "Validating PoreSippR target storage account"

storage_account_resource_group="$(
  az storage account show \
    --name "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    --query resourceGroup \
    --output tsv
)"

if [[ -z "$storage_account_resource_group" ]]; then
  echo \
    "Could not determine the resource group for storage account " \
    "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    >&2
  exit 1
fi

if [[ "$storage_account_resource_group" != "$PORESIPPR_TARGETS_STORAGE_RESOURCE_GROUP" ]]; then
  echo "Unexpected PoreSippR target storage resource group" >&2
  echo "Expected: ${PORESIPPR_TARGETS_STORAGE_RESOURCE_GROUP}" >&2
  echo "Actual:   ${storage_account_resource_group}" >&2
  exit 1
fi

echo "Obtaining PoreSippR target storage account key"

storage_account_key="$(
  az storage account keys list \
    --resource-group "$PORESIPPR_TARGETS_STORAGE_RESOURCE_GROUP" \
    --account-name "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    --query '[0].value' \
    --output tsv
)"

if [[ -z "$storage_account_key" ]]; then
  echo \
    "Could not obtain an access key for " \
    "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    >&2
  exit 1
fi

echo "Validating PoreSippR target blob"

target_blob_exists="$(
  az storage blob exists \
    --account-name "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    --account-key "$storage_account_key" \
    --container-name "$PORESIPPR_TARGETS_CONTAINER" \
    --name "$PORESIPPR_TARGETS_BLOB" \
    --query exists \
    --output tsv
)"

if [[ "$target_blob_exists" != "true" ]]; then
  echo \
    "PoreSippR target blob is missing or inaccessible" \
    >&2
  exit 1
fi

targets_sas_start="$(
  date \
    --utc \
    --date='5 minutes ago' \
    '+%Y-%m-%dT%H:%MZ'
)"

targets_sas_expiry="$(
  date \
    --utc \
    --date="+${PORESIPPR_TARGETS_SAS_LIFETIME_HOURS} hours" \
    '+%Y-%m-%dT%H:%MZ'
)"

echo "Generating short-lived PoreSippR target SAS"

targets_sas_token="$(
  az storage blob generate-sas \
    --account-name "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    --account-key "$storage_account_key" \
    --container-name "$PORESIPPR_TARGETS_CONTAINER" \
    --name "$PORESIPPR_TARGETS_BLOB" \
    --permissions r \
    --start "$targets_sas_start" \
    --expiry "$targets_sas_expiry" \
    --https-only \
    --output tsv
)"

unset storage_account_key
storage_account_key=""

if [[ -z "$targets_sas_token" ]]; then
  echo \
    "Azure CLI returned an empty PoreSippR target SAS token" \
    >&2
  exit 1
fi

PKR_VAR_poresippr_targets_url="$(
  printf \
    'https://%s.blob.core.windows.net/%s/%s?%s' \
    "$PORESIPPR_TARGETS_STORAGE_ACCOUNT" \
    "$PORESIPPR_TARGETS_CONTAINER" \
    "$PORESIPPR_TARGETS_BLOB" \
    "$targets_sas_token"
)"

unset targets_sas_token
targets_sas_token=""

PKR_VAR_poresippr_targets_sha256="$PORESIPPR_TARGETS_SHA256"

export PKR_VAR_poresippr_targets_url
export PKR_VAR_poresippr_targets_sha256

if [[ -z "$PKR_VAR_poresippr_targets_url" ]]; then
  echo \
    "Azure CLI returned an empty PoreSippR target SAS URL" \
    >&2
  exit 1
fi

if [[ "$PKR_VAR_poresippr_targets_url" != https://* ]]; then
  echo \
    "Generated PoreSippR target SAS URL does not use HTTPS" \
    >&2
  exit 1
fi

targets_url_without_query="${PKR_VAR_poresippr_targets_url%%\?*}"

if [[ "$targets_url_without_query" != */"$PORESIPPR_TARGETS_BLOB" ]]; then
  echo \
    "Generated PoreSippR target SAS URL does not identify " \
    "$PORESIPPR_TARGETS_BLOB" \
    >&2
  exit 1
fi

if [[ ! "$PKR_VAR_poresippr_targets_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  echo \
    "Generated Packer target checksum is not a lowercase SHA-256" \
    >&2
  exit 1
fi

echo "PoreSippR target SAS generated successfully"
echo "PoreSippR target SAS expiry: ${targets_sas_expiry}"
echo \
  "PoreSippR target SHA-256: " \
  "$PKR_VAR_poresippr_targets_sha256"

echo "Initializing Packer plugins"

packer init \
  "$PACKER_TEMPLATE"

echo "Checking Packer formatting"

packer fmt \
  -check \
  "$PACKER_TEMPLATE" \
  "$VARIABLE_FILE"

echo "Validating Packer configuration"

packer validate \
  -var-file="$VARIABLE_FILE" \
  "$PACKER_TEMPLATE"

echo "Starting FoodPort Nanopore image build"
echo "Image version: ${IMAGE_VERSION}"
echo "Build log: ${BUILD_LOG}"

PACKER_LOG=1 \
PACKER_LOG_PATH="$BUILD_LOG" \
packer build \
  -var-file="$VARIABLE_FILE" \
  "$PACKER_TEMPLATE"

echo "Verifying published gallery image"

az sig image-version show \
  --resource-group "$BUILD_RESOURCE_GROUP" \
  --gallery-name "$GALLERY_NAME" \
  --gallery-image-definition "$IMAGE_NAME" \
  --gallery-image-version "$IMAGE_VERSION" \
  --query \
    '{id:id,state:provisioningState,published:publishingProfile.publishedDate,regions:publishingProfile.targetRegions}' \
  --output yaml

published_state="$(
  az sig image-version show \
    --resource-group "$BUILD_RESOURCE_GROUP" \
    --gallery-name "$GALLERY_NAME" \
    --gallery-image-definition "$IMAGE_NAME" \
    --gallery-image-version "$IMAGE_VERSION" \
    --query provisioningState \
    --output tsv
)"

if [[ "$published_state" != "Succeeded" ]]; then
  echo \
    "Published image did not report provisioning state Succeeded: " \
    "$published_state" \
    >&2
  exit 1
fi

echo "Checking for remaining temporary Packer resources"

az resource list \
  --resource-group "$BUILD_RESOURCE_GROUP" \
  --query \
    "[?contains(name, 'pkr')].{name:name,type:type}" \
  --output table

echo \
  "FoodPort Nanopore image ${GALLERY_NAME}/${IMAGE_NAME}/${IMAGE_VERSION} " \
  "was built successfully."
