# FoodPort Nanopore Task Wrapper and Storage Plan

## Status

This document records the agreed design and current implementation state of
the FoodPort Nanopore workflow following successful build, publication, and
immutable GPU acceptance of Nanopore image `0.0.13`.

Current status:

- Image `development/nanopore/0.0.13` is the accepted development image.
- The task wrapper is installed in the image and its manifest validation,
  atomic input materialization, scheduler execution, and result-manifest
  behavior are covered by acceptance evidence.
- The scheduler acceptance suite passed twice in the latest GPU run.
- The latest acceptance run processed one POD5 file as one scheduler batch,
  summarized 80,356 reads, retained four barcode FASTQ outputs, produced four
  mapping BAM/BAI pairs and four mapping result CSV files, verified one
  expected `gntK` read for barcode 22, and prevented duplicate processing.
- FoodPort model/API submission and Batch polling code are present and have
  focused tests.
- Azure Blob output publication, restart-aware publication state, and the
  authenticated FoodPort API-to-Batch-to-publication workflow are implemented;
  a live target-environment smoke test remains an operational check.

The image now provides the validated scientific runtime required to:

- access an NVIDIA A10 GPU;
- run Dorado basecalling with the pinned model;
- demultiplex configured barcodes;
- retain barcode-specific FASTQ fragments;
- run cumulative minimap2 target mapping;
- sort and index BAM files with Samtools;
- generate iteration-specific target result CSV files;
- maintain durable scheduler state and status documents; and
- prevent an unchanged POD5 file from being processed twice.

Image `0.0.8` passed immutable GPU acceptance with:

- PoreSippR commit
  `3b89eaf5a0a204fc9e7ba696b92c4b5fa1364065`;
- target FASTA SHA-256
  `6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9`;
- 6,663 target records;
- six POD5 files totaling 19,421,191,856 bytes;
- 291,082 reads in the final summary;
- 7 classified reads and 291,075 unclassified reads;
- two retained barcode FASTQ outputs;
- two mapping BAM and BAI pairs and twelve mapping result CSV files;
- six cumulative `gntK` reads across the six batches;
- exactly one expected `gntK` read for barcode 22 in the per-batch validation;
- successful duplicate-processing prevention; and
- 896 seconds of GPU processing elapsed time.

Acceptance evidence is retained under
`infrastructure/nanopore-image/docs/acceptance-results/0.0.8-six-files-20260911h`.

---

## 1. Architectural Principles

### 1.1 FoodPort remains the system of record

FoodPort owns:

- run creation and run-name uniqueness;
- file records and upload status;
- immutable input manifests;
- finalization;
- Azure Batch submission;
- processing status;
- result locations;
- retry and cancellation state; and
- authorization for run and result access.

### 1.2 The GUI remains an operator client

The GUI may:

- create a FoodPort run;
- discover and monitor local POD5 files;
- optionally calculate SHA-256 while reading files;
- request short-lived per-blob upload URLs;
- upload POD5 files directly to Azure Blob Storage;
- confirm completed uploads;
- finalize a run; and
- display upload, processing, and result status.

The GUI must not:

- receive Azure account keys;
- store long-lived SAS credentials;
- decide run-name uniqueness without server enforcement;
- construct the authoritative finalized manifest independently of FoodPort; or
- submit an untracked Batch processing job.

### 1.3 The scheduler remains cloud-independent

`poresippr_incremental_dorado_scheduler.py` remains responsible for local
scientific processing only:

- stable POD5 discovery;
- bounded batching;
- processing each POD5 fingerprint once;
- Dorado basecalling;
- barcode demultiplexing;
- retained FASTQ management;
- cumulative mapping;
- target result generation;
- state and status checkpointing; and
- orderly completion and failure status.

The scheduler must not know about:

- Azure storage account names;
- container credentials;
- SAS URLs;
- FoodPort API tokens;
- Batch job or task identifiers; or
- result access-control policy.

### 1.4 A Python task wrapper owns orchestration

A new Python task wrapper will own:

- input-manifest retrieval and validation;
- POD5 download and verification;
- scheduler run and metadata CSV generation;
- scheduler process lifecycle;
- FASTQ compression coordination;
- committed-output detection;
- cloud publication;
- publication checkpointing;
- software and reference provenance;
- final result-manifest generation;
- log and exit-code capture; and
- local disk reclamation.

---

## 2. Run Identity

The externally visible run identifier will use the GUI-provided run name, for
example:

```text
260825-nanopore
```

FoodPort must enforce run-name uniqueness transactionally. Client-side checks
alone are insufficient because two clients could attempt to reserve the same
name concurrently.

Recommended run-name rules:

- required and immutable after creation;
- unique in the applicable FoodPort namespace;
- normalized to lowercase;
- leading and trailing whitespace removed;
- no `/` or `\\` path separators;
- no `.` or `..` path components;
- limited to letters, numbers, and single hyphens;
- no consecutive hyphens;
- practical maximum length enforced by FoodPort; and
- rejected with an HTTP conflict response when already reserved.

FoodPort should retain both its database identifier and the run name:

```json
{
  "run_id": 42,
  "run_name": "260825-nanopore",
  "storage_prefix": "runs/260825-nanopore"
}
```

The run name is the storage-facing identifier. The database ID remains useful
for relational integrity, auditing, and API routing.

---

## 3. Azure Storage Layout

New Nanopore runs will use lifecycle-oriented containers rather than one
container per run.

### 3.1 Raw-input container

```text
Container: nanopore-runs

<run-name>/
  input/
    manifests/
      input-manifest-v000001.json
      input-manifest-v000002.json
    control/
      state.json
    pod5/
      <normalized-relative-path>.pod5
```

Example:

```text
nanopore-runs/
  260825-nanopore/
    input/manifests/input-manifest-v000001.json
    input/pod5/pass/sample-001.pod5
```

### 3.2 Results container

```text
Container: nanopore-results

runs/<run-name>/
    fastq/
    mapping/
    results/
    manifests/
    control/
    logs/
```

Example:

```text
runs/260825-nanopore/
    fastq/
        barcode12/
            batch-000001/
                sample.fastq.gz
        barcode22/
            batch-000001/
                sample.fastq.gz

    mapping/
        iteration-000001/
            sample12.sorted.bam
            sample12.sorted.bam.bai
        final/
            sample12.sorted.bam
            sample12.sorted.bam.bai

    results/
        sample12_iteration1.csv
        sample22_iteration1.csv

    manifests/
        iterations/
            iteration-000001.json
        publication-state.json
        result-manifest.json
        latest.json

    control/
      state.json

    logs/
        task-stdout.log
        task-stderr.log
        exit-code.txt
```

### 3.3 Existing data

Existing container-per-run workflows do not need to be migrated. The new
prefix-based storage model applies to the new Nanopore API and GUI workflow.

### 3.4 Downloading a complete run

A complete run can still be downloaded recursively using its run prefix. The
change from container-per-run to prefix-per-run does not prevent bulk download.

---

## 4. Input Manifest and Upload Workflow

### 4.1 POD5 upload

For each POD5 file:

1. the GUI discovers a stable local file;
2. the GUI calls FoodPort's prepare endpoint;
3. FoodPort normalizes and validates the relative path;
4. FoodPort creates or returns the unique `NanoporeFile` record;
5. FoodPort returns a short-lived, write/create-only SAS scoped to one blob;
6. the GUI uploads the bytes directly to `nanopore-raw`;
7. the GUI calls the complete endpoint; and
8. FoodPort verifies cloud identity and marks the file uploaded.

The prepare and complete operations must be idempotent.

### 4.2 Blob completion evidence

The completed file record should contain:

- normalized relative path;
- blob name;
- expected and observed size;
- ETag;
- upload completion time;
- upload attempt count;
- status; and
- SHA-256 when available.

### 4.3 Hashing policy

Preferred long-term policy:

- the GUI calculates SHA-256 while reading/uploading each POD5 file;
- the complete endpoint records that digest; and
- the Batch wrapper verifies SHA-256 after download.

Acceptable first-release policy if GUI hashing is deferred:

- FoodPort verifies blob existence, blob name, size, and ETag;
- the wrapper verifies the downloaded local size; and
- SHA-256 remains optional but recommended.

A size mismatch or digest mismatch must fail the download. The wrapper must not
silently process a mismatched file.

### 4.4 Authoritative manifest

One JSON manifest is authoritative. The scheduler's run and metadata CSV files
are generated locally by the Python wrapper from that manifest.

Example finalized manifest:

```json
{
  "schema_version": 1,
  "run_id": 42,
  "run_name": "260825-nanopore",
  "finalized": true,
  "finalized_at": "2026-09-09T13:30:00Z",
  "dorado": {
    "model": "dna_r10.4.1_e8.2_400bps_fast@v5.2.0",
    "barcode_kit": "SQK-RBK114-24"
  },
  "reference": {
    "path": "/opt/foodport/poresippr-data/PoreSippR_DB_251110.fasta",
    "sha256": "6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9",
    "sequence_count": 6663
  },
  "barcodes": [
    {
      "barcode": 12,
      "seqid": "sample-12",
      "olnid": "OLN12"
    }
  ],
  "files": [
    {
      "relative_path": "pass/sample-001.pod5",
      "blob_name": "runs/260825-nanopore/pod5/pass/sample-001.pod5",
      "size_bytes": 3095356360,
      "etag": "example-etag",
      "sha256": null
    }
  ]
}
```

The wrapper validates the manifest and generates:

```text
scheduler-run.csv
scheduler-metadata.csv
```

The manifest and generated CSV files must be retained in task evidence.

---

## 5. Processing Mode

### 5.1 Streaming production mode

The production implementation should process while uploads continue:

```text
GUI uploads files
FoodPort marks files complete
FoodPort writes immutable batch manifest generation
FoodPort starts the long-lived Batch task.
wrapper downloads manifest and POD5 files
scheduler processes bounded batches
wrapper publishes each batch report
additional files enter later manifest generations
user terminates run
FoodPort stops new batches and signals Batch VM/task shutdown
```

### 5.2 Manifest generations

Streaming mode processes while uploads continue and uses monotonic manifest
generations:

```text
input-manifest-v000001.json
input-manifest-v000002.json
```

The wrapper polls these immutable names through the input-container SAS rather
than treating a live POD5 listing as authoritative. Each generation is
processed once in the same task workspace, and its result manifest is
published under:

```text
runs/<run-name>/iterations/iteration-000001/manifests/iteration-000001.json
```

FoodPort writes `<run-name>/input/control/state.json` with `state=stopping` or
`state=error` during termination or an explicit run error. The wrapper drains
visible generations and exits. A scheduler failure exits immediately; Azure
Batch's task walltime is the hard upper bound and is currently 16 hours.

---

## 6. Local Task Directory Layout

The wrapper prepares a local task root such as:

```text
/mnt/resource/foodport-nanopore/
    input/
        scheduler-run.csv
        scheduler-metadata.csv
        .upload-complete
        pod5/
            ...

    output/
        fastq/
        mapping/
        results/
        state.json
        status.json

    wrapper/
        download-state.json
        publication-state.json
        task-metadata.json
        result-manifest.json

    logs/
        scheduler-stdout.log
        scheduler-stderr.log
        wrapper.log
        exit-code.txt
```

Downloads use temporary names:

```text
input/pod5/.incoming/<name>.partial
```

After validation, the wrapper atomically renames each file to its final local
path. The scheduler only observes final paths.

---

## 7. Download Management

### 7.1 Wrapper responsibilities

The Python wrapper downloads input files. The scientific scheduler does not.

For each file, the wrapper:

1. validates the manifest path;
2. rejects absolute paths and traversal components;
3. downloads to a temporary path;
4. checks size;
5. checks SHA-256 when provided;
6. atomically renames the completed file;
7. records it in `download-state.json`; and
8. safely resumes after restart.

### 7.2 Batch sizing and disk controls

The wrapper should support:

```text
max_download_files
max_download_bytes
ready_file_target
minimum_free_disk_bytes
poll_seconds
```

For a future live-upload mode, the wrapper should remain only one or two
scheduler batches ahead. For example:

```text
scheduler maximum batch files: 4
wrapper ready-file target: 8
```

### 7.3 Local POD5 reclamation

The wrapper may delete a local processed POD5 only after:

- the scheduler has atomically checkpointed the batch;
- required batch outputs have been published;
- the input is present in the authoritative cloud manifest;
- size and optional digest were verified; and
- publication state records the completed output set.

The cloud source remains authoritative for task retry.

---

## 8. FASTQ Compression and Retention

### 8.1 Storage requirement

Barcode-specific FASTQ fragments are retained in the task workspace and are
published with the iteration outputs. The current scheduler accepts both
`.fastq` and `.fastq.gz`; gzip publication is a future optimization, not a
condition of the current workflow.

### 8.2 Dorado behavior

Dorado demultiplexing emits uncompressed FASTQ when `--emit-fastq` is used.
The scheduler retains those fragments and maps them cumulatively.

### 8.3 Compression location

Compression is not currently required by the accepted implementation. The
scheduler retains the demultiplexed fragments in its durable `fastq/` tree and
the wrapper publishes the committed output tree for each iteration.

### 8.4 Mapping compatibility

The scheduler recognizes `.fastq` and `.fastq.gz`, and its base-count logic
reads gzip-compressed FASTQ. Current acceptance uses the retained fragments
and validates cumulative mapping across repeated processing passes.

### 8.5 Required tests

- cumulative base counting works for gzip files;
- minimap2 mapping succeeds using retained FASTQ inputs; and
- scheduler retries do not duplicate or corrupt retained gzip files.

---

## 9. Output Publication Model

### 9.1 Publication uses scheduler checkpoints

The wrapper must not upload scheduler outputs merely because a file appears
stale. A scheduler batch is publishable only after it appears in the atomically
written `state.json` batch ledger.

The wrapper runs one scheduler pass per immutable manifest generation. When a
generation finishes, it publishes that generation's immutable output set before
polling for the next generation.

### 9.2 Immutable outputs

Upload once and never silently overwrite:

```text
fastq/barcodeXX/batch-XXXXXX/*.fastq.gz
results/<seqid>_iteration<N>.csv
manifests/iterations/iteration-<N>.json
```

On retry:

- skip an existing destination when size and SHA-256 match;
- fail when an existing immutable destination differs; and
- never silently replace conflicting scientific evidence.

### 9.3 Cumulative mapping snapshots

Local cumulative mapping paths are regenerated after each iteration:

```text
mapping/<seqid>.sorted.bam
mapping/<seqid>.sorted.bam.bai
```

Cloud publication uses versioned iteration paths. The wrapper polls immutable
input-manifest names through the input-container SAS rather than treating a
live POD5 listing as authoritative. Each generation is processed once in the
same task workspace, and its result manifest is published under:

```text
runs/<run-name>/iterations/iteration-000001/manifests/iteration-000001.json
```

The wrapper waits for later generations until FoodPort writes
`<run-name>/input/control/state.json` with `state=stopping` or `state=error`.
The task then drains visible generations and exits. A scheduler failure exits
the task immediately; Azure Batch's task walltime remains the hard upper
bound (currently 16 hours). FoodPort submits exactly one Azure Batch task per
run.

After successful run completion, the final pair may also be published under:

```text
mapping/final/<seqid>.sorted.bam
mapping/final/<seqid>.sorted.bam.bai
```

The BAM and BAI must be published as one manifest-governed pair. Consumers must
not infer completeness from one blob alone.

### 9.4 Mutable latest-state documents

These may be overwritten intentionally:

```text
control/state.json
control/status.json
manifests/publication-state.json
manifests/latest.json
```

Publication order:

1. immutable FASTQ, result, BAM, and BAI files;
2. immutable iteration manifest;
3. state and status snapshots;
4. `latest.json` last.

`latest.json` acts as the commit pointer for readers.

Example:

```json
{
  "schema_version": 1,
  "run_name": "260825-nanopore",
  "latest_iteration": 3,
  "iteration_manifest": "manifests/iterations/iteration-000003.json",
  "updated_at": "2026-09-09T14:25:00Z"
}
```

### 9.5 Intermediate BAM retention

Default proposal:

- retain all FASTQ.gz batch files permanently according to protocol;
- retain all result CSV files permanently;
- retain all iteration manifests permanently;
- retain final cumulative BAM and BAI according to protocol;
- retain intermediate cumulative BAM and BAI snapshots for a configurable
  temporary period; and
- apply Azure lifecycle rules to remove expired intermediate mapping snapshots.

The exact intermediate-BAM retention period remains an operational decision.

---

## 10. Publication State and Restart Safety

The wrapper maintains:

```text
publication-state.json
```

Example:

```json
{
  "schema_version": 1,
  "run_name": "260825-nanopore",
  "published_files": {
    "fastq/barcode12/batch-000001/sample.fastq.gz": {
      "size_bytes": 1234567,
      "sha256": "example-digest",
      "etag": "example-etag",
      "published_at": "2026-09-09T14:00:00Z"
    }
  },
  "published_iterations": [1],
  "latest_iteration": 1
}
```

Rules:

1. path plus SHA-256 identifies an immutable published file;
2. matching existing files are skipped safely;
3. conflicting existing files cause failure;
4. publication state is written atomically;
5. a wrapper restart resumes from publication state;
6. final task success requires processing and publication success; and
7. the final result manifest is uploaded last.

---

## 11. Python Task Wrapper

The accepted 0.0.13 image installs the task-wrapper slice at:

```text
/opt/foodport/poresippr/foodport_nanopore_task.py
/usr/local/bin/foodport-nanopore-task
```

The wrapper validates each generation manifest, rejects traversal and duplicate
POD5 paths, verifies file size and optional SHA-256, materializes POD5 files
atomically, generates scheduler CSV files, runs one scheduler pass, publishes
the generation outputs, and then polls for the next generation. A control blob
set to `stopping` or `error` controls shutdown.

### 11.1 Installed entry point

Installed source path:

```text
/opt/foodport/poresippr/foodport_nanopore_task.py
```

Stable command:

```text
/usr/local/bin/foodport-nanopore-task
```

### 11.2 Suggested structure

Initial implementation may be one script with testable classes:

```text
ManifestClient
ManifestValidator
BlobDownloader
SchedulerConfigurationBuilder
SchedulerProcess
ResultPublisher
ProvenanceCollector
TaskController
```

A later refactor may split these into a package.

### 11.3 Wrapper lifecycle

```text
load environment and task configuration
fetch and validate the first generation manifest
download and validate its POD5 inputs
generate scheduler CSV files and run one scheduler pass
publish that generation's outputs and result manifest
poll for the next immutable generation
repeat until stopping, error, walltime, or task failure
write final logs and exit status
exit according to processing and publication outcome
```

### 11.4 Exit semantics

The wrapper distinguishes:

```text
processing_status
publication_status
```

A successful scientific run with failed result publication is a failed Batch
task because FoodPort cannot consider the run complete until outputs are durable.

The wrapper must attempt to publish failure logs and status even when the
scheduler exits nonzero.

---

## 12. Software and Reference Provenance

The final result manifest must capture the complete runtime provenance.

### 12.1 Image metadata sources

Read existing image metadata from:

```text
/etc/foodport/image.json
/etc/foodport/nvidia-driver.json
/etc/foodport/dorado.json
/etc/foodport/micromamba.json
/etc/foodport/poresippr-runtime.json
/etc/foodport/poresippr-repository.json
/etc/foodport/poresippr-targets.json
```

Supplement it with live runtime checks.

### 12.2 Required software fields

```json
{
  "software": {
    "image": {
      "gallery_version": "0.0.8",
      "resource_id": "/subscriptions/.../versions/0.0.8"
    },
    "operating_system": {
      "name": "Ubuntu",
      "version": "24.04",
      "kernel": "runtime-value"
    },
    "gpu": {
      "name": "NVIDIA A10-12Q",
      "memory_mib": 12288,
      "driver_version": "570.237"
    },
    "dorado": {
      "version": "2.1.2+8b8fc5d",
      "model": "dna_r10.4.1_e8.2_400bps_fast@v5.2.0"
    },
    "python": {
      "version": "3.12.14"
    },
    "minimap2": {
      "version": "2.31-r1302"
    },
    "samtools": {
      "version": "1.24"
    },
    "micromamba": {
      "version": "2.9.0"
    },
    "pod5": {
      "version": "runtime-value"
    },
    "azcopy": {
      "version": "runtime-value"
    },
    "poresippr": {
      "repository": "https://github.com/OLC-Bioinformatics/PoreSippR-GUI.git",
      "commit": "3b89eaf5a0a204fc9e7ba696b92c4b5fa1364065",
      "scheduler_sha256": "runtime-value"
    }
  }
}
```

### 12.3 Reference provenance

```json
{
  "reference": {
    "name": "PoreSippR_DB_251110.fasta",
    "sha256": "6cb7610351c99d80023ac800a99430b2763b446ad5399abf61ae06a5584857c9",
    "sequence_count": 6663
  }
}
```

### 12.4 Additional reproducibility fields

Record:

- input-manifest path and SHA-256;
- scheduler run CSV SHA-256;
- scheduler metadata CSV SHA-256;
- task start and finish times;
- Batch job and task IDs;
- pool ID and VM size;
- wrapper version and source commit;
- scheduler arguments;
- output file sizes and SHA-256 values;
- scheduler state schema version;
- result-manifest schema version; and
- processing and publication statuses.

---

## 13. Final Result Manifest

Example:

```json
{
  "schema_version": 1,
  "run_id": 42,
  "run_name": "260825-nanopore",
  "status": "completed",
  "processing_status": "completed",
  "publication_status": "completed",
  "input_manifest": {
    "blob_name": "260825-nanopore/input/manifests/input-manifest-v000001.json",
    "sha256": "example-digest"
  },
  "software": {},
  "reference": {},
  "processing": {
    "processed_pod5_count": 48,
    "batch_count": 12,
    "final_iteration": 12,
    "started_at": "2026-09-09T13:30:00Z",
    "finished_at": "2026-09-09T15:00:00Z"
  },
  "outputs": [
    {
      "kind": "fastq",
      "blob_name": "runs/260825-nanopore/fastq/barcode22/batch-000001/sample.fastq.gz",
      "size_bytes": 123456,
      "sha256": "example-digest"
    }
  ]
}
```

FoodPort consumes this manifest rather than discovering results through a live
blob listing.

---

## 14. Authentication and Credential Handling

Required principles:

- no Azure account keys in the GUI;
- no long-lived SAS credentials in the GUI or Batch task;
- no SAS query strings in logs, state, or manifests;
- separate read access for raw inputs and write access for results;
- short expiry;
- least privilege;
- credentials supplied through secure Batch task configuration; and
- query strings redacted from all exceptions.

Preferred long-term option:

```text
Batch pool managed identity with Blob data-plane permissions scoped to the
required containers.
```

Acceptable initial option:

```text
short-lived input and output SAS credentials generated server-side by FoodPort
and supplied securely to the Batch task.
```

For the current integration phase, credentials must remain server-side and
must not appear in GUI state, task logs, manifests, or exceptions. The
long-term production choice between a Batch managed identity and short-lived
server-generated SAS credentials remains an operational deployment decision.

---

## 15. Retention Policy Defaults

Agreed requirement:

- `.fastq.gz` files are permanent protocol-required outputs.

Proposed defaults pending confirmation:

- raw POD5: retain according to the existing raw-data protocol;
- FASTQ.gz: permanent protocol retention;
- result CSV: permanent;
- iteration manifests: permanent;
- final BAM and BAI: protocol retention;
- intermediate cumulative BAM and BAI: temporary configurable retention;
- scheduler state and status: retain with results;
- task logs: retain according to operational audit policy; and
- local Batch-node files: delete after verified cloud publication or node
  teardown.

---

## 16. Required Tests

### 16.1 Manifest and path validation

- reject absolute paths;
- reject `..` traversal;
- reject mismatched run prefixes;
- reject duplicate file entries;
- reject missing required fields;
- reject invalid run names;
- validate immutable input-manifest generation handling; and
- verify generated CSV content.

### 16.2 Downloads

- retry does not duplicate local files;
- size mismatch fails;
- SHA-256 mismatch fails when provided;
- partial files never become scheduler-visible;
- restart resumes completed downloads;
- disk-floor checks prevent unsafe downloads; and
- manifest files outside the run prefix are rejected.

### 16.3 FASTQ compression

- valid deterministic gzip output;
- exact decompressed content;
- atomic final appearance;
- cleanup on error;
- cumulative base counting;
- minimap2 accepts compressed FASTQ; and
- retries do not duplicate retained output.

### 16.4 Publication

- immutable matching blob is skipped;
- immutable conflicting blob fails;
- BAM and BAI are represented as one iteration output set;
- iteration manifest uploads only after files;
- `latest.json` uploads last;
- restart resumes publication;
- publication-state writes are atomic;
- failure logs upload after scheduler failure;
- publication failure causes task failure; and
- final manifest uploads only after all required outputs.

### 16.5 Provenance

- all required software versions are captured;
- image metadata agrees with live runtime values where applicable;
- reference checksum and record count are correct;
- scheduler and wrapper source identities are recorded;
- manifest and CSV digests are recorded; and
- no credential-bearing URLs appear in manifests.

### 16.6 Integration

- representative finalized manifest downloads and processes successfully;
- expected FASTQ.gz files are published;
- expected mapping and result files are published;
- duplicate POD5 processing is prevented;
- task restart resumes safely;
- exactly one finalized run produces exactly one Batch processing submission;
- retry processes the identical immutable manifest; and
- FoodPort can report current processing and publication state.

---

## 17. Implementation Sequence

1. Define the input-manifest JSON schema.
2. Define iteration-manifest and result-manifest JSON schemas.
3. Define and enforce run-name normalization and uniqueness in FoodPort.
4. Implement local manifest validation.
5. Implement scheduler CSV generation from the manifest.
6. Add deterministic retained FASTQ compression to the scheduler.
7. Add gzip and minimap2 regression tests.
8. Implement the wrapper's local download-state handling.
9. Implement software and reference provenance collection.
10. Implement a local-filesystem publication backend for tests.
11. Implement the Azure Blob download and publication backend.
12. Implement publication-state and restart behavior.
13. Implement final result-manifest generation.
14. Add FoodPort Batch task submission using the wrapper entry point.
15. Test the wrapper on a disposable VM created from image `0.0.8`.
16. Build the wrapper and FASTQ compression into the next immutable image.
17. Run full immutable image and task-wrapper acceptance.

---

## 18. Decisions Still Required

These decisions do not block schema and local wrapper development, but they
must be resolved before production deployment.

### 18.1 Processing mode

The first-release mode is streaming. FoodPort submits exactly one Azure Batch
job/task for an open run. The wrapper polls immutable manifest generations,
invokes the cloud-independent scheduler for each generation, and publishes
iteration-scoped result manifests. Finalization stops intake and writes the
control marker; the task exits after draining visible generations.

### 18.2 POD5 SHA-256 requirement

Recommended target:

```text
GUI calculates SHA-256 during upload and wrapper verifies after download.
```

Possible first release:

```text
ETag plus exact size required; SHA-256 optional.
```

### 18.3 Batch storage authentication

Choose between:

```text
managed identity
```

and:

```text
short-lived server-generated input/output SAS credentials
```

Managed identity is preferred when the required role assignments and SDK flow
are practical.

### 18.4 Intermediate BAM retention

Set the lifecycle period for versioned non-final BAM/BAI snapshots. Final BAM,
BAI, FASTQ.gz, result CSV, and manifests follow the approved protocol.

### 18.5 Wrapper source location and versioning

Decide whether the wrapper initially lives in:

```text
olc_genomics_portal/infrastructure/nanopore-image
```

or in a dedicated package/repository. Regardless of location, the result
manifest must record its exact commit and file checksum.

---

## 19. Definition of Done for the Next Image Milestone

The next image milestone is complete when:

- a stable Python task-wrapper command is installed;
- a validated finalized manifest can be downloaded;
- scheduler CSV files are generated locally;
- POD5 files are downloaded and verified safely;
- Dorado output is retained as `.fastq.gz`;
- the scheduler completes using compressed FASTQ files;
- committed iterations are published incrementally;
- cumulative BAM/BAI snapshots use versioned cloud paths;
- final FASTQ.gz, BAM/BAI, CSV, state, status, logs, and manifests are durable;
- complete software and reference provenance is recorded;
- wrapper restart resumes without duplicate processing or publication;
- failures publish diagnostic status and logs;
- the task exits successfully only after required publication succeeds; and
- end-to-end acceptance passes against the two-container run-prefix layout.

---

## 20. Summary of Agreed Design

```text
Run name:
    unique FoodPort-enforced name such as 260825-nanopore

Raw storage:
    nanopore-runs/<run-name>/input/pod5/
    nanopore-runs/<run-name>/input/manifests/

Result storage:
    nanopore-results/runs/<run-name>/fastq/
    nanopore-results/runs/<run-name>/mapping/
    nanopore-results/runs/<run-name>/results/
    nanopore-results/runs/<run-name>/manifests/
    nanopore-results/runs/<run-name>/control/
    nanopore-results/runs/<run-name>/logs/

FoodPort:
    authoritative run, files, finalization, manifest, Batch submission, status

GUI:
    discovery, optional hashing, upload, completion, finalize, progress

Python wrapper:
    download, verify, generate CSVs, run scheduler, publish, provenance

Scheduler:
    local scientific processing and durable batch state only

FASTQ:
    Dorado emits .fastq; scheduler compresses atomically to permanent .fastq.gz

Publication trigger:
    committed scheduler state, not file-age heuristics

Immutable cloud outputs:
    FASTQ batches, iteration CSVs, iteration manifests, versioned BAM/BAI

Mutable cloud snapshots:
    state.json, status.json, publication-state.json, latest.json

Provenance:
    complete image, GPU, software, source, scheduler, reference, and task data
```
