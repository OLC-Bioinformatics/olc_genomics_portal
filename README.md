# FoodPort Genomics Portal

[![Build Status](https://dev.azure.com/CFIA-DevOps-ACIA/FoodPort/_apis/build/status%2FFoodport-CI?branchName=dev)](https://dev.azure.com/CFIA-DevOps-ACIA/FoodPort/_build/latest?definitionId=1064&branchName=dev)

FoodPort is a web portal for searching CFIA genomic metadata and data, running
sequence-analysis workflows, and managing research assemblies. It runs with
Docker Compose and currently uses Azure for cloud storage and Batch processing.

## Documentation

- [User guide](docs/user-guide.md): portal navigation and common workflows.
- [Developer guide](docs/development.md): local setup, architecture, and branch
  workflow.
- [Testing guide](docs/testing.md): isolated database, Django, and browser tests.
- [Deployment guide](docs/deployment.md): Azure DevOps pipelines, deployment
  secrets, and operational procedures.
- [Portal operations runbook](docs/portal-operations.md): service health,
  background-job triage, and safe recovery.
- [Redmine Assistant operations guide](docs/redmine-assistant/README.md).
- [Nanopore desktop GUI guide](docs/README.md) and [Nanopore API
  contract](docs/nanopore-api.md).

## Main workflows

- Explore genomic metadata, browse/search records, and retrieve sequence data.
- Assemble data from existing Blob containers or upload and manage research
  FASTQ runs.
- Run analyses such as gene search, AMR investigation, annotation, Virus Typer,
  PrimerValidator, PrimerVerifier, 16S, and SNV location.
- Stream Nanopore POD5 files through the companion desktop application.

Which pages and datasets are available depends on account permissions and
deployment configuration. See the [user guide](docs/user-guide.md) for workflow
orientation.

## Run locally

Install Docker Compose v2 and obtain the deployment-specific `env` file from
the portal maintainers. Do not commit `env` or `.env` files. For local
development, use the Compose overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Stop the local services without removing persistent volumes:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml down
```

See the [developer guide](docs/development.md) for prerequisites, service
responsibilities, health checks, and safe local-development practices.

## Tests

Run the default Django suite with its isolated PostgreSQL test database:

```bash
docker compose -p foodport-test \
  -f docker-compose-test.yml run --build --rm test-runner
```

See the [testing guide](docs/testing.md) for database lifecycle, Selenium
browser tests, CI checks, and test limitations.

## Continuous integration and deployment

The **Foodport-CI** Azure Pipeline runs on pushes and pull requests for `main`,
`dev`, and `foodport`. It checks dependency locks, Django tests, translations,
critical Flake8 errors, browser/static assets, and deployment images. Tests use
isolated services and do not submit jobs to production Azure resources.

`azure-pipelines-update.yml` updates the production VM from `origin/main`.
`azure-pipelines-dev.yml` and `azure-pipelines-full-infra.yml` provision and
deploy development infrastructure. The production update pipeline uses the
VM's existing `env` file; it does not provision secrets from the Dev variable
group. See the [deployment guide](docs/deployment.md) for details.

## Data provisioning

Sequence datasets and source metadata are provisioned separately from this
repository. Contact the portal maintainers for the procedure applicable to
your deployment.
