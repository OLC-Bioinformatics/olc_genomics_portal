# FoodPort Genomics Portal

[![Build Status](https://dev.azure.com/CFIA-DevOps-ACIA/FoodPort/_apis/build/status%2FFoodport-CI?branchName=dev)](https://dev.azure.com/CFIA-DevOps-ACIA/FoodPort/_build/latest?definitionId=1064&branchName=dev)

A web portal for accessing CFIA genomic data, with some tools included.

The portal runs with Docker Compose. Django and Celery use internal Batch and
Storage services rather than calling cloud-provider SDKs directly or accessing
the host Docker socket. Azure is the current backend; a local-cluster backend
is planned.

## Run locally

Install Docker Compose v2 and obtain the deployment-specific `env` file from
the portal maintainers. Do not commit `env` or `.env` files.
The local `GoC-GdC-Root-A.crt` trust anchor is optional for clean builds; if
present in a Docker build context, images add it to the system trust store.

The default Compose configuration is deployment-oriented: application code is
baked into the images and Gunicorn does not run with autoreload. Application
containers default to UID/GID `1000:1000`, matching the current deployment
bind-mount ownership. No exports are needed on that host. If your local
account uses different IDs, override them before starting Compose. For local
development, use the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

The portal and Celery containers run as non-root. `staticfiles` and
`olc_webportalv2/media` must be writable by the configured UID/GID; `sequences`
is mounted read-only. Deployment checks these permissions and does not change
ownership of existing data. Review and migrate ownership on existing bind
mounts before deployment if they were previously written by root.
Django's rotating diagnostic log is stored at `/tmp/django_debug.log` inside
each container and is not preserved when a container is recreated.

Stop local services without removing their named data volumes:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml down
```

The Dev deployment pipeline receives its environment values from the
`FoodPort-Dev-Portal-Secrets` Azure DevOps variable group and transfers them to
the VM over SSH. The VM does not need Key Vault access for deployment. Compose
passes the Azure Storage account key only to the Storage service. Batch also
receives the non-secret account name needed for its SAS-backed Blob mounts, but
not the account key. Batch account credentials go only to Batch; web and Celery
containers receive neither provider's credentials. The internal service tokens
are domain-separated values derived from `SECRET_KEY`.

## Provider services

The Django/web container uses provider-neutral HTTP clients. The Batch service
owns job submission, progress, cancellation, and pool/node operations. The
Storage service owns remote object operations and issues short-lived transfer
URLs for web and Batch clients. Azure SDK imports and provider credentials are
isolated to the service that owns each provider operation. Compose currently
selects `STORAGE_BACKEND=azure`; a local-cluster implementation is future work.

The Batch service preserves `/submit_batch_request`'s pool/job/task response
shape and submits asynchronously. Its configured analysis types are `COWBAT`,
`AmpliSeq`, and `COWSNPhR`; input object listing/copying and mount/output SAS
generation are delegated to Storage. The mount SAS expires after seven days,
so a long-lived pool may require credential renewal. Gallery-image Trusted
Launch metadata discovery and `no_tidy=true` are not supported by this
submission path; unsupported analysis types and `no_tidy=true` return an error.

#### Research run file management

The Research Assembly page includes **Upload and Manage FASTQ** for containers
that already exist in Blob Storage. From there, authenticated users can browse
the run's files, upload additional files, remove selected files, rename the run,
and submit it for assembly. Submission requires at least one complete R1/R2
FASTQ pair; any `SampleSheet.csv` is also checked against the uploaded samples.
Container renames run asynchronously, and the page reports their status.

#### Task monitoring

Tasks run on the portal are executed via celery. You can use the nice interface provided
by [flower](https://flower.readthedocs.io/en/latest/index.html) to monitor tasks.

The Compose Redis service uses AOF persistence in the named `redis-data` volume
so queued tasks and results survive future container recreation. Existing data
in an older Redis container is not automatically migrated to this volume, so
plan the initial upgrade around pending work. Preserve the volume during routine
redeploys; `docker compose down -v` removes it.

Compose publishes Flower on host port `8888` without configuring
authentication. Restrict access to this port with the host firewall or an
authenticated reverse proxy; do not expose the dashboard publicly as-is.

#### Deployment

Portal VM deployments are managed by the Azure DevOps pipelines. The
`azure-pipelines-update.yml` pipeline updates the main portal VM, and
`azure-pipelines-dev.yml` manages the development deployment. Both use the
repository's default `docker-compose.yml`; the retired
`docker-compose-prod.yml` instructions are no longer applicable.
 
 
## Data provisioning

Sequence datasets and source metadata are provisioned separately from this
repository. The historical README instructions referenced scripts that are no
longer present; use the current deployment-specific data-ingestion procedure
provided by the portal maintainers.

## Running tests

Run the Django test suite with its PostgreSQL test database:

```bash
docker compose -p foodport-tests \
  -f docker-compose-test.yml \
  run --build --rm test-runner
```

The browser-test overlay runs the Selenium tests and checks that representative
CSS and JavaScript assets are served successfully by the project's Nginx
configuration. It collects static files into a shared test volume and verifies
the HTTP status, MIME type, and response body for each asset.

Run the browser and Nginx tests with an isolated Compose project:

```bash
docker compose -p foodport-browser-tests \
  -f docker-compose-test.yml \
  -f docker-compose-browser-test.yml \
  up --build --abort-on-container-exit --exit-code-from test-runner test-runner
```

Use `up` for this overlay so the test runner has the network alias required by
the browser-driven Django live server. After the tests finish, remove the
containers and network without deleting the test data volumes:

```bash
docker compose -p foodport-browser-tests \
  -f docker-compose-test.yml \
  -f docker-compose-browser-test.yml \
  down
```

## Continuous integration

The **Foodport-CI** Azure Pipeline (ID `1064`) runs on pushes to and pull
requests targeting `main`, `dev`, and `foodport`. The badge above shows the
latest CI status for `dev`.

CI runs the Django test suite against an isolated PostgreSQL service, checks
dependency locks and critical Flake8 errors, compiles translations, and runs
browser-driven Selenium tests plus HTTP checks for static assets. It builds
the web, Batch, Storage, Redmine, Nginx, and RAG images; starts Batch and checks
its health endpoint; verifies the portal image can run as a non-root user; and
runs the RAG test suite. These tests do not submit jobs to Azure Batch or use
production storage or databases.

GitHub rulesets protect `main`, requiring a pull request and a passing
`Foodport-CI` check before merge. CI also runs on `dev` pushes for fast
feedback; `dev` is not a protected branch. The `foodport` branch is reserved
for production-VM hotfix validation.

Python dependency inputs use `.in` files; the corresponding `.txt` files are
generated, hash-locked requirements. Regenerate them with
`bash utility/compile_python_requirements.sh` and verify them with
`bash utility/compile_python_requirements.sh --check`. Run the script with
Python 3.14 for portal/Storage locks and Python 3.12 for Batch/RAG locks; CI
checks both interpreter groups.

## Azure DevOps deploy pipeline

The direct update pipeline is `azure-pipelines-update.yml`. It updates the main
portal VM and rebuilds/recreates changed Compose services without first
stopping the entire stack.

## External Terraform repo dependency

The dedicated deploy pipelines in `olc_genomics_portal/azure-pipelines-dev.yml` and
`olc_genomics_portal/azure-pipelines-full-infra.yml` now checkout the external repository
`FoodPort/foodport-tf` from Azure DevOps.
That repo contains the Terraform code used by these pipelines under:

- `foodport-tf/dev-vm`
- `foodport-tf/full-infra`

The pipeline uses the repo alias `foodport-tf` and checks it out to
`$(Build.SourcesDirectory)/foodport-tf`.

If you are running these pipelines, the Azure DevOps project must contain the
`foodport-tf` repo and the pipeline must be allowed to fetch it.

The dedicated deploy pipelines do not require a separate repo-specific pipeline variable for checkout when
`foodport-tf` is an Azure Repos Git repository in the same organization. Pipeline access is handled by Azure DevOps
repo resource authorization and the agent's OAuth credential.

The new dedicated pipelines accept `sshPublicKey` as a pipeline variable only.
The Dev VM identity still needs `Azure Batch Data Contributor` on the Batch
account before it submits Batch jobs. It does not need `Key Vault Secrets User`
for deployment. See [the Dev VM Terraform README](../foodport-tf/dev-vm/README.md)
for scoped role commands and cleanup.

For example, via Azure DevOps CLI:

```bash
az pipelines run --name "<your-dev-pipeline-name>" --branch main \
  --variables sshPublicKey="<public-key>" destroy=false deployEnvironment=dev useHostedAgent=false bootstrapRemoteVm=false
```

After assigning the runtime roles to the principal ID in the `devVm` artifact,
rerun with `bootstrapRemoteVm=true`.

If the repo is in a different Azure DevOps project, ensure the pipeline is authorized to access the external repo from
Pipeline settings > Resources > Repositories, or via the repo resource permissions dialog.

If you later need to run git commands against the checked-out `foodport-tf` repo from script tasks, the pipeline
checkout also persists credentials for that repo so the task can use the repo access token.

## Branch and pull request workflow

`main` is protected by GitHub rulesets: changes must come through a pull
request, and the `Foodport-CI` check must pass before merge. `dev` accepts
direct pushes and runs CI for feedback. Create feature branches from `dev`:

```bash
git switch dev
git pull --ff-only origin dev
git switch -c my-feature-branch
# Make and commit changes.
git push -u origin my-feature-branch
```

Push completed changes to `dev`, then promote tested changes from `dev` to
`main` with a pull request. The `foodport` branch is reserved for
production-VM hotfix work; CI runs on it as well. After a hotfix, bring the fix
back into `dev` and `main` so it is not lost in later promotions.

## Azure DevOps pipeline usage

The portal deployment and CI pipelines are registered in the **FoodPort** Azure
DevOps project. Their IDs were checked on 2026-11-23:

- `Foodport-CI` (ID 1064) uses this repository's `azure-pipelines-ci.yml`.
- `FoodPort-dev-VM` (ID 978) uses this repository's `azure-pipelines-dev.yml`.
- `FoodPort-full-infra` (ID 979) uses this repository's `azure-pipelines-full-infra.yml`.
- `FoodPort-update-portal` (ID 968) uses `azure-pipelines-update.yml`.

The separate standalone Terraform pipelines remain registered in the
**Virtool** project and use the `foodport-tf` Azure Repos repository:
`foodport-tf` (ID 963), `foodport-tf (964)` (ID 964), and
`foodport-tf (971)` (ID 971). They are documented in the `foodport-tf`
repository's README.

### Azure DevOps service connection status

The current FoodPort service connection is
`CFDC-FoodPort-service-connection`, and it uses service-principal-key
authentication (`spnKey`). As checked on 2026-10-30, the connection
`CFDC-FoodPort-managed-identity` does not yet exist in FoodPort.
The Dev and full-infrastructure YAMLs currently default to that intended
managed-identity connection name; runs will need the connection created and
authorized first, or the parameter must be changed to another configured
connection.

For the migration, create a dedicated user-assigned managed identity and a
managed-identity/workload-identity-federation service connection in the
FoodPort project. Authorize only the required pipelines, and keep the existing
service-principal connection until the new connection has been tested. Do not
reuse `foodportbatch`, which is the Batch service identity. The Dev deployment's
provider credentials are provided by its Azure DevOps variable group and are
scoped by Compose to the Batch or Storage service; the Django/web container
uses the authenticated internal service APIs.

### Pipeline purposes

`olc_genomics_portal/azure-pipelines-dev.yml` is the end-to-end Dev VM deploy pipeline:

- provisions the Dev VM via Terraform
- publishes the VM IP and managed-identity principal ID
- bootstraps the remote VM via SSH if `bootstrapRemoteVm=true`
- deploys the portal app using downloaded artifacts and secrets

`olc_genomics_portal/azure-pipelines-full-infra.yml` is the end-to-end full infra deploy pipeline:

- provisions the portal infrastructure via Terraform
- publishes the portal VM IP artifact
- bootstraps the remote VM via SSH if `bootstrapRemoteVm=true`
- deploys the portal app using downloaded artifacts and secrets

`olc_genomics_portal/azure-pipelines-update.yml` is a separate direct app update
pipeline. It is intended for updates to an existing portal VM.

### Dev VM deployment variable group

The `FoodPort-Dev-VM` pipeline (ID 978) uses the `FoodPort-Dev-Portal-Secrets`
variable group in the **FoodPort** project. The group is initially created with
`FOODPORT_SECRET_VARIABLES_READY=false`. The 47 variables below are already
created, marked secret, and set to the placeholder `__REPLACE_ME__`; leave the
readiness flag false until all placeholders have been replaced.

In Azure DevOps, open **Pipelines > Library > FoodPort-Dev-Portal-Secrets**.
Edit each existing variable using the exact name in the first column and enter
the current value of the corresponding Key Vault secret in the second column.
Keep every value marked secret using the lock icon. If you cannot read a Key
Vault value, ask an authorized secret owner to enter it; do not put secret
values in source control, pipeline logs, or chat.

`AZURE_ACCOUNT_KEY` is passed to the Batch service for Azure Blob mounts. Treat
it as a storage-account secret; it is not passed to the web container or logged.

`VM_IMAGE` is the Azure Compute Gallery image-version resource ID used by new
Azure Batch pools, not the operating-system image for the portal VM. The
portal VM's operating system is configured separately in `foodport-tf`.
To use a newer Gallery image for future Batch pools, update `VM_IMAGE` to the
full version resource ID and rerun the Dev deployment pipeline. Existing Batch
pools are not changed by updating this value.

| Variable name | Existing Key Vault secret |
|---|---|
| `SECRET_KEY` | `secret-key` |
| `SECRET_KEY_BASE` | `secret-key-base` |
| `REDMINE_DB_PASSWORD` | `redmine-database-password` |
| `AZURE_ACCOUNT_NAME` | `azure-account-name` |
| `AZURE_ACCOUNT_KEY` | `azure-account-key` |
| `BATCH_ACCOUNT_NAME` | `batch-account-name` |
| `BATCH_ACCOUNT_URL` | `batch-account-url` |
| `BATCH_ACCOUNT_KEY` | `batch-account-key` |
| `BATCH_ACCOUNT_SUBNET` | `batch-account-subnet` |
| `VM_IMAGE` | `vm-image` |
| `COWBAT_NODE_AGENT_SKU` | `cowbat-node-agent-sku` |
| `BATCH_SECURITY_TYPE` | Optional; defaults to `trustedLaunch` for Batch pools. Set to `none` only for images that do not use Trusted Launch. |
| `BATCH_SECURE_BOOT_ENABLED` | Optional Trusted Launch UEFI setting (`true`/`false`); unset leaves the Azure default. |
| `BATCH_VTPM_ENABLED` | Optional Trusted Launch UEFI setting (`true`/`false`); unset leaves the Azure default. |
| `AMPLISEQ_IMAGE` | `ampliseq-image` |
| `AMPLISEQ_NODE_AGENT_SKU` | `ampliseq-node-agent-sku` |
| `COWSNPHR_IMAGE` | `cowsnphr-image` |
| `COWSNPHR_NODE_AGENT_SKU` | `cowsnphr-node-agent-sku` |
| `AD_APP_ID` | `ad-app-id` |
| `AD_APP_SECRET_ID` | `ad-app-secret-id` |
| `AD_APP_SECRET` | `ad-app-secret` |
| `ENTRA_ID_CLIENT_ID` | `entra-id-client-id` |
| `ENTRA_ID_CLIENT_SECRET` | `entra-id-client-secret` |
| `ENTRA_TENANT_ID` | `entra-id-tenant-id` |
| `REDMINE_DEFAULT_FROM` | `redmine-default-from` |
| `EMAIL_HOST_USER` | `email-host-user` |
| `EMAIL_HOST_PASSWORD` | `email-host-password` |
| `EMAIL_RELAY_SECRET` | `email-relay-secret` |
| `DB_NAME` | `db-name` |
| `DB_USER` | `db-user` |
| `DB_PASS` | `db-pass` |
| `DB_SERVICE` | `db-service` |
| `DB_PORT` | `db-port` |
| `DJANGO_EMAIL_BACKEND` | `django-email-backend` |
| `SENTRY_DSN` | `sentry-dsn` |
| `ENVIRONMENT` | `environment` |
| `CONNECTION_STRING` | `connection-string` |
| `SITE_URL` | `site-url` |
| `DJANGO_SERVER_NAME` | `django-server-name` |
| `REDMINE_SERVER_NAME` | `redmine-server-name` |
| `API_KEY` | `api-key` |
| `DEV_API_KEY` | `dev-api-key` |
| `MARIA_DB_ROOT_PASSWORD` | `maria-db-root-password` |
| `MYSQL_PASSWORD` | `mysql-password` |
| `SUBSCRIPTION_ID` | `subscription-id` |
| `TENANT_ID` | `tenant-id` |
| `CLIENT_ID` | `client-id` |
| `CLIENT_SECRET` | `client-secret` |
| `RESOURCE_GROUP_NAME` | `resource-group-name` |
| `STORAGE_ACCOUNT_NAME` | `storage-account-name` |

After adding all 47 values, set the existing non-secret
`FOODPORT_SECRET_VARIABLES_READY` variable to `true`. Under the variable group's
**Pipeline permissions**, authorize only `FoodPort-dev-VM`; do not enable access
for all pipelines. The pipeline stages the files with restrictive permissions,
transfers them over SSH, and removes the temporary remote copy after deployment.

### Self-hosted agent support

The deploy pipelines can run on a self-hosted Azure DevOps agent instead of a hosted Microsoft agent.
This applies to both the dedicated end-to-end deploy pipelines and the direct app update pipeline.
Use the pipeline parameters:

```bash
--parameters useHostedAgent=false selfHostedPool="<your-pool-name>"
```

The new dedicated pipelines also expect `sshPublicKey` to be provided as a pipeline variable.

This is useful when the portal VM is on a private network and a hosted Azure DevOps agent cannot SSH to it.

#### What is involved in the self-hosted agent setup

1. Create a small Azure VM in the same VNet or peered network as the portal VM.
2. Install the Azure Pipelines agent software on that VM.
3. Register the VM with an Azure DevOps agent pool, e.g. `my-self-hosted-pool`.
4. Give the VM outbound internet access so it can reach Azure DevOps, but no public inbound SSH is required for the portal VM.
5. Configure the VM to auto-shutdown when idle and start only when needed.

You can create and manage the Azure VM with `az cli`, and use the VM auto-shutdown feature or a schedule to minimize cost.

### Example self-hosted agent run

```bash
az pipelines run --name "<your-dedicated-pipeline-name>" --branch main \
  --parameters useHostedAgent=false selfHostedPool="my-self-hosted-pool"
```

Then the pipeline will use the self-hosted agent pool instead of `ubuntu-latest`.

### Azure VM cost control

- Use a small instance size (for example, B1s/B2s)
- Enable auto-shutdown in Azure Portal or with `az vm auto-shutdown`
- Start the VM only when you need to run deployments
- Stop/deallocate it when idle to avoid compute charges

### Important

The dedicated deploy pipelines still require `prod_ssh_key` and other secure files in the Azure DevOps library, as well as access to the portal VM via SSH.

### Azure CLI setup

If you have not configured Azure DevOps CLI defaults yet:

```bash
az extension add --name azure-devops
az devops configure --defaults organization=https://dev.azure.com/CFIA-DevOps-ACIA project=FoodPort
```

If you need to disable connection verification while installing the extension:

```bash
AZURE_CLI_DISABLE_CONNECTION_VERIFICATION=1 az extension add --name azure-devops
```

### Run the dedicated deploy pipelines

```bash
az pipelines run --name "<your-dev-pipeline-name>" --branch main \
  --parameters destroy=false deployEnvironment=dev useHostedAgent=false sshPublicKey="<public-key>" bootstrapRemoteVm=true
```

```bash
az pipelines run --name "<your-full-infra-pipeline-name>" --branch main \
  --parameters destroy=false deployEnvironment=dev useHostedAgent=false sshPublicKey="<public-key>" bootstrapRemoteVm=true
```

### Run the direct app update pipeline

If you register `azure-pipelines-update.yml` as a separate pipeline, run:

```bash
az pipelines run --name "<your-update-pipeline-name>" --branch main
```

This is the pipeline to use when you want to push a code update to the portal VM and rebuild the compose stack.

### Pipeline file locations

- Dev VM deploy pipeline: `olc_genomics_portal/azure-pipelines-dev.yml`
- Full infra deploy pipeline: `olc_genomics_portal/azure-pipelines-full-infra.yml`
- Direct app update pipeline: `olc_genomics_portal/azure-pipelines-update.yml`
- Legacy dispatcher pipeline: `olc_genomics_portal/azure-pipelines.yml` (deprecated)
