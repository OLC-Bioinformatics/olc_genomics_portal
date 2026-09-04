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
environment, and the commit-pinned PoreSippR source. Actual GPU communication
is tested later on `Standard_NV18ads_A10_v5`.

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

Do not store secrets in `.pkrvars.hcl` files.

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
```

The image installs the repository at:

```text
/opt/foodport/poresippr
```

The installed scheduler path is:

```text
/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py
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
- Micromamba `2.9.0`;
- the PoreSippR environment name, path, and runtime `PATH`; and
- the pinned PoreSippR repository URL, commit, installation path, and scheduler.

The repository section is equivalent to:

```json
{
  "poresippr_repository": {
    "repository": "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git",
    "commit": "691b3a3c2944139cb0093f81909331f7b8d46983",
    "source_branch": "madhubioinfo-dorado-patch1",
    "install_path": "/opt/foodport/poresippr",
    "scheduler": "/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py"
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
8. install-poresippr-repository.sh
9. validate-image.sh
10. deprovision.sh
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
reported version, and root-prefix directory.

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
7. confirms that the incremental scheduler exists;
8. installs the repository under `/opt/foodport/poresippr`;
9. excludes `.git`, `__pycache__`, and `.pyc` files;
10. normalizes ownership to `root:root`;
11. validates scheduler syntax without writing bytecode;
12. executes scheduler `--help` with bytecode disabled;
13. calculates the installed scheduler SHA-256; and
14. writes `/etc/foodport/poresippr-repository.json`.

The generated repository manifest contains:

```json
{
  "repository": "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git",
  "commit": "691b3a3c2944139cb0093f81909331f7b8d46983",
  "source_branch": "madhubioinfo-dorado-patch1",
  "install_path": "/opt/foodport/poresippr",
  "scheduler": "/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py",
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
- required Python imports;
- package and explicit manifests;
- required packages in the retained package manifest.

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
- scheduler `--help` execution with bytecode disabled; and
- agreement with the repository section in image metadata.

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
/etc/foodport/poresippr-repository.json
```

---

## Standard Build Workflow for `0.0.4`

Run from the repository root or from the Packer directory using equivalent
paths.

### Validate shell scripts

```bash
bash -n \
  infrastructure/nanopore-image/scripts/provision-base.sh \
  infrastructure/nanopore-image/scripts/install-nvidia-grid.sh \
  infrastructure/nanopore-image/scripts/install-dorado.sh \
  infrastructure/nanopore-image/scripts/install-micromamba.sh \
  infrastructure/nanopore-image/scripts/install-poresippr-environment.sh \
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

packer validate \
  -var-file=infrastructure/nanopore-image/packer/development.pkrvars.hcl \
  infrastructure/nanopore-image/packer/nanopore.pkr.hcl
```

The expected validation result is:

```text
The configuration is valid.
```

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

The repository includes `scripts/build-image.sh`. The equivalent direct Packer
workflow is:

```bash
cd ~/FoodPort/olc_genomics_portal/infrastructure/nanopore-image/packer

IMAGE_VERSION=0.0.4
rm -f "packer-build-${IMAGE_VERSION}.log"

PACKER_LOG=1 \
PACKER_LOG_PATH="packer-build-${IMAGE_VERSION}.log" \
packer build \
  -var-file=development.pkrvars.hcl \
  nanopore.pkr.hcl
```

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
- minimap2, samtools, POD5, and required Python packages;
- runtime and explicit package manifests;
- a commit-pinned PoreSippR-GUI installation;
- the incremental Dorado scheduler;
- source and scheduler checksum metadata; and
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

The scheduler is installed at:

```dotenv
NANOPORE_PORESIPPR_SCHEDULER=/opt/foodport/poresippr/poresippr_incremental_dorado_scheduler.py
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

### Functional acceptance

The expanded acceptance should verify:

1. a real POD5 file is readable;
2. Dorado can access the A10 GPU;
3. the pinned model loads;
4. the scheduler discovers only stable POD5 files;
5. the scheduler processes bounded batches;
6. demultiplexed FASTQ fragments are retained;
7. minimap2 and samtools produce cumulative mapping results;
8. `state.json` and `status.json` are valid and durable;
9. the scheduler does not process an unchanged POD5 fingerprint twice;
10. the completion marker permits an orderly successful exit; and
11. result files are uploaded according to the Batch task policy.

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

### Scheduler is missing from the installed repository

Confirm that the pinned commit contains:

```text
poresippr_incremental_dorado_scheduler.py
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
- Packer formatting and validation completed successfully for the current
  `0.0.4` definition; and
- scoped shell syntax, JSON, and whitespace checks completed successfully.

Accepted image remains:

```text
/subscriptions/dcdc7934-5cce-43de-a6ed-22e2e163c2e1/resourceGroups/CFDC-FoodPort-Batch-rg/providers/Microsoft.Compute/galleries/development/images/nanopore/versions/0.0.3
```

Immediate next steps:

1. review and commit the complete `infrastructure/nanopore-image` change set;
2. add focused unit tests for the incremental scheduler;
3. update the Azure Batch task command to invoke the installed scheduler;
4. update GPU acceptance to validate repository metadata and the scheduler;
5. decide how acceptance will create the scheduler run and metadata CSV files;
6. verify the representative run's actual barcode kit and expected barcodes;
7. confirm completion-marker and output-upload behavior;
8. build and publish immutable image `0.0.4`;
9. run the expanded GPU and processing acceptance workflow;
10. retain the `0.0.4` acceptance evidence; and
11. update FoodPort to image `0.0.4` only after acceptance succeeds.

Image `0.0.4` is therefore implementation-ready for source control and build
preparation, but it is not yet accepted for FoodPort use.
