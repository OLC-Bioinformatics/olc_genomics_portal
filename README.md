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

Add your IP address to ALLOWED_HOSTS in `prod.yml`, and make a directory called
`postgres-data` in the root of your cloned dir. You should now be able to boot up the portal. You'll need the following commands (in this order, run in the root
of the directory you cloned):

- `docker-compose -f docker-compose-prod.yml build`
- `docker-compose -f docker-compose-prod.yml up`

At this point the portal should be up and fully functional, and will restart automatically if it fails for any reason.


#### Running tests

To run unit tests, run the following command:

`docker-compose run web python3 manage.py test`

If anything fails, you definitely don't want to deploy.

To make tests that run via selenium work:

Download gecko driver to root of repository:

`wget https://github.com/mozilla/geckodriver/releases/download/v0.24.0/geckodriver-v0.24.0-linux64.tar.gz && tar xf geckodriver-v0.24.0-linux64.tar.gz`

Actually run tests:

`docker-compose run web /bin/bash -c "(Xvfb :99 &) && export DISPLAY=:99 && python3 manage.py test"`

### Troubleshooting
Once container is running, open a new terminal window and type
-`docker ps`. This will give you a list of all the containers.
Type
-`docker exec -it TAB` the tab will populate with olc. From there, add a 'w' and TAB again to complete the path. Add `/bin/bash` to the end of the line. It should look something like this.
-`docker exec -it olc_genomics_portal_web_1_e276278d1742 /bin/bash`

This attaches into the container. 
-`python3 manage.py migrate` will migrate changes to the models.
-`python3 manage.py createsuperuser` to create user for portal

Open new terminal in root portal folder,
-` cp /mnt/nas2/users/(youruser)/SeqTracking.csv .` then run to populate metadata
-`python make_metadata_csv.py`

Return to the docker container terminal window and input
-`python3 manage.py upload_metadata Metadata_csv.csv` this populates the database with sequences.

If "encoding error", use nano or vim to edit make_metadata_csv.py 
-`nano make_metadata_csv.py` or
-`vi make_metadata_csv.py`
and remove `, encoding='ISO-8859-1'` and save

For sequences, copy folder into root directory of app
-`cp -r /mnt/nas2/users/(youruser)/sequences .`
