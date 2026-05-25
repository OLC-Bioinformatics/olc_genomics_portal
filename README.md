[![Build status](https://travis-ci.org/OLC-Bioinformatics/olc_genomics_portal.svg?master)](https://travis-ci.org/olc-bioinformatics)

OLC Webportal V2
================

A web portal for accessing CFIA genomic data, with some tools included.

The portal itself runs via docker-compose, and submits jobs to Azure Batch Service.

## Installing

You will need to: 

- clone this repository
- have docker-compose installed and working on your system
- have an azure storage and azure batch account
- have a sentry.io account and django dsn set up (see https://docs.sentry.io/platforms/python/django/ - this lets 
you get emails/notifications whenever things go wrong!)

## Setup

#### Azure Setup
- create a custom VM image with tools you want to be able to run through the portal installed, and have it registered
as an app through the azure portal that has access to batch service. This image must be in the same subscription as
your Azure Batch account
- The VM should have a `/databases` folder with any resources you need to run assembly pipeline/other things,
and a `/envs` folder where conda environments are stored - see the `tasks.py` of various apps to see the commands that
the VM actually runs
- create a container called `raw-data` in your Azure storage account and store .fastq.gz files there - it's assumed that
they're MiSeq files that start with SEQIDs **NOTE: No tools in the portal currently use raw data. This is a future TODO**
- create a container called `processed-data` in your Azure storage account and put your illumina assemblies there. It's 
assumed that they're named in the format seqid.fasta


#### Making Your Env File

Create a file called `env` that has the following variables that the portal will use:

```
DB_NAME=yourpostgresdbname
DB_USER=yourpostgresdbuser
DB_PASS=yourdbpassword
DB_SERVICE=postgres
DB_PORT=5432
SECRET_KEY=yourdjangosecretkey
AZURE_ACCOUNT_NAME=yourazurestorageaccount
AZURE_ACCOUNT_KEY=yourazurestoragekey
BATCH_ACCOUNT_NAME=azurebatchaccountname
BATCH_ACCOUNT_URL=https://azurebatchaccountname.canadacentral.batch.azure.com
BATCH_ACCOUNT_KEY=batchaccountkey
EMAIL_HOST_USER=youremail@gmail.com
EMAIL_HOST_PASSWORD=emailpassword
VM_IMAGE=/subscriptions/subscription_id/resourceGroups/subscription/providers/Microsoft.Compute/images/image_name
VM_CLIENT_ID=vm_client_id
VM_SECRET=vm_secret_key
VM_TENANT=vm_tenant_id
SENTRY_DSN=your_sentry_dsn
```

#### Task monitoring

Tasks run on the portal are executed via celery. You can use the nice interface provided
by [flower](https://flower.readthedocs.io/en/latest/index.html) to monitor tasks.

Flower is set up to use some basic authentication - you can either a username/password in your `docker-compose.yml` by 
changing `$FLOWER_USER` and `$FLOWER_PASSWORD`, or you can just set the environmental variables 
before booting the portal.

```
export FLOWER_USER=a_flower_username
export FLOWER_PASSWORD=a_flower_password
```

#### Booting up the portal

Add your IP address to ALLOWED_HOSTS in `prod.py`, and make a directory called
`postgres-data` in the root of your cloned dir. You should now be able to boot up the portal. You'll need the following commands (in this order, run in the root
of the directory you cloned):

- `docker-compose -f docker-compose-prod.yml build`
- `docker-compose -f docker-compose-prod.yml up`

You'll then need to get database structure set up - attach into the running web container (command will be something like
 `docker exec -it olc_genomics_portal_web_1 /bin/bash`) and run `python3 manage.py migrate` (you shouldn't need to make migrations,
 these get pushed to the repository.) At this point, the portal will be up and running, but there won't be any metadata in there,
 and so it's still pretty useless. Read on to see how to get metadata into the portal.
 
 
#### Adding in metadata/sequence data so the portal isn't useless

In the root of this repository there are 2 scripts used to get sequence data working - it's assumed that you're at OLC 
and on the local network there, or they won't work at all. Here's what they do and how to use them.

First, make a container in your Azure Storage account called `databases` - these scripts assume that it already exists.

The `make_mash_sketch.py` script will create a mash sketch of all OLC's sequence data that the near neighbors tool needs 
and upload the sketch to blob storage.
To run it, just run `python make_mash_sketch.py` in a virtualenv with Azure Storage installed and provide the Azure account 
name and key when prompted (you'll also need mash v2.1 installed and on your path). Once the script is done,
you should be able to see a file called `sketchomatic.msh` in the `databases` blob storage container.

The `make_mega_fasta.py` script will combine all of OLC's sequence files into one, make a BLAST database from it, and 
upload the BLAST database to blob storage. You'll need to have BLAST installed on your machine to make this work, and have
biopython/azure-storage available in your python environment. 

Once both of those scripts have run, go to the machine the portal is running on and run `download_databases.py` from the root of
this repository, providing azure account credentials when asked. This will download the files created by the
previous two scripts from blob storage into the correct locations on your machine.

Now sequence data is present, but no metadata is associated with the sequences. The metadata in the portal
comes from OLC's access database. You'll need to export the SeqTracking and SeqMetadata queries from that database as CSV files,
and then get them onto the machine the portal is running on. From there, attach into the web container as 
previously described, and run `python3 manage.py upload_metadata SeqTracking.csv SeqMetadata.csv`. This should get all relevant metadata
from those file into the portal's database. At this point, the portal should now be fully funcitonal! Woohoo!


#### Running tests

This happens automatically via Travis-CI - see `.travis.yml` for the commands used to make this work if you want to run 
tests locally.

Often, Travis decides that the tests have failed even though they haven't actually (it'll show all tests pass, but
build exits with a non-zero code). As far as I can tell this is completely random, so you actually have to 
go into the Travis web UI to see if tests are passing or not.

## Azure DevOps deploy pipeline

A separate deployment pipeline is available at `olc_genomics_portal/azure-pipelines.update.yml`.
This pipeline is designed to run on pushes to `main`, SSH to the portal VM at `10.148.57.4`, pull the latest
repo state, and restart the compose stack with `docker compose up -d --build`.

## Branch-based update workflow

Because `main` is protected, use a branch-based workflow for repository changes:

1. Create a new branch from `main`:

```bash
git checkout -b my-feature-branch
```

2. Make your changes and commit them:

```bash
git add .
git commit -m "Describe the change"
```

3. Push the branch upstream:

```bash
git push -u origin my-feature-branch
```

4. Open a pull request from `my-feature-branch` into `main`.

5. Review, merge, and delete the branch when the PR is accepted.

6. Update your local `main`:

```bash
git checkout main
git pull origin main
```

This workflow ensures you can continue making changes without pushing directly to protected `main`.

### If you forgot to create the branch first

If you started working on `main` by mistake, you can still move your work to a branch.

#### Case A: you have uncommitted changes

```bash
git checkout -b my-feature-branch
git add .
git commit -m "Describe the change"
git push -u origin my-feature-branch
```

Then open the PR from `my-feature-branch` into `main`.

This creates a branch from your current `main` state and preserves your uncommitted edits.

#### Case B: you already committed on `main`

If your commits are only on your local `main`, create the branch from the current state and push it:

```bash
git checkout -b my-feature-branch
git push -u origin my-feature-branch
```

Then open the PR from `my-feature-branch` into `main`.

Once your work is safely on the branch and pushed, switch back to `main` and reset it to match remote:

```bash
git checkout main
git fetch origin
git reset --hard origin/main
```

Use `git reset --hard` only after you have pushed or otherwise saved your work, because it discards local commits and changes.

If you need to preserve any local work before cleaning `main`, use `git stash` first:

```bash
git stash push -m "work in progress"
git checkout -b my-feature-branch
git stash pop
```

Then commit and push from the new branch.

## Azure DevOps pipeline usage

This repository uses three pipeline scenarios:

- `foodport-tf` — full infrastructure pipeline
- `foodport-tf (964)` — dev VM-only pipeline
- `olc_genomics_portal` — app deployment pipeline

The app pipeline in `olc_genomics_portal/azure-pipelines.yml` supports these options:

- `infrastructure=dev-vm` + `bootstrapRemoteVm=true`
  - consumes `dev-vm-trigger` from `foodport-tf (964)`
  - bootstraps the dev VM and deploys the portal app
- `infrastructure=full-infra` + `bootstrapRemoteVm=true`
  - consumes `portal-trigger` from `foodport-tf`
  - bootstraps the portal VM created by the full infra pipeline and deploys the app
- `infrastructure=none` + `bootstrapRemoteVm=false`
  - app deployment only; does not download a VM IP or bootstrap a remote host

Example manual run commands:

```bash
az pipelines run --name "OLC-Bioinformatics.olc_genomics_portal" --branch main --parameters infrastructure=dev-vm bootstrapRemoteVm=true
```

```bash
az pipelines run --name "OLC-Bioinformatics.olc_genomics_portal" --branch main --parameters infrastructure=full-infra bootstrapRemoteVm=true
```

```bash
az pipelines run --name "OLC-Bioinformatics.olc_genomics_portal" --branch main --parameters infrastructure=none bootstrapRemoteVm=false
```

The dev-vm pipeline YAML is in `foodport-tf/dev-vm/azure-pipelines-dev-vm.yml` and the full infra pipeline YAML is in `foodport-tf/full-infra/azure-pipelines.infra.yml`.

