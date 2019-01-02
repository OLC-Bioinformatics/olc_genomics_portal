OLC Webportal V2
================

A web portal for accessing CFIA genomic data, with some tools included.

The portal itself runs via docker-compose, and submits jobs to Azure Batch Service.

## Installing

You will need to: 

- clone this repository
- have docker-compose installed and working on your system
- have an azure storage and azure batch account

## Setup

#### Azure Setup
- create a custom VM image with tools you want to be able to run through the portal installed, and have it registered
as an app through the azure portal that has access to batch service. This image must be in the same subscription as
your Azure Batch account
- The VM should have a `/databases` folder with any resources you need to run assembly pipeline/other things,
and a `/envs` folder where conda environments are stored - see the `tasks.py` of various apps to see the commands that
the VM actually runs
- create a container called `raw-data` in your Azure storage account and store .fastq.gz files there - it's assumed that
they're MiSeq files that start with SEQIDs
- create a container called `processed-data` in your Azure storage account and put your illumina assemblies there. It's 
assumed that they're named in the format seqid.fasta

#### Portal Machine Setup
A copy of all your processed data needs to be on your portal machine - change the ./sequences volume mount
in `docker-compose.yml` as necessary, and put all the FASTA files there in addition to the `processed-data` blob container.

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
```

#### Booting up the portal

Add your IP address to ALLOWED_HOSTS in `prod.yml`, and make a directory called
`postgres-data` in the root of your cloned dir. You should now be able to boot up the portal. You'll need the following commands (in this order, run in the root
of the directory you cloned):

- `docker-compose build`
- `docker-compose up`

At this point the portal should be up and fully functional, and will restart automatically if it fails for any reason.

