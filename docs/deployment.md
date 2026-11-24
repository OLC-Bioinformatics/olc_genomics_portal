# Deployment guide

This guide covers the portal's Azure DevOps deployment pipelines. Confirm
current pipeline registration, permissions, service connections, and target
environment values in Azure DevOps before running a deployment; those details
can change independently of this repository.

## Pipeline overview

| Pipeline file | Purpose |
|---|---|
| `azure-pipelines-dev.yml` | Provisions and deploys the development VM. |
| `azure-pipelines-full-infra.yml` | Provisions and deploys the full development infrastructure. |
| `azure-pipelines-update.yml` | Updates application code on the existing production VM from `origin/main`. |
| `azure-pipelines-ci.yml` | Runs CI checks and tests; it does not deploy. |

The direct update pipeline is triggered by pushes to `main`. It validates the
VM checkout, fast-forwards it to `origin/main`, updates the managed CA
certificate, and runs `docker compose up -d --build --wait`. It checks the
portal's local HTTP response after startup. A failed post-deployment check
reports failure but does not automatically roll back.

The direct production update pipeline does not read
`FoodPort-Dev-Portal-Secrets` and does not regenerate the VM's `env` file.
Changes to that Dev variable group do not update production secrets.

The dedicated Dev and full-infrastructure pipelines check out the Terraform
repository `FoodPort/foodport-tf`, using its `dev-vm` or `full-infra`
configuration. Pipeline access to that repository and the required Azure
service connection must be authorized before running them.

## Production rollback

Prefer reverting a change in a pull request to `main` and allowing the normal
pipeline to deploy the reviewed revert. Before any rollback, assess database
migration compatibility and current backups; reverting application code does
not undo database changes.

For an emergency rollback, an authorized operator should pause the automatic
update pipeline, record the current commit, and verify that the VM checkout is
clean and its local commits are safely pushed. After confirming the chosen
earlier commit and database compatibility, check out that commit detached and
run from the checkout:

```bash
docker compose up -d --build --wait --wait-timeout 300
```

Verify application behavior and service health. The normal pipeline refuses a
detached checkout; return to a clean, tracked branch before re-enabling it.

## Development deployment variable group

The Dev and full-infrastructure pipelines use the `FoodPort-Dev-Portal-Secrets`
variable group in the **FoodPort** project. The Dev pipeline reads all the
values in the table below from this group. The full-infrastructure pipeline
reads the Key Vault-backed values directly from Key Vault; it uses this group
for `RAG_TRUSTED_SERVICE_TOKEN` and optional Nanopore settings. Authorize both
pipelines under the variable group's **Pipeline permissions**; do not enable
access for all pipelines.

The group uses the non-secret `FOODPORT_SECRET_VARIABLES_READY` flag to prevent
deployment while placeholders remain. Keep it `false` until required values
have been set. The table maps the 48 Key Vault-backed values required by the
Dev pipeline and the directly supplied `RAG_TRUSTED_SERVICE_TOKEN`.

In Azure DevOps, open **Pipelines > Library >
FoodPort-Dev-Portal-Secrets**. For each required Key Vault-backed entry, edit
the existing variable using the exact Azure DevOps variable name in the first
column and enter the current value of the corresponding Key Vault secret.
Keep every value marked secret. If you cannot read a Key Vault value, ask an
authorized secret owner to enter it. Do not put secret values in source
control, pipeline logs, or chat.

`RAG_TRUSTED_SERVICE_TOKEN` is also required and must be marked secret. It is
the shared Bearer credential used by the Redmine Assistant plugin and RAG
service. Generate a high-entropy single-line value (for example,
`openssl rand -hex 32`) and enter it directly in the variable group; it is not
fetched from Key Vault. Both Dev and full-infrastructure deployments write it
to the generated `env` file. Production token provisioning and rotation must
use the production environment's approved secret-handling process.

`AZURE_ACCOUNT_KEY` is a storage-account secret. The `BATCH_ACCOUNT_*`
variables configure the Azure Batch account. Keep both sets of credentials
secret. `VM_IMAGE` is the Azure Compute Gallery image-version resource ID used
by new Batch pools, not the portal VM's operating-system image. Updating
`VM_IMAGE` does not change existing Batch pools.

| Azure DevOps variable | `env` variable | Key Vault secret or source |
|---|---|---|
| `foodport_django_secret_key` | `SECRET_KEY` | `secret-key` |
| `foodport_django_secret_key_base` | `SECRET_KEY_BASE` | `secret-key-base` |
| `REDMINE_DB_PASSWORD` | `REDMINE_DB_PASSWORD` | `redmine-database-password` |
| `RAG_DB_PASSWORD` | `RAG_DB_PASSWORD` | `rag-db-password` |
| `AZURE_ACCOUNT_NAME` | `AZURE_ACCOUNT_NAME` | `azure-account-name` |
| `AZURE_ACCOUNT_KEY` | `AZURE_ACCOUNT_KEY` | `azure-account-key` |
| `BATCH_ACCOUNT_NAME` | `BATCH_ACCOUNT_NAME` | `batch-account-name` |
| `BATCH_ACCOUNT_URL` | `BATCH_ACCOUNT_URL` | `batch-account-url` |
| `BATCH_ACCOUNT_KEY` | `BATCH_ACCOUNT_KEY` | `batch-account-key` |
| `BATCH_ACCOUNT_SUBNET` | `BATCH_ACCOUNT_SUBNET` | `batch-account-subnet` |
| `VM_IMAGE` | `VM_IMAGE` | `vm-image` |
| `COWBAT_NODE_AGENT_SKU` | `COWBAT_NODE_AGENT_SKU` | `cowbat-node-agent-sku` |
| `AMPLISEQ_IMAGE` | `AMPLISEQ_IMAGE` | `ampliseq-image` |
| `AMPLISEQ_NODE_AGENT_SKU` | `AMPLISEQ_NODE_AGENT_SKU` | `ampliseq-node-agent-sku` |
| `COWSNPHR_IMAGE` | `COWSNPHR_IMAGE` | `cowsnphr-image` |
| `COWSNPHR_NODE_AGENT_SKU` | `COWSNPHR_NODE_AGENT_SKU` | `cowsnphr-node-agent-sku` |
| `AD_APP_ID` | `AD_APP_ID` | `ad-app-id` |
| `AD_APP_SECRET_ID` | `AD_APP_SECRET_ID` | `ad-app-secret-id` |
| `AD_APP_SECRET` | `AD_APP_SECRET` | `ad-app-secret` |
| `ENTRA_ID_CLIENT_ID` | `ENTRA_ID_CLIENT_ID` | `entra-id-client-id` |
| `ENTRA_ID_CLIENT_SECRET` | `ENTRA_ID_CLIENT_SECRET` | `entra-id-client-secret` |
| `ENTRA_TENANT_ID` | `ENTRA_TENANT_ID` | `entra-id-tenant-id` |
| `REDMINE_DEFAULT_FROM` | `REDMINE_DEFAULT_FROM` | `redmine-default-from` |
| `EMAIL_HOST_USER` | `EMAIL_HOST_USER` | `email-host-user` |
| `EMAIL_HOST_PASSWORD` | `EMAIL_HOST_PASSWORD` | `email-host-password` |
| `EMAIL_RELAY_SECRET` | `EMAIL_RELAY_SECRET` | `email-relay-secret` |
| `DB_NAME` | `DB_NAME` | `db-name` |
| `DB_USER` | `DB_USER` | `db-user` |
| `DB_PASS` | `DB_PASS` | `db-pass` |
| `DB_SERVICE` | `DB_SERVICE` | `db-service` |
| `DB_PORT` | `DB_PORT` | `db-port` |
| `DJANGO_EMAIL_BACKEND` | `DJANGO_EMAIL_BACKEND` | `django-email-backend` |
| `SENTRY_DSN` | `SENTRY_DSN` | `sentry-dsn` |
| `ENVIRONMENT` | `ENVIRONMENT` | `environment` |
| `CONNECTION_STRING` | `CONNECTION_STRING` | `connection-string` |
| `SITE_URL` | `SITE_URL` | `site-url` |
| `DJANGO_SERVER_NAME` | `DJANGO_SERVER_NAME` | `django-server-name` |
| `REDMINE_SERVER_NAME` | `REDMINE_SERVER_NAME` | `redmine-server-name` |
| `API_KEY` | `API_KEY` | `api-key` |
| `DEV_API_KEY` | `DEV_API_KEY` | `dev-api-key` |
| `MARIA_DB_ROOT_PASSWORD` | `MARIA_DB_ROOT_PASSWORD` | `maria-db-root-password` |
| `MYSQL_PASSWORD` | `MYSQL_PASSWORD` | `mysql-password` |
| `SUBSCRIPTION_ID` | `SUBSCRIPTION_ID` | `subscription-id` |
| `TENANT_ID` | `TENANT_ID` | `tenant-id` |
| `CLIENT_ID` | `CLIENT_ID` | `client-id` |
| `CLIENT_SECRET` | `CLIENT_SECRET` | `client-secret` |
| `RESOURCE_GROUP_NAME` | `RESOURCE_GROUP_NAME` | `resource-group-name` |
| `STORAGE_ACCOUNT_NAME` | `STORAGE_ACCOUNT_NAME` | `storage-account-name` |
| `RAG_TRUSTED_SERVICE_TOKEN` | `RAG_TRUSTED_SERVICE_TOKEN` | Direct secret; not in Key Vault |

`NANOPORE_IMAGE` and `NANOPORE_NODE_AGENT_SKU` are optional variable-group
values. Set both to the verified image-version resource ID and compatible node
agent SKU before enabling Nanopore runs. Optional Entra desktop settings are
documented in the [desktop Entra setup guide](ENTRA_DESKTOP_SETUP.md).

After setting all required values, set `FOODPORT_SECRET_VARIABLES_READY` to
`true`. Authorize the `FoodPort-dev-VM` and `FoodPort-full-infra` pipelines to
use the group.

## Self-hosted agents

The deploy pipelines can use a self-hosted Azure DevOps agent when a hosted
agent cannot reach the portal VM's private network. Configure an agent pool
with outbound access to Azure DevOps and network access to the VM; no public
inbound SSH to the agent is required. Restrict its permissions and avoid
leaving it running when not needed.

For dedicated pipeline runs, the configured pipeline must receive the SSH
public key as a pipeline variable. Example:

```bash
az extension add --name azure-devops
az devops configure --defaults \
  organization=https://dev.azure.com/CFIA-DevOps-ACIA project=FoodPort

az pipelines run --name "<your-dev-pipeline-name>" --branch main \
  --parameters destroy=false deployEnvironment=dev useHostedAgent=false bootstrapRemoteVm=true \
  --variables sshPublicKey="<public-key>"
```

Use the full-infrastructure pipeline's registered name and the same parameters
to run that pipeline.
The direct update pipeline can be run with:

```bash
az pipelines run --name "<your-update-pipeline-name>" --branch main
```

Check current pipeline names, parameters, approvals, secure files, agent-pool
permissions, and service-connection authorization in Azure DevOps before
running. Do not pass secret values as command-line parameters.
