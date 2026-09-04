#!/usr/bin/env bash

set -euo pipefail

STORAGE_ACCOUNT="${STORAGE_ACCOUNT:?STORAGE_ACCOUNT is required}"
CONTAINER_NAME="${CONTAINER_NAME:?CONTAINER_NAME is required}"
SAS_HOURS="${SAS_HOURS:-4}"
SAS_AUTH_MODE="${SAS_AUTH_MODE:-key}"
STORAGE_RESOURCE_GROUP="${STORAGE_RESOURCE_GROUP:-CFDC-FoodPort-Batch-rg}"

start_time="$(
  date \
    --utc \
    --date='5 minutes ago' \
    '+%Y-%m-%dT%H:%MZ'
)"

expiry_time="$(
  date \
    --utc \
    --date="+${SAS_HOURS} hours" \
    '+%Y-%m-%dT%H:%MZ'
)"

case "$SAS_AUTH_MODE" in
  login)
    sas_token="$(
      az storage container generate-sas \
        --account-name "$STORAGE_ACCOUNT" \
        --name "$CONTAINER_NAME" \
        --permissions rl \
        --start "$start_time" \
        --expiry "$expiry_time" \
        --auth-mode login \
        --as-user \
        --https-only \
        --output tsv
    )"
    ;;

  key)
    storage_account_key="$(
      az storage account keys list \
        --resource-group "$STORAGE_RESOURCE_GROUP" \
        --account-name "$STORAGE_ACCOUNT" \
        --query '[0].value' \
        --output tsv
    )"

    if [[ -z "$storage_account_key" ]]; then
      echo \
        "Could not obtain an access key for ${STORAGE_ACCOUNT}" \
        >&2
      exit 1
    fi

    sas_token="$(
      az storage container generate-sas \
        --account-name "$STORAGE_ACCOUNT" \
        --account-key "$storage_account_key" \
        --name "$CONTAINER_NAME" \
        --permissions rl \
        --start "$start_time" \
        --expiry "$expiry_time" \
        --https-only \
        --output tsv
    )"

    unset storage_account_key
    ;;

  *)
    echo "Unsupported SAS_AUTH_MODE: ${SAS_AUTH_MODE}" >&2
    echo "Expected one of: login, key" >&2
    exit 1
    ;;
esac

if [[ -z "$sas_token" ]]; then
  echo "The generated SAS token is empty" >&2
  exit 1
fi

printf 'https://%s.blob.core.windows.net/%s?%s\n' \
  "$STORAGE_ACCOUNT" \
  "$CONTAINER_NAME" \
  "$sas_token"