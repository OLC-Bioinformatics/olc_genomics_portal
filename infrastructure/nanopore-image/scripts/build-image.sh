#!/usr/bin/env bash

set -euo pipefail

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

if [[ -z "$IMAGE_VERSION" ]]; then
  echo \
    "Unable to read image_version from ${VARIABLE_FILE}" \
    >&2
  exit 1
fi

if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo \
    "Unable to read subscription_id from ${VARIABLE_FILE}" \
    >&2
  exit 1
fi

if [[ -z "$BUILD_RESOURCE_GROUP" ]]; then
  echo \
    "Unable to read build_resource_group from ${VARIABLE_FILE}" \
    >&2
  exit 1
fi

if [[ -z "$GALLERY_NAME" ]]; then
  echo \
    "Unable to read gallery_name from ${VARIABLE_FILE}" \
    >&2
  exit 1
fi

if [[ -z "$IMAGE_NAME" ]]; then
  echo \
    "Unable to read image_name from ${VARIABLE_FILE}" \
    >&2
  exit 1
fi

for command_name in az packer; do
  if ! command -v "$command_name" >/dev/null; then
    echo \
      "Required build command is missing: ${command_name}" \
      >&2
    exit 1
  fi
done

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
