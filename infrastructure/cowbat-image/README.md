# COWBAT Batch image

This image extends the existing COWBAT gallery image with refreshed Ubuntu CA
certificates, the GoC root CA in both the Ubuntu and Conda trust stores, a
Prokka 1.14.6 environment at `/envs/prokka`, and a fully initialized
MOB-Suite database. The database is installed at MOB-Suite's package-default
location so the existing `mob_recon` command can find it without a per-job
download. The build VM uses Trusted Launch to match the source image's
security type.

The build is pinned to source image `0.0.60` and publishes immutable gallery
version `0.0.61` in Canada Central. It uses Packer's Azure CLI authentication;
the build host must be logged into the FoodPort subscription and able to reach
the configured private subnet.

From the repository root, run:

```bash
infrastructure/cowbat-image/scripts/build-image.sh
```

The repository-root `GoC-GdC-Root-A.crt` file must be supplied from its
authorized secure source; it is intentionally ignored by Git and should not be
committed.

The build validates that version `0.0.61` is unused, installs and checks
Prokka, initializes the MOB database from the upstream MOB-Suite mirror,
checks the database artifacts, publishes the new gallery version, and verifies
its provisioning state. The build fails rather than overwriting an existing
gallery version.

After publication, set the Batch service's `VM_IMAGE` configuration to the
new version's resource ID and restart the Batch service so new COWBAT pools
use it. Also update the corresponding Azure DevOps `VM_IMAGE` variable so a
future deployment does not restore version `0.0.59`.
