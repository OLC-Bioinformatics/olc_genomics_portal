# Nanopore Backend Implementation

This document describes the implemented FoodPort and Azure Nanopore workflow
used by `nanopore-cloud-gui`.

## Scope

The GUI is a local operator client. FoodPort remains the system of record for runs, file manifests, orchestration, processing status, and results. The GUI must never receive Azure account keys or store long-lived SAS credentials.

## Current implementation status

The Nanopore runtime baseline is complete and accepted. Image
`development/nanopore/0.0.13` passed an immutable GPU acceptance run
on `Standard_NV18ads_A10_v5`, including Dorado basecalling, demultiplexing,
mapping, sorted/indexed BAM output, expected-target detection, durable state
and status files, and duplicate processing prevention. The accepted evidence
is retained in
`infrastructure/nanopore-image/docs/acceptance-results/0.0.13`.

The run processed 1 POD5 file as 1 one-file scheduler batch, summarized
80,356 reads, retained 4 FASTQ files, produced 4 mapping result CSV files
and 4 mapping BAM/BAI pairs, and completed a second scheduler pass without
reprocessing any input. The acceptance validation found the expected 1 read
for barcode 22 mapped to `gntK`.

The FoodPort backend slice is implemented in the repository and covered by
focused tests:

- authenticated run, file, manifest, and processing-batch model boundaries;
- idempotent prepare, complete, and finalize behavior;
- direct Blob upload using short-lived SAS URLs;
- immutable-manifest creation and one-time Batch submission;
- transient GPU-capacity retry handling with bounded exponential backoff; and
- Batch task polling and short-lived result-manifest URLs.

The implementation is complete through the repository integration boundary.
Remaining work is deployment and operational verification rather than GPU
runtime development:

- execute a real authenticated API-to-Batch-to-publication workflow;
- verify durable result publication before marking a run complete;
- verify deployed migrations, permissions, Celery polling, and CI coverage;
- finalize least-privilege Azure identity/SAS handling, retention, audit, and
  monitoring; and
- add retry, cancel, artifact-listing, and event endpoints if the GUI requires
  them.

## API Contract

The GUI currently expects these operations:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/auth/token/` | Obtain a FoodPort token. |
| `POST` | `/nanopore/runs/` | Create a Nanopore run. |
| `GET` | `/nanopore/runs/{run_id}/` | Return run and processing status. |
| `POST` | `/nanopore/runs/{run_id}/files/prepare/` | Register a file and return a short-lived upload URL. |
| `PUT` | SAS URL | Upload bytes directly to Azure Blob Storage. |
| `POST` | `/nanopore/runs/{run_id}/files/{file_id}/complete/` | Confirm that the blob upload completed. |
| `POST` | `/nanopore/runs/{run_id}/finalize/` | Stop intake and request graceful processing termination. |

Optional follow-up operations are `retry`, `cancel`, artifact `results`, and
`events` endpoints for richer operator control. The current GUI contract uses
the status endpoint and the latest-result URL.

### Create run

Request:

```json
{
  "run_name": "260825-nanopore",
  "barcode_kit": "SQK-RBK114-24",
  "barcode_values": [1],
  "sample_metadata": {
    "samples": [
      {"barcode": 1, "seqid": "2026-MIN-0001", "olnid": "OLN-1"}
    ]
  }
}
```

The response should include at least:

```json
{
  "run_id": 42,
  "run_name": "260825-nanopore",
  "workflow_state": "uploading"
}
```

The server may also return instrument, model, panel, or upload-prefix metadata as those fields become available in the GUI.

### Prepare file

Request:

```json
{
  "relative_path": "pass/sample.pod5",
  "size_bytes": 1048576
}
```

Response:

```json
{
  "file_id": 9,
  "upload_url": "https://storage.example/...short-lived-SAS..."
}
```

The operation must be idempotent for `(run_id, relative_path)`. If the same file is prepared again, return the existing file record or safely replace an incomplete upload URL. Never create duplicate manifest entries.

### Complete file

Completion must verify the blob exists in the expected container/prefix and record its size and identity. The endpoint should be idempotent and safe to repeat after a client timeout.

## Data Model

Add Nanopore-specific models rather than putting all fields on a generic sequencing model.

### `NanoporeRun`

- `sequencing_run` or FoodPort run reference
- `instrument_name`
- `instrument_type`
- `source_directory`
- `dorado_model`
- `reference_panel`
- `status`
- `started_at`
- `last_file_seen_at`
- `finalized_at`
- `created_by`

### `NanoporeFile`

- run reference
- `relative_path`
- `blob_name`
- `size_bytes`
- `sha256` (recommended)
- `first_seen_at`
- `stable_at`
- `upload_started_at`
- `uploaded_at`
- `status`
- `attempt_count`
- `last_error`
- unique constraint on `(run, relative_path)`

### `NanoporeProcessingBatch`

- run reference
- immutable input manifest reference
- Azure Batch job and task IDs
- `status`
- `submitted_at`
- `started_at`
- `finished_at`
- `exit_code`
- `error`

## State and Idempotency

Suggested run states:

```text
created -> uploading -> processing -> stopping -> complete
                         |                         |
                         +-> failed                +-> failed
```

Suggested file states:

```text
pending -> uploading -> uploaded -> included
             |             |
             +-> failed    +-> failed
```

Required rules:

1. A file is identified by run plus normalized relative path.
2. Retries must not duplicate file records or processing inputs.
3. Each completed file must become eligible for the next processing batch without waiting for termination.
4. Each processing batch must have a durable input manifest and published report.
5. Termination must stop new intake, prevent new batches, and signal the Batch VM/task to shut down.
6. Repeated termination calls must be idempotent.
7. API requests should support an idempotency key for run creation and termination operations where practical.

## Azure Storage

- Use a managed identity or server-side service principal to create SAS URLs.
- Scope each SAS to one blob, with write/create permissions only, and a short expiry.
- Keep the storage account key and service principal credentials server-side.
- Validate the expected container and blob prefix before accepting completion.
- Configure CORS only for the required workstation origins and headers.
- Apply lifecycle and retention rules appropriate for raw POD5 data.
- Record blob size, ETag, checksum, and upload completion time.

## Azure Batch Processing

When the first file is completed:

1. Start or wake the required Azure Batch task.
2. Add completed files to bounded processing batches and publish each new
  generation to the active task.
3. Run Dorado basecalling, minimap2 alignment, samtools processing, target detection, and report generation.
4. Publish each batch report and update `NanoporeProcessingBatch` status.
5. Continue polling for later generations while the run remains open.
6. On termination, stop new batch creation, finish or cancel active work according to policy, and shut down the VM/task.
7. Capture stdout, stderr, exit code, tool versions, reference versions, and container/image digest for reproducibility.
8. Make batch creation and termination idempotent.

The wrapper owns the complete run lifecycle: it polls immutable manifest
generations, invokes the scheduler sequentially, publishes each generation
before advancing, and retains the cumulative working directory for the
lifetime of one Azure task. The poller must not submit a new task for each
generation. When that task exits successfully after finalization, the active
task, job, and pool are cleaned up together.

The processing implementation should define explicit failure and retry policies. A failed task must be restartable without reprocessing a different manifest.

For transient Azure GPU capacity failures, the FoodPort Celery submission task
retries the same immutable manifest up to 8 times by default. The delay starts
at 60 seconds, doubles after each failed attempt, and is capped at 15 minutes.
These defaults can be changed with `NANOPORE_SUBMISSION_MAX_RETRIES`,
`NANOPORE_SUBMISSION_RETRY_DELAY`, and
`NANOPORE_SUBMISSION_MAX_RETRY_DELAY`. The AzureBatch service classifies
`AllocationFailed` and insufficient-capacity errors as retryable `503`
responses; it does not own the Celery retry schedule.

## Security and Operations

- Enforce FoodPort authentication and authorization on every run/file endpoint.
- Verify that a user can access the requested run before issuing a SAS URL.
- Do not put tokens, SAS URLs, or account credentials in application logs.
- Rate-limit file preparation and protect against path traversal. Reject absolute paths and `..` segments.
- Validate file sizes and allowed extensions.
- Audit run creation, file completion, finalization, retry, cancellation, and result access.
- Add metrics for prepared files, completed files, failed files, bytes uploaded, finalize latency, Batch duration, and processing failures.

## Verified Runtime Acceptance

The accepted `0.0.13` run processed one POD5 file as one scheduler batch.
The scheduler completed a second pass without duplicate processing and
produced four demultiplexed result sets and four mapping BAM/BAI pairs, and
found the expected one `barcode22`/`gntK` read. Evidence is retained under
`infrastructure/nanopore-image/docs/acceptance-results/0.0.13`.

## Production Integration Acceptance Criteria

- A GUI-created run is visible in FoodPort without manual portal setup.
- Repeating prepare, complete, or finalize after a timeout is safe and idempotent.
- A 50 GB run can upload concurrently without exposing Azure account credentials.
- A finalized run has an immutable manifest and exactly one processing submission.
- The processing job can be retried from that manifest.
- The GUI can display current run state, upload failures, processing state, and result links.
- API, model, permission, idempotency, and Batch integration tests run in CI.
