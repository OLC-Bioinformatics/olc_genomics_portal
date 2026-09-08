# FoodPort Nanopore Azure Image Build with Packer

## Purpose

This document describes the FoodPort Nanopore Azure VM image build,
publication, validation, and acceptance workflow. HashiCorp Packer builds the
image and publishes it to the existing Azure Compute Gallery image definition:

```text
development/nanopore
```

The most recently accepted development image is:

```text
0.0.3
```

Its resource ID is:

```text
/subscriptions/dcdc7934-5cce-43de-a6ed-22e2e163c2e1/resourceGroups/CFDC-FoodPort-Batch-rg/providers/Microsoft.Compute/galleries/development/images/nanopore/versions/0.0.3
```

Image `0.0.4` is currently under development. It must not be configured as the
FoodPort production or accepted development image until its build, publication,
and GPU acceptance workflow succeed.

Image history:

```text
0.0.1  Private-network connectivity proof
0.0.2  NVIDIA GRID driver foundation
0.0.3  Dorado runtime and pinned basecalling model
0.0.4  PoreSippR runtime, pinned source, and incremental processing scheduler
0.1.0  Planned first complete development image
1.0.0  Planned first production-ready image
```

---

## Architecture and Responsibilities

```text
Azure CLI and existing Azure configuration
    Persistent infrastructure and one-time setup

Packer
    Temporary build VM, provisioning, validation, and immutable image versions

AzureBatch and FoodPort
    Batch pools, jobs, tasks, mounts, data movement, and result publication

PoreSippR-GUI repository
    Commit-pinned analysis source and incremental Dorado scheduler
```

Packer creates a temporary CPU VM, provisions and validates it, deprovisions
it, publishes an immutable gallery version, and removes temporary resources.
Terraform is not required for this workflow because the persistent Azure
resources already exist.

---

## Existing Azure Resources

### Subscription

```text
Name: CFDC-FoodPort-Sub
Subscription ID: dcdc7934-5cce-43de-a6ed-22e2e163c2e1
Tenant ID: 18b5a5ed-1d86-41d3-94a0-bc27dae32ab2
```

### Build and gallery resource group

```text
CFDC-FoodPort-Batch-rg
Location: canadacentral
```

### Private network

```text
Resource group: CFDC-FoodPort-network-rg
Virtual network: CFDC-FoodPort-vnet
Subnet: CFDC-FoodPort-BatchNodes-snet
Address prefix: 10.148.57.32/27
```

The temporary Packer VM receives no public IP. The host running Packer must
have private TCP/22 connectivity to this subnet.

### Azure Compute Gallery

```text
Resource group: CFDC-FoodPort-Batch-rg
Gallery: development
Image definition: nanopore
Operating system: Linux
Operating system state: Generalized
Hyper-V generation: V2
Security feature: TrustedLaunchSupported
Location: canadacentral
```

Gallery versions are immutable. Never attempt to rebuild an existing version
number.

---

## Repository Layout

The current image tree includes the Packer configuration, provisioning scripts,
PoreSippR environment specification, acceptance tooling, and retained acceptance
records:

```text
infrastructure/
└── nanopore-image/
    ├── NANOPORE_PACKER_IMAGE_BUILD.md
    ├── acceptance/
    │   ├── create-input-sas.sh
    │   ├── download-acceptance-input.sh
    │   ├── launch-gpu-acceptance.sh
    │   └── run-gpu-acceptance.sh
    ├── docs/
    │   └── acceptance-results/
    │       └── 0.0.3/
    │           ├── acceptance-result.json
    │           ├── acceptance-result.md
    │           ├── barcode-counts.tsv
    │           └── summary.tsv
    ├── files/
    │   ├── foodport-image.json
    │   └── poresippr-environment.yml
    ├── packer/
    │   ├── nanopore.pkr.hcl
    │   └── development.pkrvars.hcl
    └── scripts/
        ├── build-image.sh
        ├── provision-base.sh
        ├── install-nvidia-grid.sh
        ├── install-dorado.sh
        ├── install-micromamba.sh
        ├── install-poresippr-environment.sh
        ├── install-poresippr-targets.sh
        ├── install-poresippr-repository.sh
        ├── validate-image.sh
        └── deprovision.sh
```

Packer logs must not be committed:

```gitignore
infrastructure/nanopore-image/packer/*.log
```

Do not commit SAS URLs, storage keys, client secrets, downloaded installers,
extracted application archives, local plugin caches, or transient acceptance
input.

---

## Packer and Azure Plugin

The build host uses Packer from HashiCorp's signed APT repository. The original
installation produced:

```text
Packer v1.16.0
/usr/bin/packer
```

The template declares:

```hcl
packer {
  required_version = ">= 1.10.0"

  required_plugins {
    azure = {
      source  = "github.com/hashicorp/azure"
      version = "~> 2.6"
    }
  }
}
```

Initialize the plugin using:

```bash
packer init \
  infrastructure/nanopore-image/packer/nanopore.pkr.hcl
```

The previously validated Azure plugin was `v2.6.3`.

---

## Packer Template

### Authentication

The template uses the active Azure CLI session:

```hcl
use_azure_cli_auth = true
```

Select and verify the subscription before building:

```bash
az account set \
  --subscription dcdc7934-5cce-43de-a6ed-22e2e163c2e1

az account show \
  --query \
    '{subscription:id,name:name,tenant:tenantId,principal:user.name,type:user.type}' \
  --output yaml
```

### Ubuntu source

The current Packer template uses:

```hcl
os_type         = "Linux"
image_publisher = "Canonical"
image_offer     = "ubuntu-24_04-lts"
image_sku       = "server"
image_version   = "latest"
```

Because the NVIDIA GRID installer builds a DKMS module for the build kernel,
using `latest` means the source kernel can change between builds. The final
image validator confirms that the installed NVIDIA module version is correct
and that its `vermagic` matches the running Packer build kernel.

For stricter rebuild reproducibility, pin the Marketplace image version before
creating a release candidate. If the source remains `latest`, retain the
captured kernel, source image information, and generated package manifests as
part of the build evidence.

### CPU build VM

```hcl
build_vm_size = "Standard_D4s_v5"
```

The Packer VM remains CPU-only. It installs and validates driver files, kernel
module metadata, Dorado, the basecalling model, Micromamba, the PoreSippR
environment, the checksum-pinned target database, and the commit-pinned
PoreSippR source. Actual GPU communication is tested later on
`Standard_NV18ads_A10_v5`.

### Private networking

```hcl
virtual_network_resource_group_name    = var.network_resource_group
virtual_network_name                   = var.virtual_network_name
virtual_network_subnet_name            = var.subnet_name
private_virtual_network_with_public_ip = false
```

### Gallery destination

```hcl
shared_image_gallery_destination {
  subscription         = var.subscription_id
  resource_group       = var.build_resource_group
  gallery_name         = var.gallery_name
  image_name           = var.image_name
  image_version        = var.image_version
  replication_regions  = [var.location]
  storage_account_type = "Standard_LRS"
}
```

---

## Development Variables for `0.0.4`

The current development variable file is equivalent to:

```hcl
subscription_id = "dcdc7934-5cce-43de-a6ed-22e2e163c2e1"
location        = "Canada Central"
image_version   = "0.0.4"

build_resource_group = "CFDC-FoodPort-Batch-rg"

network_resource_group = "CFDC-FoodPort-network-rg"
virtual_network_name   = "CFDC-FoodPort-vnet"
subnet_name            = "CFDC-FoodPort-BatchNodes-snet"

gallery_name = "development"
image_name   = "nanopore"

build_vm_size = "Standard_D4s_v5"

poresippr_repository_url = \
  "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git"

poresippr_repository_commit = \
  "691b3a3c2944139cb0093f81909331f7b8d46983"
```

The commit must be the full lowercase 40-character SHA. The Packer template
validates that format before beginning the build.

The template also requires two runtime values for the static PoreSippR target
FASTA:

```text
poresippr_targets_url
poresippr_targets_sha256
```

`build-image.sh` generates a fresh, four-hour, HTTPS-only, read-only SAS scoped
to the single target blob and exports it through
`PKR_VAR_poresippr_targets_url`. The non-secret target checksum is pinned in
the wrapper and exported through `PKR_VAR_poresippr_targets_sha256`:

```text
Target blob: poresippr-data/PoreSippR_DB_251110.fasta
Storage account: carlingst01
Storage resource group: CFDC-FoodPort-Batch-rg
SHA-256: 6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9
FASTA records: 6663
```

The wrapper uses the same existing storage-account-key mechanism as the POD5
acceptance tooling, but the generated target SAS is narrower: it is scoped to
one blob and grants only read permission. The storage account key is held only
in a local shell variable long enough to sign the SAS. It is never exported to
Packer, sent to the temporary VM, written to metadata, or printed.

Do not store secrets in `.pkrvars.hcl` files. In particular, never commit SAS
URLs, SAS tokens, or storage account keys.

---

## Pinned PoreSippR Source

Image `0.0.4` does not install the latest branch tip. It installs this exact
PoreSippR-GUI commit:

```text
Repository: https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git
Branch used to prepare commit: madhubioinfo-dorado-patch1
Commit: 691b3a3c2944139cb0093f81909331f7b8d46983
```

The branch name is informational. The full commit SHA is authoritative.
Branches and movable tags can change over time, while the commit identifies the
exact source included in the image.

The pinned commit contains:

```text
poresippr_incremental_dorado_scheduler.py
tests/test_poresippr_incremental_dorado_scheduler.py
```

The scheduler test suite currently contains 41 tests. The suite passed locally
under Python 3.12 and is also executed during final Packer image validation
using the Python interpreter installed inside the image's PoreSippR
environment.

The image installs the repository at:

```text
/opt/foodport/poresippr
```

The installed scheduler and test paths are:

```text
/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py
/opt/foodport/poresippr/tests/test_poresippr_incremental_dorado_scheduler.py
```

The installer fetches the exact SHA in a temporary Git repository, checks out
`FETCH_HEAD` in detached mode, verifies `HEAD`, copies the source without
`.git`, validates the scheduler, records a scheduler checksum, and removes the
temporary clone.

The installed image therefore contains source provenance without retaining the
Git object database.

---

## Image Metadata

`files/foodport-image.json` is copied to:

```text
/etc/foodport/image.json
```

For `0.0.4`, the manifest identifies:

- image version `0.0.4`;
- build stage `poresippr-runtime`;
- Trusted Launch with Secure Boot and vTPM disabled;
- NVIDIA GRID `570.237` for `NVadsA10_v5`;
- Dorado `2.1.2`;
- the pinned fast model;
- Micromamba `2.9.0`, its root-prefix configuration path, and the scoped
  `ssl_verify: false` setting required by the intercepted build network;
- the PoreSippR environment name, path, and runtime `PATH`;
- the pinned PoreSippR repository URL, commit, installation path, scheduler,
  and scheduler-test path; and
- the static PoreSippR target FASTA name, installed path, and generated
  provenance-manifest path.

The repository section is equivalent to:

```json
{
  "poresippr_repository": {
    "repository": "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git",
    "commit": "691b3a3c2944139cb0093f81909331f7b8d46983",
    "source_branch": "madhubioinfo-dorado-patch1",
    "install_path": "/opt/foodport/poresippr",
    "scheduler": "/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py",
    "scheduler_test": "/opt/foodport/poresippr/tests/test_poresippr_incremental_dorado_scheduler.py"
  },
  "poresippr_targets": {
    "name": "PoreSippR_DB_251110.fasta",
    "path": "/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta",
    "manifest": "/etc/foodport/poresippr-targets.json"
  }
}
```

Additional generated metadata files include:

```text
/etc/foodport/nvidia-driver.json
/etc/foodport/dorado.json
/etc/foodport/dna_r10.4.1_e8.2_400bps_fast@v5.2.0.sha256
/etc/foodport/micromamba.json
/etc/foodport/poresippr-runtime.json
/etc/foodport/poresippr-targets.json
/etc/foodport/poresippr-repository.json
```

Retained reproducibility manifests include:

```text
/opt/ont/manifests/poresippr-environment.yml
/opt/ont/manifests/poresippr-conda-list.txt
/opt/ont/manifests/poresippr-conda-explicit.txt
```

---

## Provisioner Order for `0.0.4`

The current order is:

```text
1. Copy foodport-image.json
2. Copy poresippr-environment.yml
3. provision-base.sh
4. install-nvidia-grid.sh
5. install-dorado.sh
6. install-micromamba.sh
7. install-poresippr-environment.sh
8. install-poresippr-targets.sh
9. install-poresippr-repository.sh
10. validate-image.sh
11. deprovision.sh
```

`deprovision.sh` must always remain last. No validation or installation
provisioner may follow it.

---

## Base Layer

`provision-base.sh` installs and validates the base packages required by later
layers. These include tools such as:

```text
curl
git
jq
rsync
waagent
```

`rsync` is required by `install-poresippr-repository.sh` to copy the pinned
source tree while excluding Git metadata and Python bytecode.

---

## NVIDIA GRID Layer

`install-nvidia-grid.sh` installs:

```text
NVIDIA GRID/vGPU release: 18.8
Guest driver: 570.237
Target VM family: NVadsA10_v5
```

The script:

- installs build tools, DKMS, matching kernel headers, and PCI utilities;
- downloads the Azure GRID installer;
- disables Nouveau;
- updates initramfs;
- installs the driver with DKMS;
- records `/etc/foodport/nvidia-driver.json`; and
- removes the installer.

The previously observed installer SHA-256 was:

```text
1a529dd4d173ba3b36f3c284bb54fce5dd39c9e757856980b8ae0b7798e7764b
```

The CPU Packer VM produces this expected warning:

```text
You do not appear to have an NVIDIA GPU supported by the driver installed in this system.
```

Do not treat the warning as a failure when the installer exits zero. Packer
validation checks:

```text
nvidia-smi executable exists
module version is 570.237
module file exists
module vermagic matches the build kernel
Nouveau is disabled
```

Do not execute `nvidia-smi` as a required Packer device check. Actual device
communication belongs in GPU acceptance.

---

## Dorado Layer

`install-dorado.sh` installs:

```text
Dorado: 2.1.2+8b8fc5d
Platform: linux-x64
Versioned install path: /opt/ont/dorado/2.1.2
Stable bin directory: /opt/ont/dorado/bin
Stable command: /usr/local/bin/dorado
Model: dna_r10.4.1_e8.2_400bps_fast@v5.2.0
Model path: /opt/ont/models/dna_r10.4.1_e8.2_400bps_fast@v5.2.0
```

The archive URL is:

```text
https://cdn.oxfordnanoportal.com/software/analysis/dorado-2.1.2-linux-x64.tar.gz
```

The required archive SHA-256 is:

```text
f4ed83acfb75cf07ffe8a0fc78e26828fc911fcfc8177920be6104e1d0e02485
```

The archive is approximately 3.3 GB.

### TLS workaround

The Oxford Nanopore CDN certificate chain cannot be validated in the build
environment. The archive is downloaded with a scoped insecure TLS option,
followed by mandatory SHA-256 verification.

Dorado's model downloader has the same issue. The installer temporarily places
a wrapper named `curl` earlier in `PATH` for only the `dorado download`
invocation:

```bash
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/curl --insecure "$@"
```

The wrapper must not replace `/usr/bin/curl` and must not remain in the captured
image.

### Model integrity

After download, the installer:

- verifies that the model directory contains files;
- normalizes ownership to `root:root`;
- applies `0755` to model directories;
- applies `0644` to model files; and
- generates the model checksum manifest under `/etc/foodport`.

`validate-image.sh` runs `sha256sum --check` against the retained manifest.

---

## Micromamba Layer

`install-micromamba.sh` installs:

```text
Micromamba version: 2.9.0
Versioned binary: /opt/micromamba/bin/micromamba
Stable command: /usr/local/bin/micromamba
Root prefix: /opt/micromamba/root
```

The installer records:

```text
/etc/foodport/micromamba.json
```

The final validator confirms the metadata, executable, symbolic link target,
reported version, root-prefix directory, and root-prefix configuration file.

### TLS workaround

The GitHub certificate chain cannot be validated from the intercepted build
network. The checksum-pinned Micromamba binary is therefore downloaded with
`curl --insecure`, followed immediately by mandatory verification against this
pinned SHA-256:

```text
366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3
```

Micromamba package retrieval is configured through:

```text
/opt/micromamba/root/.mambarc
```

with:

```yaml
ssl_verify: false
```

The same setting is passed explicitly to `micromamba create`. The image records
this exception in `/etc/foodport/micromamba.json`, and `validate-image.sh`
verifies both the metadata and the actual configuration file. Installing the
organizational CA into the operating-system trust store remains the preferred
long-term replacement for this workaround.

---

## PoreSippR Environment Layer

`install-poresippr-environment.sh` creates:

```text
Environment name: poresippr
Environment path: /opt/micromamba/root/envs/poresippr
Environment specification: /opt/ont/manifests/poresippr-environment.yml
```

The environment provides the analysis runtime, including:

```text
python
minimap2
samtools
pod5
pandas
pysam
requests
PyYAML
pytest
```

The installer retains:

```text
/opt/ont/manifests/poresippr-conda-list.txt
/opt/ont/manifests/poresippr-conda-explicit.txt
```

It also records:

```text
/etc/foodport/poresippr-runtime.json
```

The configured runtime `PATH` is:

```text
/opt/micromamba/root/envs/poresippr/bin:/opt/micromamba/bin:/opt/ont/dorado/bin:/usr/local/bin:/usr/bin:/bin
```

This ordering ensures that `python`, `minimap2`, `samtools`, and `pod5` resolve
from the PoreSippR environment, while `micromamba` and the stable Dorado command
remain available.

---

## PoreSippR Target Database Layer

`install-poresippr-targets.sh` installs the versioned static mapping target:

```text
Blob: poresippr-data/PoreSippR_DB_251110.fasta
Installed path: /opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta
Generated metadata: /etc/foodport/poresippr-targets.json
SHA-256: 6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9
FASTA records: 6663
```

The target database is a static, versioned analysis dependency and is captured
inside the immutable image. This differs from POD5 input, which remains
run-specific and must continue to arrive through Batch mounts or acceptance
downloads.

On every build, `build-image.sh`:

1. verifies the active Azure CLI session and expected subscription;
2. retrieves the `carlingst01` storage account key locally;
3. verifies that the target blob exists;
4. creates a four-hour SAS scoped only to the target blob;
5. grants only read permission and requires HTTPS;
6. exports only the restricted SAS URL and pinned checksum to Packer; and
7. clears the storage key, raw SAS token, SAS URL, and Packer variables on exit.

The installer downloads the target with a scoped `curl --insecure` operation,
verifies the pinned checksum before installation, confirms FASTA structure,
counts records, installs the file as `root:root` mode `0644`, and writes a
query-free source URL to the generated provenance manifest. The SAS query is
never retained.

---

## PoreSippR Repository Layer

`install-poresippr-repository.sh` requires these Packer-provided environment
variables:

```text
PORESIPPR_REPOSITORY_URL
PORESIPPR_REPOSITORY_COMMIT
```

The installer:

1. validates the full commit-SHA format;
2. confirms required installation commands are available;
3. creates a temporary Git repository;
4. fetches only the pinned commit with depth one;
5. checks out `FETCH_HEAD` in detached mode;
6. confirms that `HEAD` equals the configured SHA;
7. confirms that the incremental scheduler and its test suite exist;
8. installs the repository under `/opt/foodport/poresippr`;
9. excludes `.git`, `__pycache__`, and `.pyc` files;
10. confirms that both the scheduler and test file exist after installation;
11. normalizes ownership to `root:root`;
12. validates scheduler syntax without writing bytecode;
13. executes scheduler `--help` with bytecode disabled;
14. calculates the installed scheduler SHA-256; and
15. writes `/etc/foodport/poresippr-repository.json`.

The generated repository manifest contains:

```json
{
  "repository": "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git",
  "commit": "691b3a3c2944139cb0093f81909331f7b8d46983",
  "source_branch": "madhubioinfo-dorado-patch1",
  "install_path": "/opt/foodport/poresippr",
  "scheduler": "/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py",
  "scheduler_test": "/opt/foodport/poresippr/tests/test_poresippr_incremental_dorado_scheduler.py",
  "scheduler_sha256": "generated-at-build-time"
}
```

---

## Incremental Dorado Scheduler

The installed entry point is:

```text
/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py
```

It replaces the original fixed-interval reprocessing approach with an
incremental, restartable workflow. Its major behavior includes:

- recursive discovery of POD5 files;
- file stability checks based on age and unchanged observations;
- a durable processed-file ledger;
- bounded POD5 batches;
- CUDA-only Dorado basecalling;
- Dorado demultiplexing;
- durable per-barcode FASTQ fragments;
- cumulative minimap2 and samtools mapping;
- iteration result CSV files;
- atomic state and status JSON updates;
- retry cleanup for incomplete batches;
- completion-marker handling;
- idle and walltime limits;
- `SIGINT` and `SIGTERM` handling; and
- no CPU basecalling fallback.

Required run CSV columns are:

```text
reference,pod5_dir,output_dir,barcode,barcode_values
```

An optional `run_id` column is supported.

Required metadata CSV columns are:

```text
Barcode,SEQID,OLNID
```

The scheduler can be invoked with:

```bash
python \
  /opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py \
  input.csv \
  metadata.csv
```

The Azure Batch task command still needs to be updated and tested against this
installed path before image `0.0.4` is accepted.

---

## Image Validation for `0.0.4`

`validate-image.sh` verifies all of the following.

### Base image

- image type, environment, version, and build stage;
- `python3`, `curl`, `jq`, and `waagent` availability;
- base component versions.

### NVIDIA

- GRID family and release metadata;
- driver version `570.237`;
- target VM family `NVadsA10_v5`;
- Nouveau blacklist and modesetting configuration;
- `nvidia-smi` executable presence without GPU communication;
- NVIDIA kernel module version, file, and `vermagic`;
- installed NVIDIA package inventory.

### Dorado

- Dorado metadata and version;
- versioned executable target;
- stable `/opt/ont/dorado/bin` symbolic link;
- pinned model directory;
- model checksum manifest;
- complete model checksum verification;
- a nonempty model tree.

### Micromamba and PoreSippR environment

- Micromamba metadata, binary, link, and version;
- PoreSippR environment metadata;
- retained environment specification checksum;
- environment and bin directories;
- required command executability;
- exact runtime `PATH` from image metadata;
- exact command resolution;
- component versions;
- required Python imports, including pytest;
- package and explicit manifests;
- required packages in the retained package manifest;
- Micromamba TLS-exception metadata; and
- the actual `ssl_verify: false` root-prefix configuration.

### PoreSippR repository

- repository manifest existence;
- exact URL and commit;
- install directory and scheduler path;
- scheduler SHA-256 metadata;
- absence of installed `.git` metadata;
- executable scheduler mode;
- absence of HTML-escaped Python source;
- scheduler checksum match;
- non-mutating scheduler syntax compilation;
- scheduler `--help` execution with bytecode disabled;
- execution of all 41 installed scheduler tests with bytecode and pytest cache
  generation disabled; and
- agreement with the repository section in image metadata.

### PoreSippR targets

- installed target FASTA existence and nonzero size;
- exact SHA-256 agreement with generated target metadata;
- positive byte and sequence counts;
- FASTA header syntax;
- actual versus recorded sequence count;
- query-free provenance metadata; and
- agreement with the target section in image metadata.

The expected final message is:

```text
FoodPort Nanopore PoreSippR image validation completed successfully
```

---

## Deprovisioning

`deprovision.sh` runs:

```bash
sudo waagent -deprovision+user -force
```

Expected warnings include stopping `waagent`, deleting DHCP leases, disabling
the root password, and deleting the Packer account. No provisioner may follow
this step.

Deprovisioning must not remove:

```text
/opt/foodport/poresippr
/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta
/opt/micromamba/root/envs/poresippr
/etc/foodport/poresippr-runtime.json
/etc/foodport/poresippr-targets.json
/etc/foodport/poresippr-repository.json
```

---

## Standard Build Workflow for `0.0.4`

Run from the repository root or from the Packer directory using equivalent
paths.

### Validate shell scripts

```bash
bash -n \
  infrastructure/nanopore-image/scripts/build-image.sh \
  infrastructure/nanopore-image/scripts/provision-base.sh \
  infrastructure/nanopore-image/scripts/install-nvidia-grid.sh \
  infrastructure/nanopore-image/scripts/install-dorado.sh \
  infrastructure/nanopore-image/scripts/install-micromamba.sh \
  infrastructure/nanopore-image/scripts/install-poresippr-environment.sh \
  infrastructure/nanopore-image/scripts/install-poresippr-targets.sh \
  infrastructure/nanopore-image/scripts/install-poresippr-repository.sh \
  infrastructure/nanopore-image/scripts/validate-image.sh \
  infrastructure/nanopore-image/scripts/deprovision.sh
```

### Validate image metadata

```bash
jq empty \
  infrastructure/nanopore-image/files/foodport-image.json
```

### Initialize, format, and validate Packer

```bash
packer fmt \
  infrastructure/nanopore-image/packer/nanopore.pkr.hcl \
  infrastructure/nanopore-image/packer/development.pkrvars.hcl

packer init \
  infrastructure/nanopore-image/packer/nanopore.pkr.hcl

PKR_VAR_poresippr_targets_url='https://example.invalid/poresippr-data/PoreSippR_DB_251110.fasta?placeholder=true' \
PKR_VAR_poresippr_targets_sha256='6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9' \
packer validate \
  -var-file=infrastructure/nanopore-image/packer/development.pkrvars.hcl \
  infrastructure/nanopore-image/packer/nanopore.pkr.hcl
```

The expected validation result is:

```text
The configuration is valid.
```

The placeholder URL is used only to satisfy required-variable validation.
`packer validate` does not download the target. The real SAS URL is created by
`build-image.sh` immediately before validation and build execution.

### Check that the target version is unused

```bash
IMAGE_VERSION=0.0.4

if az sig image-version show \
    --resource-group CFDC-FoodPort-Batch-rg \
    --gallery-name development \
    --gallery-image-definition nanopore \
    --gallery-image-version "$IMAGE_VERSION" \
    >/dev/null 2>&1; then
  echo "ERROR: Image version ${IMAGE_VERSION} already exists." >&2
  exit 1
else
  echo "Image version ${IMAGE_VERSION} is available."
fi
```

### Build with a log

Use the repository wrapper from the portal repository root:

```bash
./infrastructure/nanopore-image/scripts/build-image.sh
```

The wrapper is the preferred and supported build entry point. It validates the
Azure CLI login and subscription, refuses existing gallery versions and build
logs, creates the restricted target SAS, initializes and validates Packer,
runs the build with `PACKER_LOG=1`, verifies gallery publication, reports
remaining temporary resources, and clears sensitive variables on exit.

A direct `packer build` is not equivalent unless the caller independently
generates and exports valid values for both target variables. Do not place a
SAS URL in `development.pkrvars.hcl` or shell history.

### Retry after a failed build

The wrapper refuses to overwrite an existing build log. Preserve the previous
attempt before retrying:

```bash
mv \
  infrastructure/nanopore-image/packer/packer-build-0.0.4.log \
  infrastructure/nanopore-image/packer/packer-build-0.0.4-attempt1.log
```

Then confirm that gallery version `0.0.4` remains unused before starting the
next attempt. Retain failed logs locally for diagnosis, but do not commit them.

### Verify publication

```bash
az sig image-version show \
  --resource-group CFDC-FoodPort-Batch-rg \
  --gallery-name development \
  --gallery-image-definition nanopore \
  --gallery-image-version 0.0.4 \
  --query \
    '{id:id,state:provisioningState,published:publishingProfile.publishedDate,regions:publishingProfile.targetRegions}' \
  --output yaml
```

The required result is:

```text
state: Succeeded
```

### Confirm temporary-resource cleanup

```bash
az resource list \
  --resource-group CFDC-FoodPort-Batch-rg \
  --query "[?contains(name, 'pkr')].{name:name,type:type}" \
  --output table
```

Review the build log before deleting any remaining resources manually.

---

## Published Image History

### `0.0.1`: Connectivity proof

Validated Azure authentication, private networking, SSH, provisioning,
generalization, publication, and cleanup.

### `0.0.2`: NVIDIA foundation

Published NVIDIA GRID `570.237` for `NVadsA10_v5`. The successful build
completed in approximately 14 minutes.

### `0.0.3`: Dorado runtime and model

Published Dorado `2.1.2` and the pinned 400 bps fast model. The image
subsequently passed real GPU basecalling and demultiplexing acceptance.

### `0.0.4`: PoreSippR runtime and incremental scheduler

Current development target. This version adds:

- Micromamba `2.9.0`;
- the retained PoreSippR Conda environment specification;
- minimap2, samtools, POD5, pytest, and required Python packages;
- runtime and explicit package manifests;
- a checksum-pinned static target FASTA containing 6,663 records;
- a commit-pinned PoreSippR-GUI installation;
- the incremental Dorado scheduler and its 41-test suite;
- source, scheduler, target, and environment provenance metadata;
- scoped TLS exceptions for the intercepted build network; and
- expanded final image validation.

It is not yet an accepted image.

---

## FoodPort and AzureBatch Configuration

Until `0.0.4` passes acceptance, continue to treat `0.0.3` as the accepted
image:

```dotenv
NANOPORE_IMAGE=/subscriptions/dcdc7934-5cce-43de-a6ed-22e2e163c2e1/resourceGroups/CFDC-FoodPort-Batch-rg/providers/Microsoft.Compute/galleries/development/images/nanopore/versions/0.0.3
NANOPORE_NODE_AGENT_SKU=batch.node.ubuntu 24.04
NANOPORE_BATCH_VM_SIZE=Standard_NV18ads_A10_v5
NANOPORE_SECURITY_TYPE=trustedLaunch
NANOPORE_SECURE_BOOT_ENABLED=false
NANOPORE_VTPM_ENABLED=false
```

After `0.0.4` passes acceptance, update `NANOPORE_IMAGE` to the immutable
`0.0.4` gallery resource ID.

The runtime path prepared for the new task is:

```dotenv
NANOPORE_RUNTIME_BIN_PATH=/opt/micromamba/root/envs/poresippr/bin:/opt/micromamba/bin:/opt/ont/dorado/bin:/usr/local/bin:/usr/bin:/bin
```

The scheduler and static target database are installed at:

```dotenv
NANOPORE_PORESIPPR_SCHEDULER=/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py
NANOPORE_PORESIPPR_TARGETS=/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta
```

AzureBatch retains Trusted Launch while explicitly disabling Secure Boot and
vTPM for the GRID driver. With pinned `azure-batch==14.2.0`, the model class is
`UefiSettings`:

```python
batchmodels.SecurityProfile(
    security_type="trustedLaunch",
    uefi_settings=batchmodels.UefiSettings(
        secure_boot_enabled=False,
        v_tpm_enabled=False,
    ),
)
```

Do not use `BatchUefiSettings` with this SDK version.

The Batch command must be updated and tested so it invokes the installed
scheduler with the correct input CSV, metadata CSV, mounted input paths, output
directory, completion behavior, and output-file upload conditions.

---

## GPU Acceptance Record for `0.0.3`

The accepted image was tested on:

```text
VM size: Standard_NV18ads_A10_v5
Guest GPU: NVIDIA A10-12Q
GPU memory: 12288 MiB
Driver: 570.237
CUDA reported by driver: 12.8
Dorado: 2.1.2+8b8fc5d
```

The acceptance workflow validated:

- NVIDIA driver loading;
- CUDA access;
- Dorado startup;
- pinned model loading;
- recursive POD5 discovery;
- real GPU basecalling;
- BAM creation and summary parsing; and
- classification-aware demultiplexing.

Observed results included:

```text
Simplex reads basecalled: 80183
Reads processed for barcode classification: 80356
Dorado exit status: 0
BAM size: 264 MB
```

The observed barcode distribution was:

```text
barcode12      1
barcode16      2
barcode20      1
barcode22      1
unknown    80351
```

The low classification rate requires verification of the source run's actual
barcode kit and expected barcodes. It does not indicate failure of the GPU
image, model, basecalling, or demultiplexing implementation.

Detailed retained evidence is under:

```text
infrastructure/nanopore-image/docs/acceptance-results/0.0.3
```

---

## Required GPU Acceptance for `0.0.4`

Image `0.0.4` must repeat the established GPU checks and add PoreSippR runtime
validation.

### Image and repository metadata

Validate:

```bash
cat /etc/foodport/image.json
cat /etc/foodport/nvidia-driver.json
cat /etc/foodport/dorado.json
cat /etc/foodport/micromamba.json
cat /etc/foodport/poresippr-runtime.json
cat /etc/foodport/poresippr-targets.json
cat /etc/foodport/poresippr-repository.json
```

Confirm the repository commit:

```text
691b3a3c2944139cb0093f81909331f7b8d46983
```

### Runtime paths and versions

Validate:

```bash
nvidia-smi
micromamba --version
python --version
pytest --version
minimap2 --version
samtools --version | head -n 1
pod5 --version || true
dorado --version
git --version
jq --version
```

Confirm scheduler CLI startup:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  python \
  /opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py \
  --help
```

Validate the installed target database independently against the pinned release
values and the generated target manifest:

```bash
PINNED_TARGETS_SHA256="6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9"
PINNED_TARGETS_RECORDS="6663"

PORESIPPR_TARGETS="$(
  jq -r \
    '.poresippr_targets.path' \
    /etc/foodport/image.json
)"

EXPECTED_TARGETS_SHA256="$(
  jq -r \
    '.sha256' \
    /etc/foodport/poresippr-targets.json
)"

EXPECTED_TARGETS_RECORDS="$(
  jq -r \
    '.sequence_count' \
    /etc/foodport/poresippr-targets.json
)"

test "$EXPECTED_TARGETS_SHA256" = "$PINNED_TARGETS_SHA256"
test "$EXPECTED_TARGETS_RECORDS" -eq "$PINNED_TARGETS_RECORDS"
test -s "$PORESIPPR_TARGETS"

echo "${PINNED_TARGETS_SHA256}  ${PORESIPPR_TARGETS}" | \
  sha256sum \
    --check \
    --strict

ACTUAL_TARGETS_RECORDS="$(
  grep -c '^>' \
    "$PORESIPPR_TARGETS"
)"

test "$ACTUAL_TARGETS_RECORDS" -eq "$PINNED_TARGETS_RECORDS"

printf 'PoreSippR target records: %s\n' \
  "$ACTUAL_TARGETS_RECORDS"
```

Expected values are:

```text
SHA-256: 6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9
FASTA records: 6663
```

### Functional acceptance

The expanded acceptance should verify:

1. a real POD5 file is readable;
2. Dorado can access the A10 GPU;
3. the pinned model loads;
4. the installed target FASTA matches its pinned checksum and contains
   6,663 records;
5. scheduler run configuration uses the installed target FASTA;
6. the scheduler discovers only stable POD5 files;
7. the scheduler processes bounded batches;
8. demultiplexed FASTQ fragments are retained;
9. minimap2 and samtools produce cumulative mapping results;
10. `state.json` and `status.json` are valid and durable;
11. the scheduler does not process an unchanged POD5 fingerprint twice;
12. the completion marker permits an orderly successful exit; and
13. result files are uploaded according to the Batch task policy.

Acceptance results should be retained under:

```text
infrastructure/nanopore-image/docs/acceptance-results/0.0.4
```

Do not replace the accepted image setting until this acceptance succeeds.

---

## Troubleshooting

### NVIDIA installer warns that no GPU is present

This is expected on the CPU Packer VM. Require installer exit code zero and
validate actual device communication on the GPU acceptance node.

### `nvidia-smi` fails during Packer validation

It can initialize NVML and fail without a GPU. Validate executable presence and
`modinfo nvidia` during Packer, then run `nvidia-smi` on the GPU node.

### NVIDIA module `vermagic` does not match

The source Marketplace image or kernel changed. Rebuild the GRID module against
the running build kernel, or pin the Marketplace image version and repeat the
build.

### Dorado archive TLS failure

Use the scoped insecure download only with mandatory pinned SHA-256
verification. Never bypass checksum verification.

### Dorado model TLS failure

Use the temporary `curl` wrapper only for `dorado download`. Remove it through
the cleanup trap.

### Dorado reports no POD5 data found

Use `--recursive` when the input directory contains nested blob paths, or point
Dorado directly at the POD5-containing directory.

### Micromamba binary or package TLS failure

The build network does not trust the intercepted certificate chain. The
Micromamba binary download uses `curl --insecure` only with mandatory pinned
SHA-256 verification. Package retrieval uses
`/opt/micromamba/root/.mambarc` with `ssl_verify: false` and passes
`--ssl-verify false` explicitly to environment creation. Do not remove the
binary checksum verification.

### PoreSippR target SAS generation fails

The build wrapper uses `az storage account keys list` because the build
identity does not have Blob data-plane or user-delegation-key permissions on
`carlingst01`. Confirm that Azure CLI authentication is active, the expected
subscription is selected, the storage account remains in
`CFDC-FoodPort-Batch-rg`, and account-key retrieval succeeds. The generated SAS
must remain scoped to `PoreSippR_DB_251110.fasta`, read-only, HTTPS-only, and
short-lived.

### PoreSippR target checksum mismatch

Confirm that the blob still matches:

```text
6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9
```

Do not update the checksum merely to make a build pass. First verify that an
intentional target-database release occurred and update the filename, checksum,
metadata, documentation, and acceptance expectations together.

### PoreSippR commit cannot be fetched

Confirm outbound GitHub connectivity and verify that the full SHA exists in the
configured repository:

```text
691b3a3c2944139cb0093f81909331f7b8d46983
```

Do not silently fall back to the branch tip.

### Repository commit does not match

The installer must stop if the checked-out `HEAD` differs from the configured
commit. Confirm the Packer variable file and provisioner environment variables.

### Scheduler or scheduler tests are missing from the installed repository

Confirm that the pinned commit contains:

```text
poresippr_incremental_dorado_scheduler.py
tests/test_poresippr_incremental_dorado_scheduler.py
```

and that the installer copies the repository to:

```text
/opt/foodport/poresippr
```

### Scheduler checksum mismatch

Compare the installed scheduler with
`/etc/foodport/poresippr-repository.json`. A mismatch indicates source mutation
after metadata generation or an incorrect installed path.

### Scheduler syntax validation cannot write bytecode

The installed repository is owned by `root:root`. Use the non-mutating
`compile()` validation and set `PYTHONDONTWRITEBYTECODE=1` for CLI validation.

### Runtime command resolves outside the PoreSippR environment

Confirm the runtime `PATH` begins with:

```text
/opt/micromamba/root/envs/poresippr/bin
```

and compare it with `.poresippr.runtime_bin_path` in
`/etc/foodport/image.json`.

### Gallery version already exists

Gallery versions are immutable. Increment both
`development.pkrvars.hcl` and `foodport-image.json`.

### Build fails after deprovisioning

No provisioner may run after:

```bash
sudo waagent -deprovision+user -force
```

### Temporary Packer resources remain

```bash
az resource list \
  --resource-group CFDC-FoodPort-Batch-rg \
  --query "[?contains(name, 'pkr')].{name:name,type:type}" \
  --output table
```

Review the build log before deleting resources manually.

---

## Versioning Strategy

```text
0.0.1  Connectivity proof
0.0.2  NVIDIA GRID foundation
0.0.3  Dorado runtime and pinned model
0.0.4  PoreSippR runtime, pinned source, and incremental scheduler
0.1.0  First complete development image
1.0.0  First production-ready image
```

For every version:

1. update `image_version` in the variable file;
2. update the image manifest version and build stage;
3. add one focused layer;
4. pin external archives, models, environments, and source commits;
5. extend validation;
6. format and validate Packer;
7. confirm the target version is unused;
8. build and verify publication;
9. run meaningful GPU acceptance;
10. retain non-secret acceptance evidence;
11. update FoodPort only after acceptance; and
12. commit templates, scripts, checksums, metadata, and documentation.

---

## Source-Control Practices

Commit:

- Packer templates;
- non-secret variable files;
- provisioning and validation scripts;
- environment specifications;
- metadata manifests;
- pinned checksums and source commits;
- acceptance scripts;
- non-secret acceptance evidence;
- documentation; and
- pipeline definitions.

Do not commit:

- Packer logs;
- Azure credentials or SAS URLs;
- client secrets or private keys;
- downloaded installers;
- extracted Dorado archives or models;
- local Packer plugin caches;
- temporary Git clones; or
- transient Batch task data.

Use scoped staging for this work because the portal repository may contain
unrelated changes:

```bash
git add \
  infrastructure/nanopore-image
```

Validate only the relevant diff when unrelated working-tree changes exist:

```bash
git diff \
  --check \
  -- \
  infrastructure/nanopore-image
```

---

## Current Status and Next Milestones

Completed:

- `0.0.1` connectivity proof published;
- `0.0.2` NVIDIA GRID foundation published;
- `0.0.3` Dorado runtime and pinned model published and accepted;
- Trusted Launch retained with Secure Boot and vTPM disabled;
- NVIDIA GRID `570.237` validated on NVIDIA A10-12Q;
- Dorado `2.1.2` validated with real POD5 input;
- 80,183 reads basecalled successfully in `0.0.3` acceptance;
- a valid 264 MB BAM created and summarized;
- all 80,356 records demultiplexed;
- Micromamba and the PoreSippR environment added for `0.0.4`;
- minimap2, samtools, POD5, and required Python imports validated;
- the PoreSippR repository pinned to commit
  `691b3a3c2944139cb0093f81909331f7b8d46983`;
- the incremental Dorado scheduler added to the pinned source;
- repository provenance and scheduler checksum metadata added;
- all 41 incremental scheduler tests passed locally;
- Micromamba TLS handling was corrected after the first build attempt
  exposed the intercepted certificate chain;
- the checksum-pinned `PoreSippR_DB_251110.fasta` target database and
  per-build restricted SAS workflow were added;
- Packer formatting and validation completed successfully for the current
  `0.0.4` definition using safe placeholder target variables; and
- scoped shell syntax, JSON, and whitespace checks completed successfully.
- image `0.0.4` built successfully in 19 minutes 46 seconds;
- all in-image validation checks and 41 scheduler tests passed;
- the checksum-pinned target FASTA was validated with 6,663 records;
- gallery version `development/nanopore/0.0.4` was published successfully on
  2026-09-08; and
- Packer removed the temporary VM, NIC, disk, and deployment resources.

Accepted image remains:

```text
/subscriptions/dcdc7934-5cce-43de-a6ed-22e2e163c2e1/resourceGroups/CFDC-FoodPort-Batch-rg/providers/Microsoft.Compute/galleries/development/images/nanopore/versions/0.0.3
```

Immediate next steps:

1. commit the Micromamba TLS, target installer, target metadata, validator, and
   build-wrapper corrections;
2. preserve the failed first-attempt Packer log as
   `packer-build-0.0.4-attempt1.log`;
3. run `build-image.sh`, which creates a fresh target SAS automatically;
4. verify that the in-image scheduler suite reports all 41 tests passing;
5. verify gallery publication and temporary-resource cleanup;
6. update the Azure Batch task command to invoke the installed scheduler;
7. update GPU acceptance to validate repository and target metadata;
8. create representative scheduler run and metadata CSV files;
9. verify the representative run's barcode kit and expected barcodes;
10. confirm completion-marker and output-upload behavior;
11. run the expanded GPU and processing acceptance workflow;
12. retain the `0.0.4` acceptance evidence; and
13. update FoodPort to image `0.0.4` only after acceptance succeeds.

Image `0.0.4` is published as a GPU-acceptance candidate, but it is not yet
accepted for FoodPort use.
