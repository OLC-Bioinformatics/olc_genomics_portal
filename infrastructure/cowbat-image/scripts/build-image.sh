#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIRECTORY="$(
  cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"
IMAGE_ROOT="$(
  cd -- "${SCRIPT_DIRECTORY}/.." &&
    pwd
)"
PACKER_DIRECTORY="${IMAGE_ROOT}/packer"
PACKER_TEMPLATE="${PACKER_DIRECTORY}/cowbat.pkr.hcl"
VARIABLE_FILE="${PACKER_DIRECTORY}/development.pkrvars.hcl"
ROOT_CA_FILE="${IMAGE_ROOT}/../../GoC-GdC-Root-A.crt"

read_pkrvar() {
  awk -F'"' -v name="$1" \
    '$0 ~ "^[[:space:]]*" name "[[:space:]]*=" { print $2; exit }' \
    "$VARIABLE_FILE"
}

SUBSCRIPTION_ID="$(read_pkrvar subscription_id)"
IMAGE_VERSION="$(read_pkrvar image_version)"
SOURCE_IMAGE_VERSION="$(read_pkrvar source_image_version)"
RESOURCE_GROUP="$(read_pkrvar resource_group)"
GALLERY_NAME="$(read_pkrvar gallery_name)"
IMAGE_NAME="$(read_pkrvar image_name)"

for required_value in \
  SUBSCRIPTION_ID \
  IMAGE_VERSION \
  SOURCE_IMAGE_VERSION \
  RESOURCE_GROUP \
  GALLERY_NAME \
  IMAGE_NAME; do
  if [[ -z "${!required_value}" ]]; then
    echo "Unable to read ${required_value} from ${VARIABLE_FILE}" >&2
    exit 1
  fi
done

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required; install it and run az login first." >&2
  exit 1
fi
if ! command -v packer >/dev/null 2>&1; then
  echo "Packer is required; install Packer 1.10 or later first." >&2
  exit 1
fi
if [[ ! -s "$ROOT_CA_FILE" ]]; then
  echo \
    "The repository-root GoC-GdC-Root-A.crt secure file is missing or empty." \
    >&2
  exit 1
fi

az account set --subscription "$SUBSCRIPTION_ID"
active_subscription="$(az account show --query id --output tsv)"
if [[ "$active_subscription" != "$SUBSCRIPTION_ID" ]]; then
  echo "Azure CLI is using an unexpected subscription." >&2
  exit 1
fi

az sig image-version show \
  --resource-group "$RESOURCE_GROUP" \
  --gallery-name "$GALLERY_NAME" \
  --gallery-image-definition "$IMAGE_NAME" \
  --gallery-image-version "$SOURCE_IMAGE_VERSION" \
  --output none

existing_version="$(
  az sig image-version list \
    --resource-group "$RESOURCE_GROUP" \
    --gallery-name "$GALLERY_NAME" \
    --gallery-image-definition "$IMAGE_NAME" \
    --query "[?name=='${IMAGE_VERSION}'].name | [0]" \
    --output tsv
)"
if [[ -n "$existing_version" && "$existing_version" != "None" ]]; then
  echo "Gallery version ${IMAGE_VERSION} already exists; refusing to overwrite." >&2
  exit 1
fi

cd "$PACKER_DIRECTORY"
packer init "$PACKER_TEMPLATE"
packer fmt -check "$PACKER_TEMPLATE"
packer validate -var-file="$VARIABLE_FILE" "$PACKER_TEMPLATE"

PACKER_LOG=1 \
  packer build \
    -on-error=cleanup \
    -var-file="$VARIABLE_FILE" \
    "$PACKER_TEMPLATE"

az sig image-version show \
  --resource-group "$RESOURCE_GROUP" \
  --gallery-name "$GALLERY_NAME" \
  --gallery-image-definition "$IMAGE_NAME" \
  --gallery-image-version "$IMAGE_VERSION" \
  --query \
    '{id:id,state:provisioningState,regions:publishingProfile.targetRegions}' \
  --output yaml

published_state="$(
  az sig image-version show \
    --resource-group "$RESOURCE_GROUP" \
    --gallery-name "$GALLERY_NAME" \
    --gallery-image-definition "$IMAGE_NAME" \
    --gallery-image-version "$IMAGE_VERSION" \
    --query provisioningState \
    --output tsv
)"
if [[ "$published_state" != "Succeeded" ]]; then
  echo "Gallery image publication did not succeed: ${published_state}" >&2
  exit 1
fi
